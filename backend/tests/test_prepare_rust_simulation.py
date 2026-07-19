from __future__ import annotations

import json

import pandas as pd

from scripts.prepare_rust_simulation import StudyBarsQuery, prepare, prepare_study
from strategies_v2.utils import (
    OutputIndicatorSubscriptionOrder,
    OutputTickerSubscription,
    SmaIndicatorSubscription,
    StrategyOutput,
)


def test_prepare_builds_one_bulk_dataset_without_portfolio_points(monkeypatch, tmp_path):
    (tmp_path / "params.json").write_text(
        json.dumps(
            {
                "ticker": "SPY",
                "scale": "1d",
                "simulation_scale": "1d",
                "strategy_name": "Rust test",
                "start_date": "2024-01-02",
                "end_date": "2024-01-03",
                "initial_deposit": 10_000,
                "provider": "auto",
                "max_leverage": 1.0,
            }
        ),
        encoding="utf-8",
    )
    frame = pd.DataFrame(
        {
            "open": [100.0, 101.0],
            "high": [102.0, 103.0],
            "low": [99.0, 100.0],
            "close": [101.0, 102.0],
            "volume": [1_000.0, 1_100.0],
        },
        index=pd.to_datetime(["2024-01-02", "2024-01-03"], utc=True),
    )

    def fake_fetch(self, *args, **kwargs):
        return frame, False

    monkeypatch.setattr(
        "scripts.prepare_rust_simulation.HistoricalBarsQuery.fetch_chunked_merge",
        fake_fetch,
    )
    startup = StrategyOutput(
        [
            OutputTickerSubscription(
                id="price",
                ticker="SPY",
                scale="1d",
                session="all",
                partial=False,
            )
        ]
    )

    dataset = prepare(tmp_path, startup)

    assert dataset["primary_ticker"] == "SPY"
    assert dataset["total_units"] == 2
    assert len(dataset["events"]) == 2
    assert all(event["invoke_strategy"] for event in dataset["events"])
    assert all(event["base_close"] for event in dataset["events"])
    assert all(
        [point["kind"] for point in event["points"]] == ["ohlc"]
        for event in dataset["events"]
    )
    assert dataset["subscription_charts"][0]["title"] == "SPY price (1d)"


def test_prepare_study_fetches_maximum_warmup_once_and_reuses_slices(monkeypatch, tmp_path):
    (tmp_path / "params.json").write_text(
        json.dumps(
            {
                "ticker": "SPY",
                "scale": "1d",
                "simulation_scale": "1d",
                "strategy_name": "Rust optimization test",
                "start_date": "2024-02-01",
                "end_date": "2024-02-10",
                "initial_deposit": 10_000,
                "provider": "auto",
                "max_leverage": 1.0,
            }
        ),
        encoding="utf-8",
    )
    frame = pd.DataFrame(
        {
            "open": range(100, 200),
            "high": range(101, 201),
            "low": range(99, 199),
            "close": range(100, 200),
            "volume": [1_000.0] * 100,
        },
        index=pd.date_range("2023-11-01", periods=100, freq="1D", tz="UTC"),
    )
    calls = []

    def fake_fetch(self, *args, **kwargs):
        calls.append((args, kwargs))
        return frame, 1

    monkeypatch.setattr(
        "scripts.prepare_rust_simulation.HistoricalBarsQuery.fetch_chunked_merge",
        fake_fetch,
    )

    def startup(period: int) -> StrategyOutput:
        return StrategyOutput(
            [
                OutputTickerSubscription(
                    id="price",
                    ticker="SPY",
                    scale="1d",
                    session="all",
                    partial=False,
                ),
                OutputIndicatorSubscriptionOrder(
                    indicator=SmaIndicatorSubscription(
                        id="sma",
                        ticker="SPY",
                        scale="1d",
                        session="all",
                        period=period,
                        partial=False,
                    )
                ),
            ]
        )

    study = prepare_study(tmp_path, [startup(10), startup(20)])

    assert len(calls) == 1
    assert calls[0][1]["padding_days"] == 60
    assert study["max_padding_days"] == 60
    assert study["trial_windows"][0]["warmup_start"] > study["trial_windows"][1]["warmup_start"]

    cached = StudyBarsQuery(study)
    candidate, _ = cached.fetch_chunked_merge(
        "SPY",
        "1d",
        pd.Timestamp("2024-02-01").date(),
        pd.Timestamp("2024-02-10").date(),
        padding_days=30,
        provider="auto",
        session="all",
    )
    assert candidate.index.min() == pd.Timestamp("2024-01-02", tz="UTC")
