import pandas as pd
from alpaca.data.timeframe import TimeFrame

from application.services import backtest_data as utils


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [1.0],
            "high": [2.0],
            "low": [0.5],
            "close": [1.5],
            "volume": [100],
        },
        index=pd.to_datetime(["2024-01-01"]),
    )


def test_fetch_stock_bars_uses_alpaca_provider(monkeypatch):
    called = {"alpaca": 0, "moex": 0}
    expected = _sample_df()

    def _alpaca(**kwargs):
        called["alpaca"] += 1
        return expected

    def _moex(**kwargs):
        called["moex"] += 1
        return expected

    monkeypatch.setattr(utils, "_fetch_alpaca_bars", _alpaca)
    monkeypatch.setattr(utils, "_fetch_moex_bars", _moex)

    out = utils.fetch_stock_bars(
        ticker="AAPL",
        start_test_date="2024-01-01",
        end_test_date="2024-01-10",
        history_padding_days=0,
        timeframe=TimeFrame.Day,
        provider="alpaca",
    )

    assert called == {"alpaca": 1, "moex": 0}
    assert out.equals(expected)


def test_fetch_stock_bars_uses_moex_provider(monkeypatch):
    called = {"alpaca": 0, "moex": 0}
    expected = _sample_df()

    def _alpaca(**kwargs):
        called["alpaca"] += 1
        return expected

    def _moex(**kwargs):
        called["moex"] += 1
        return expected

    monkeypatch.setattr(utils, "_fetch_alpaca_bars", _alpaca)
    monkeypatch.setattr(utils, "_fetch_moex_bars", _moex)

    out = utils.fetch_stock_bars(
        ticker="SBER",
        start_test_date="2024-01-01",
        end_test_date="2024-01-10",
        history_padding_days=0,
        timeframe=TimeFrame.Day,
        provider="moex",
    )

    assert called == {"alpaca": 0, "moex": 1}
    assert out.equals(expected)


def test_fetch_stock_bars_auto_fallback_to_moex(monkeypatch):
    called = {"alpaca": 0, "moex": 0}
    expected = _sample_df()

    def _alpaca(**kwargs):
        called["alpaca"] += 1
        raise RuntimeError("alpaca failed")

    def _moex(**kwargs):
        called["moex"] += 1
        return expected

    monkeypatch.setattr(utils, "_fetch_alpaca_bars", _alpaca)
    monkeypatch.setattr(utils, "_fetch_moex_bars", _moex)

    out = utils.fetch_stock_bars(
        ticker="SBER",
        start_test_date="2024-01-01",
        end_test_date="2024-01-10",
        history_padding_days=0,
        timeframe=TimeFrame.Day,
        provider="auto",
    )

    assert called == {"alpaca": 1, "moex": 1}
    assert out.equals(expected)


def test_regular_hourly_bars_align_to_session_open():
    idx = pd.date_range("2024-01-02 14:30", periods=26, freq="15min", tz="UTC")
    df = pd.DataFrame(
        {
            "open": [float(i) for i in range(26)],
            "high": [float(i) + 0.5 for i in range(26)],
            "low": [float(i) - 0.5 for i in range(26)],
            "close": [float(i) + 0.25 for i in range(26)],
            "volume": [1.0] * 26,
        },
        index=idx,
    )

    out = utils._regular_hourly_bars_from_intraday(df)

    assert [ts.strftime("%H:%M") for ts in out.index] == [
        "14:30",
        "15:30",
        "16:30",
        "17:30",
        "18:30",
        "19:30",
        "20:30",
    ]
    assert list(out["volume"]) == [4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 2.0]
    assert out.iloc[-1]["close"] == 25.25

