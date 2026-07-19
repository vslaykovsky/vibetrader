from datetime import date
import json
from types import SimpleNamespace

import pandas as pd
import scripts.simulate_strategy_v2 as simulator_script

from scripts.simulate_strategy_v2 import (
    _build_position_value_chart,
    _build_subscription_charts,
)
from application.services import backtest_data as backtest_utils
from strategies_v2.utils import RenkoIndicatorSubscription


def test_build_subscription_charts_handles_atr_renko_bricks():
    idx = pd.date_range("2024-01-01", periods=2, freq="1D", tz="UTC")
    base_df = pd.DataFrame(
        {
            "open": [100.0, 102.0],
            "high": [103.0, 104.0],
            "low": [99.0, 101.0],
            "close": [102.0, 103.0],
            "volume": [1.0, 1.0],
        },
        index=idx,
    )
    brick_time = int(pd.Timestamp("2024-01-01", tz="UTC").timestamp())
    marker = backtest_utils.LwcMarker(
        time="2024-01-01",
        position="belowBar",
        color="#26a69a",
        shape="arrowUp",
        text="BUY",
    )
    charts = _build_subscription_charts(
        tickers=["X"],
        base_scale="1d",
        per_base_df={"X": base_df},
        per_engine={},
        per_engine_ind_subs={},
        primary_ticker="X",
        start_d=date(2024, 1, 1),
        end_d=date(2024, 1, 2),
        markers={"X": [marker]},
        output_indicator_points={},
        renko_specs=[
            RenkoIndicatorSubscription(
                id="renko",
                ticker="X",
                scale="1d",
                brick_size_mode="atr",
                atr_period=2,
                atr_multiplier=1.5,
            )
        ],
        renko_bricks={"renko": [(brick_time, 100.0, 102.5, "up", 2.5)]},
    )

    assert [chart.title for chart in charts] == [
        "X price (1d)",
        "X renko bricks (ATR 2 x 1.5, scale=1d)",
    ]
    assert charts[1].series[0].data[0].open == 100.0
    assert charts[1].series[0].data[0].close == 102.5
    assert charts[0].series[0].markers == [marker]


def test_build_position_value_chart_uses_one_line_per_ticker():
    chart = _build_position_value_chart(
        {
            "MSFT": [
                backtest_utils.LwcTimeValuePoint(time="2024-01-01", value=0.0),
                backtest_utils.LwcTimeValuePoint(time="2024-01-02", value=-2500.0),
            ],
            "AAPL": [
                backtest_utils.LwcTimeValuePoint(time="2024-01-01", value=1000.0),
                backtest_utils.LwcTimeValuePoint(time="2024-01-02", value=1200.0),
            ],
            "EMPTY": [],
        }
    )

    assert chart is not None
    assert chart.title == "Current position value"
    assert [series.label for series in chart.series] == [
        "AAPL position value",
        "MSFT position value",
    ]
    assert [point.value for point in chart.series[0].data] == [1000.0, 1200.0]
    assert [point.value for point in chart.series[1].data] == [0.0, -2500.0]
    assert (
        _build_position_value_chart(
            {
                "AAPL": [
                    backtest_utils.LwcTimeValuePoint(time="2024-01-01", value=1000.0),
                    backtest_utils.LwcTimeValuePoint(time="2024-01-02", value=1200.0),
                ]
            }
        )
        is None
    )
    assert _build_position_value_chart({}) is None


def test_rust_entry_dispatches_release_simulator_binary(monkeypatch, tmp_path):
    entry = tmp_path / "strategy.rs"
    entry.write_text("// generated strategy", encoding="utf-8")
    (tmp_path / "params.json").write_text(
        json.dumps(
            {
                "start_date": "2024-01-01",
                "end_date": "2024-01-02",
                "initial_deposit": 10_000,
            }
        ),
        encoding="utf-8",
    )
    executable = tmp_path / "target" / "release" / "strategy_simulator"
    calls = []
    monkeypatch.setattr(
        simulator_script,
        "build_rust_binary",
        lambda workspace, source: (
            calls.append((workspace, source)) or executable
        ),
    )

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(simulator_script.subprocess, "run", fake_run)

    assert simulator_script.main(["--entry", str(entry)]) == 0
    assert calls[0] == (tmp_path, "simulator.rs")
    command, options = calls[1]
    assert command == [str(executable)]
    assert options["cwd"] == str(tmp_path)
    assert options["env"]["STRATEGY_PYTHON_EXECUTABLE"]
    assert options["env"]["VIBETRADER_RUST_DATA_ADAPTER"].endswith(
        "prepare_rust_simulation.py"
    )


def test_rust_entry_can_run_simulator_under_perf(monkeypatch, tmp_path):
    entry = tmp_path / "strategy.rs"
    entry.write_text("// generated strategy", encoding="utf-8")
    (tmp_path / "params.json").write_text(
        json.dumps(
            {
                "start_date": "2024-01-01",
                "end_date": "2024-01-02",
                "initial_deposit": 10_000,
            }
        ),
        encoding="utf-8",
    )
    executable = tmp_path / "target" / "release" / "strategy_simulator"
    monkeypatch.setattr(
        simulator_script,
        "build_rust_binary",
        lambda workspace, source: executable,
    )
    monkeypatch.setattr(
        simulator_script.shutil,
        "which",
        lambda command: "/usr/bin/perf" if command == "perf" else None,
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(simulator_script.subprocess, "run", fake_run)

    assert simulator_script.main(["--entry", str(entry), "--perf"]) == 0
    command, options = calls[0]
    assert command == [
        "/usr/bin/perf",
        "record",
        "--call-graph",
        "dwarf",
        "--output",
        str(tmp_path / "perf.data"),
        "--",
        str(executable),
    ]
    assert options["cwd"] == str(tmp_path)


def test_rust_entry_dispatches_optimizer_binary(monkeypatch, tmp_path):
    entry = tmp_path / "strategy.rs"
    entry.write_text("// generated strategy", encoding="utf-8")
    (tmp_path / "params.json").write_text(
        json.dumps(
            {
                "start_date": "2024-01-01",
                "end_date": "2024-01-02",
                "initial_deposit": 10_000,
            }
        ),
        encoding="utf-8",
    )
    executable = tmp_path / "target" / "release" / "strategy_optimizer"
    calls = []
    monkeypatch.setattr(
        simulator_script,
        "build_rust_binary",
        lambda workspace, source: calls.append((workspace, source)) or executable,
    )

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(simulator_script.subprocess, "run", fake_run)

    assert simulator_script.main(["--entry", str(entry), "--optimize"]) == 0
    assert calls[0] == (tmp_path, "optimizer.rs")
    assert calls[1][0] == [str(executable)]
