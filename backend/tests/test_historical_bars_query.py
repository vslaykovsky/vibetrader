import time
from datetime import date

import pandas as pd
import pytest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from application.queries.historical_bars import (
    HistoricalBarsQuery,
    _covers_expected_us_equity_sessions,
    infer_asset_class,
    scale_to_timeframe,
)
from db.models import Base, Ticker


def _tf_key(tf: TimeFrame) -> tuple:
    return (tf.amount, tf.unit)


def test_scale_to_timeframe_maps_common_scales():
    assert _tf_key(scale_to_timeframe("1m")) == _tf_key(TimeFrame.Minute)
    assert _tf_key(scale_to_timeframe("15M")) == (15, TimeFrameUnit.Minute)
    assert _tf_key(scale_to_timeframe("1h")) == _tf_key(TimeFrame.Hour)
    assert _tf_key(scale_to_timeframe("4h")) == (4, TimeFrameUnit.Hour)
    assert _tf_key(scale_to_timeframe("1d")) == _tf_key(TimeFrame.Day)
    assert _tf_key(scale_to_timeframe("1w")) == _tf_key(TimeFrame.Week)


def test_scale_to_timeframe_rejects_unknown():
    with pytest.raises(ValueError, match="Unsupported scale"):
        scale_to_timeframe("2h")


def test_infer_asset_class_detects_crypto_symbols():
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=eng)
    Session = sessionmaker(bind=eng, future=True)
    session = Session()
    try:
        session.add_all(
            [
                Ticker(ticker="BTC/USD", provider="alpaca", tags=["crypto"]),
                Ticker(ticker="AAPL", provider="alpaca", tags=["stock", "SNP500"]),
                Ticker(ticker="SBER", provider="moex", tags=["stock"]),
            ]
        )
        session.commit()

        assert infer_asset_class("BTCUSD", provider="alpaca", session=session) == "crypto"
        assert infer_asset_class("BTC/USD", provider="alpaca", session=session) == "crypto"
        assert infer_asset_class("AAPL", provider="alpaca", session=session) == "us_equity"
        assert infer_asset_class("SBER", provider="moex", session=session) == "us_equity"
        assert infer_asset_class("ETH/USD", provider="alpaca", session=session) is None
    finally:
        session.close()


def test_historical_bars_query_fetch_delegates(monkeypatch):
    expected = pd.DataFrame(
        {"open": [1.0], "high": [1.1], "low": [0.9], "close": [1.05], "volume": [10.0]},
        index=pd.to_datetime(["2024-01-02"]),
    )
    calls: list[dict] = []

    def fake_fetch_stock_bars(**kwargs):
        calls.append(kwargs)
        return expected

    monkeypatch.setattr(
        "application.queries.historical_bars.utils.fetch_stock_bars", fake_fetch_stock_bars
    )

    q = HistoricalBarsQuery()
    out = q.fetch(
        "SPY__TEST__NO_DB_HIT",
        "1d",
        date(2024, 1, 1),
        date(2024, 1, 31),
        padding_days=5,
        provider="alpaca",
    )

    assert out.equals(expected)
    assert len(calls) == 1
    c0 = calls[0]
    assert c0["ticker"] == "SPY__TEST__NO_DB_HIT"
    assert c0["start_test_date"] == "2024-01-01"
    assert c0["end_test_date"] == "2024-01-31"
    assert c0["history_padding_days"] == 5
    assert c0["provider"] == "alpaca"
    assert c0["session"] == "regular"
    assert c0["dividend_adjusted"] is False
    assert _tf_key(c0["timeframe"]) == _tf_key(TimeFrame.Day)


def test_historical_bars_query_cache_reuses_fetch(monkeypatch):
    expected = pd.DataFrame(
        {"open": [1.0], "high": [1.1], "low": [0.9], "close": [1.05], "volume": [10.0]},
        index=pd.to_datetime(["2024-01-02"]),
    )
    calls: list[int] = []

    def fake_fetch_stock_bars(**kwargs):
        calls.append(1)
        return expected

    monkeypatch.setattr(
        "application.queries.historical_bars.utils.fetch_stock_bars", fake_fetch_stock_bars
    )

    q = HistoricalBarsQuery(cache_ttl_seconds=600.0)
    a = q.fetch(
        "SPY__TEST__NO_DB_HIT",
        "1d",
        date(2024, 1, 1),
        date(2024, 1, 31),
        padding_days=5,
        provider="alpaca",
    )
    b = q.fetch(
        "SPY__TEST__NO_DB_HIT",
        "1d",
        date(2024, 1, 1),
        date(2024, 1, 31),
        padding_days=5,
        provider="alpaca",
    )
    assert a.equals(expected)
    assert b.equals(expected)
    assert len(calls) == 1


def test_daily_session_coverage_requires_every_trading_day():
    class _Row:
        def __init__(self, ts):
            self.timestamp = ts

    rows_through_july_2 = [
        _Row(pd.Timestamp(f"2026-{day}T14:00:00Z").to_pydatetime())
        for day in ("06-29", "06-30", "07-01", "07-02")
    ]
    assert (
        _covers_expected_us_equity_sessions(
            rows_through_july_2,
            start=date(2026, 6, 29),
            end=date(2026, 7, 6),
        )
        is False
    )

    rows_through_july_6 = [
        *rows_through_july_2,
        _Row(pd.Timestamp("2026-07-06T14:00:00Z").to_pydatetime()),
    ]
    assert (
        _covers_expected_us_equity_sessions(
            rows_through_july_6,
            start=date(2026, 6, 29),
            end=date(2026, 7, 6),
        )
        is True
    )


def test_daily_session_coverage_excludes_us_holiday_and_weekend():
    class _Row:
        def __init__(self, ts):
            self.timestamp = ts

    rows = [
        _Row(pd.Timestamp("2026-07-02T19:45:00Z").to_pydatetime()),
        _Row(pd.Timestamp("2026-07-06T13:30:00Z").to_pydatetime()),
    ]
    assert (
        _covers_expected_us_equity_sessions(
            rows,
            start=date(2026, 7, 2),
            end=date(2026, 7, 6),
        )
        is True
    )
