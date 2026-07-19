import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from application.services.strategy_runtime import StrategyRuntime, StrategyRuntimeError
from application.services.rust_build import build_rust_binary
from strategies_v2.utils import (
    InputOhlcDataPoint,
    Ohlc,
    StrategyInput,
    StrategyOutput,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def test_strategy_runtime_echo_startup_and_time_ack():
    rt = StrategyRuntime(FIXTURES_DIR, entry_script="echo_strategy.py")
    try:
        startup = rt.start()
        assert isinstance(startup, StrategyOutput)
        kinds = [p.kind for p in startup.root]
        assert "ticker_subscription" in kinds

        step = StrategyInput(
            unixtime=1_700_000_000,
            points=[
                InputOhlcDataPoint(
                    ticker="TEST",
                    ohlc=Ohlc(open=1.0, high=2.0, low=0.5, close=1.5, volume=0.0),
                ),
            ],
        )
        resp = rt.send(step)
        acks = [p for p in resp.root if p.kind == "time_ack"]
        assert len(acks) == 1
        assert acks[0].unixtime == 1_700_000_000
    finally:
        rt.close()

    rt = StrategyRuntime(FIXTURES_DIR, entry_script="bad_time_ack_strategy.py")
    try:
        rt.start()
        step = StrategyInput(
            unixtime=1_700_000_000,
            points=[
                InputOhlcDataPoint(
                    ticker="TEST",
                    ohlc=Ohlc(open=1.0, high=2.0, low=0.5, close=1.5, volume=0.0),
                ),
            ],
        )
        with pytest.raises(StrategyRuntimeError, match="Expected time_ack unixtime=1700000000"):
            rt.send(step)
    finally:
        rt.close()


def test_strategy_runtime_missing_script():
    rt = StrategyRuntime(FIXTURES_DIR, entry_script="nonexistent_strategy.py")
    with pytest.raises(StrategyRuntimeError, match="not found"):
        rt.start()


def test_strategy_runtime_drain_stdout_collects_split_startup_lines():
    rt = StrategyRuntime(FIXTURES_DIR, entry_script="split_startup_strategy.py")
    try:
        startup = rt.start()
        extra = rt.drain_stdout(timeout_seconds=1.0)
        combined = StrategyOutput([*startup.root, *[p for output in extra for p in output.root]])
        assert [p.kind for p in combined.root] == [
            "ticker_subscription",
            "indicator_series_catalog",
        ]
        step = StrategyInput(
            unixtime=1_700_000_000,
            points=[
                InputOhlcDataPoint(
                    ticker="TEST",
                    ohlc=Ohlc(open=1.0, high=2.0, low=0.5, close=1.5, volume=0.0),
                ),
            ],
        )
        resp = rt.send(step)
        acks = [p for p in resp.root if p.kind == "time_ack"]
        assert len(acks) == 1
        assert acks[0].unixtime == 1_700_000_000
    finally:
        rt.close()


def test_strategy_runtime_invalid_extra_stdout_fails_without_waiting_for_exit():
    rt = StrategyRuntime(FIXTURES_DIR, entry_script="invalid_extra_stdout_strategy.py")
    try:
        rt.start()
        started = time.monotonic()
        with pytest.raises(StrategyRuntimeError, match="Invalid stdout JSON"):
            rt.drain_stdout(timeout_seconds=1.0)
        assert time.monotonic() - started < 2.0
    finally:
        rt.close()


def test_strategy_runtime_finalize_collects_eda_chart_after_eof():
    rt = StrategyRuntime(FIXTURES_DIR, entry_script="eda_finalize_strategy.py")
    try:
        rt.start()
        step = StrategyInput(
            unixtime=1_700_000_000,
            points=[
                InputOhlcDataPoint(
                    ticker="TEST",
                    ohlc=Ohlc(open=1.0, high=2.0, low=0.5, close=1.5, volume=0.0),
                ),
            ],
        )
        rt.send(step)
        final = rt.finalize(timeout_seconds=10.0)
        kinds = [p.kind for p in final.root]
        assert kinds == ["chart"]
        chart = final.root[0].chart
        assert chart.type == "lightweight-charts"
        assert chart.title == "Close series"
        assert len(chart.series) == 1
        series = chart.series[0]
        assert series.label == "close"
        assert [p.value for p in series.data] == [1.5]
    finally:
        rt.close()


def test_strategy_runtime_start_does_not_write_stdin_before_subscriptions():
    rt = StrategyRuntime(FIXTURES_DIR, entry_script="echo_strategy.py")
    try:
        startup = rt.start()
        assert isinstance(startup, StrategyOutput)
        kinds = [p.kind for p in startup.root]
        assert "ticker_subscription" in kinds
        assert rt.recorded_inputs == []
        step = StrategyInput(
            unixtime=1_700_000_000,
            points=[
                InputOhlcDataPoint(
                    ticker="TEST",
                    ohlc=Ohlc(open=1.0, high=2.0, low=0.5, close=1.5, volume=0.0),
                ),
            ],
        )
        resp = rt.send(step)
        acks = [p for p in resp.root if p.kind == "time_ack"]
        assert len(acks) == 1
        assert acks[0].unixtime == 1_700_000_000
    finally:
        rt.close()


def test_strategy_runtime_msgpack_framing():
    rt = StrategyRuntime(
        FIXTURES_DIR,
        entry_script="echo_msgpack_strategy.py",
        transport="msgpack",
    )
    try:
        startup = rt.start()
        assert [point.kind for point in startup.root] == ["ticker_subscription"]
        step = StrategyInput(
            unixtime=1_700_000_000,
            points=[
                InputOhlcDataPoint(
                    ticker="TEST",
                    ohlc=Ohlc(open=1.0, high=2.0, low=0.5, close=1.5, volume=0.0),
                ),
            ],
        )
        response = rt.send(step)
        assert [point.kind for point in response.root] == ["time_ack"]
        assert response.root[0].unixtime == step.unixtime
    finally:
        rt.close()


@pytest.mark.skipif(shutil.which("cargo") is None, reason="Cargo is not installed")
def test_rust_template_compiles_and_runs_through_host(monkeypatch, tmp_path):
    template_dir = Path(__file__).resolve().parents[1] / "strategies_v2"
    for name in (
        "strategy.rs",
        "utils.rs",
        "worker.rs",
        "simulator.rs",
        "optimizer_runtime.rs",
        "portfolio.rs",
        "params.json",
    ):
        shutil.copy2(template_dir / name, tmp_path / name)
    crate_name = "vibetrader_strategy_runtime_test"
    for name in ("Cargo.toml", "Cargo.lock"):
        rendered = (template_dir / name).read_text(encoding="utf-8").replace(
            "{{CRATE_NAME}}", crate_name
        )
        (tmp_path / name).write_text(rendered, encoding="utf-8")
    monkeypatch.setenv("STRATEGY_RUST_TARGET_DIR", str(tmp_path / "target"))

    rt = StrategyRuntime(tmp_path, entry_script="strategy.rs")
    try:
        startup = rt.start()
        assert [point.kind for point in startup.root] == ["ticker_subscription"]
        response = rt.send(
            StrategyInput(
                unixtime=1_700_000_000,
                points=[
                    InputOhlcDataPoint(
                        id="price",
                        ticker="SPY",
                        ohlc=Ohlc(
                            open=1.0,
                            high=2.0,
                            low=0.5,
                            close=1.5,
                            volume=10.0,
                        ),
                    )
                ],
            )
        )
        assert [(point.kind, point.unixtime) for point in response.root] == [
            ("time_ack", 1_700_000_000)
        ]
    finally:
        rt.close()


@pytest.mark.skipif(shutil.which("cargo") is None, reason="Cargo is not installed")
def test_rust_simulator_runs_strategy_in_process(monkeypatch, tmp_path):
    template_dir = Path(__file__).resolve().parents[1] / "strategies_v2"
    for name in (
        "strategy.rs",
        "utils.rs",
        "worker.rs",
        "simulator.rs",
        "optimizer_runtime.rs",
        "portfolio.rs",
        "params.json",
    ):
        shutil.copy2(template_dir / name, tmp_path / name)
    crate_name = "vibetrader_simulator_integration_test"
    for name in ("Cargo.toml", "Cargo.lock"):
        rendered = (template_dir / name).read_text(encoding="utf-8").replace(
            "{{CRATE_NAME}}", crate_name
        )
        (tmp_path / name).write_text(rendered, encoding="utf-8")
    monkeypatch.setenv("STRATEGY_RUST_TARGET_DIR", str(tmp_path / "target"))
    monkeypatch.setenv(
        "VIBETRADER_RUST_DATA_ADAPTER",
        str(FIXTURES_DIR / "rust_data_adapter.py"),
    )
    monkeypatch.setenv("STRATEGY_PYTHON_EXECUTABLE", sys.executable)

    executable = build_rust_binary(tmp_path, "simulator.rs")
    completed = subprocess.run(
        [str(executable)], cwd=tmp_path, capture_output=True, text=True, timeout=60
    )

    assert completed.returncode == 0, completed.stderr
    metrics = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    backtest = json.loads((tmp_path / "backtest.json").read_text(encoding="utf-8"))
    assert metrics["num_trades"] == 0
    assert metrics["final_equity"] == 10_000.0
    assert backtest["strategy_name"] == "Rust integration"
    assert [chart["title"] for chart in backtest["charts"]][-2:] == [
        "Equity curve vs buy & hold",
        "Orders",
    ]
    assert '"event":"done"' in completed.stderr


@pytest.mark.skipif(shutil.which("cargo") is None, reason="Cargo is not installed")
def test_rust_optimizer_runs_trials_in_one_process(monkeypatch, tmp_path):
    template_dir = Path(__file__).resolve().parents[1] / "strategies_v2"
    for name in (
        "strategy.rs",
        "utils.rs",
        "simulator.rs",
        "optimizer.rs",
        "optimizer_runtime.rs",
        "portfolio.rs",
        "params.json",
    ):
        shutil.copy2(template_dir / name, tmp_path / name)
    params = json.loads((tmp_path / "params.json").read_text(encoding="utf-8"))
    params["unused_trial_value"] = 0.0
    (tmp_path / "params.json").write_text(json.dumps(params), encoding="utf-8")
    (tmp_path / "params-hyperopt.json").write_text(
        json.dumps(
            {
                "search_space": {
                    "unused_trial_value": {"type": "float", "low": 0.0, "high": 1.0}
                },
                "n_trials": 3,
                "seed": 1,
                "objective_metric": "total_return",
            }
        ),
        encoding="utf-8",
    )
    crate_name = "vibetrader_optimizer_integration_test"
    for name in ("Cargo.toml", "Cargo.lock"):
        rendered = (template_dir / name).read_text(encoding="utf-8").replace(
            "{{CRATE_NAME}}", crate_name
        )
        (tmp_path / name).write_text(rendered, encoding="utf-8")
    monkeypatch.setenv("STRATEGY_RUST_TARGET_DIR", str(tmp_path / "target"))
    monkeypatch.setenv(
        "VIBETRADER_RUST_DATA_ADAPTER",
        str(FIXTURES_DIR / "rust_data_adapter.py"),
    )
    monkeypatch.setenv("STRATEGY_PYTHON_EXECUTABLE", sys.executable)

    executable = build_rust_binary(tmp_path, "optimizer.rs")
    completed = subprocess.run(
        [str(executable)], cwd=tmp_path, capture_output=True, text=True, timeout=60
    )

    assert completed.returncode == 0, completed.stderr
    assert "over 3 successful trials" in completed.stdout
    assert '"event":"done"' in completed.stderr
    assert json.loads((tmp_path / "metrics.json").read_text())["final_equity"] == 10_000.0
    assert not list(tmp_path.glob(".rust-optimization-*"))


@pytest.mark.skipif(shutil.which("cargo") is None, reason="Cargo is not installed")
def test_rust_optimizer_runs_complete_walk_forward_study(monkeypatch, tmp_path):
    template_dir = Path(__file__).resolve().parents[1] / "strategies_v2"
    for name in (
        "strategy.rs",
        "utils.rs",
        "simulator.rs",
        "optimizer.rs",
        "optimizer_runtime.rs",
        "portfolio.rs",
        "params.json",
    ):
        shutil.copy2(template_dir / name, tmp_path / name)
    shutil.copy2(
        FIXTURES_DIR / "rust_sma_crossover_strategy.rs", tmp_path / "strategy.rs"
    )
    params = json.loads((tmp_path / "params.json").read_text(encoding="utf-8"))
    params.update(
        {
            "start_date": "2024-01-01",
            "end_date": "2024-01-04",
            "fast_sma_period": 1,
            "slow_sma_period": 2,
            "position_fraction": 1.0,
            "unused_trial_value": 0.0,
        }
    )
    original_params = json.dumps(params, indent=2) + "\n"
    (tmp_path / "params.json").write_text(original_params, encoding="utf-8")
    (tmp_path / "params-hyperopt.json").write_text(
        json.dumps(
            {
                "search_space": {
                    "unused_trial_value": {"type": "float", "low": 0.0, "high": 1.0}
                },
                "mode": "walk_forward",
                "n_trials": 2,
                "seed": 1,
                "objective_metric": "total_return",
                "walk_forward": {
                    "train_window_days": 1,
                    "test_window_days": 1,
                    "oos_total_days": 3,
                },
            }
        ),
        encoding="utf-8",
    )
    crate_name = "vibetrader_walk_forward_integration_test"
    for name in ("Cargo.toml", "Cargo.lock"):
        rendered = (template_dir / name).read_text(encoding="utf-8").replace(
            "{{CRATE_NAME}}", crate_name
        )
        (tmp_path / name).write_text(rendered, encoding="utf-8")
    monkeypatch.setenv("STRATEGY_RUST_TARGET_DIR", str(tmp_path / "target"))
    monkeypatch.setenv(
        "VIBETRADER_RUST_DATA_ADAPTER",
        str(FIXTURES_DIR / "rust_data_adapter.py"),
    )
    monkeypatch.setenv("STRATEGY_PYTHON_EXECUTABLE", sys.executable)

    executable = build_rust_binary(tmp_path, "optimizer.rs")
    completed = subprocess.run(
        [str(executable)], cwd=tmp_path, capture_output=True, text=True, timeout=60
    )

    assert completed.returncode == 0, completed.stderr
    assert "over 3 folds" in completed.stdout
    assert '"event":"walk_forward_done"' in completed.stderr
    assert (tmp_path / "params.json").read_text(encoding="utf-8") == original_params
    walkforward = json.loads((tmp_path / "walkforward.json").read_text())
    assert [fold["test_start"] for fold in walkforward["folds"]] == [
        "2024-01-02",
        "2024-01-03",
        "2024-01-04",
    ]
    assert walkforward["execution_model"] == "continuous_oos_portfolio"
    assert walkforward["metrics"]["final_equity"] == pytest.approx(10_000.0 * 99.0 / 103.0)
    assert walkforward["metrics"]["num_trades"] == 2
    assert walkforward["folds"][1]["ending_positions"][0]["ticker"] == "SPY"
    assert walkforward["folds"][1]["ending_positions"] == walkforward["folds"][2][
        "starting_positions"
    ]
    assert walkforward["folds"][2]["ending_positions"] == []
    backtest = json.loads((tmp_path / "backtest.json").read_text())
    assert backtest["strategy_name"].endswith("walk-forward OOS")
    assert backtest["charts"][0]["verticalMarkers"] == [
        {"time": "2024-01-02", "label": "OOS 1", "color": "#f59e0b"},
        {"time": "2024-01-03", "label": "OOS 2", "color": "#f59e0b"},
        {"time": "2024-01-04", "label": "OOS 3", "color": "#f59e0b"},
    ]
    orders = next(chart for chart in backtest["charts"] if chart["title"] == "Orders")
    assert [(row["direction"], row["time"]) for row in orders["rows"]] == [
        ("buy", 1_704_240_000),
        ("sell", 1_704_326_400),
    ]
    assert not list(tmp_path.glob(".rust-optimization-*"))
