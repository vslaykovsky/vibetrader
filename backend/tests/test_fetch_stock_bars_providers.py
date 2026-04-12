import pandas as pd

from strategies import utils


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
        timeframe="1d",
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
        timeframe="1d",
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
        timeframe="1d",
        provider="auto",
    )

    assert called == {"alpaca": 1, "moex": 1}
    assert out.equals(expected)

