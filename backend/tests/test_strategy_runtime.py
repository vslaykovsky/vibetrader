from pathlib import Path

import pytest

from application.services.strategy_runtime import StrategyRuntime, StrategyRuntimeError
from strategies_v2.utils import (
    InputOhlcDataPoint,
    InputPortfolioDataPoint,
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


def test_strategy_runtime_missing_script():
    rt = StrategyRuntime(FIXTURES_DIR, entry_script="nonexistent_strategy.py")
    with pytest.raises(StrategyRuntimeError, match="not found"):
        rt.start()


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


def test_strategy_runtime_start_with_initial_portfolio_line():
    rt = StrategyRuntime(FIXTURES_DIR, entry_script="echo_strategy.py")
    try:
        startup = rt.start(
            initial_input=StrategyInput(
                unixtime=0,
                points=[InputPortfolioDataPoint(positions=[])],
            )
        )
        assert isinstance(startup, StrategyOutput)
        kinds = [p.kind for p in startup.root]
        assert "ticker_subscription" in kinds
    finally:
        rt.close()
