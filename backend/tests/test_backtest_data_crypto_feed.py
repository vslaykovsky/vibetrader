import pytest
from alpaca.data.models import BarSet

from application.services import backtest_data


def test_alpaca_crypto_feed_locs_defaults_and_validates_env(monkeypatch):
    monkeypatch.delenv("ALPACA_CRYPTO_FEED_LOC", raising=False)
    monkeypatch.delenv("ALPACA_CRYPTO_LOC", raising=False)
    assert backtest_data._alpaca_crypto_feed_locs() == ("us-1", "us")

    monkeypatch.setenv("ALPACA_CRYPTO_FEED_LOC", "eu-1,us,us")
    assert backtest_data._alpaca_crypto_feed_locs() == ("eu-1", "us")

    monkeypatch.setenv("ALPACA_CRYPTO_FEED_LOC", "global")
    with pytest.raises(RuntimeError, match="ALPACA_CRYPTO_FEED_LOC"):
        backtest_data._alpaca_crypto_feed_locs()


def test_alpaca_crypto_barset_to_ohlcv_returns_empty_frame_for_empty_response():
    df = backtest_data._alpaca_crypto_barset_to_ohlcv(BarSet({}), "BTC/USD")

    assert df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
