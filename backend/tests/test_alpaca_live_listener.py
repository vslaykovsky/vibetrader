from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Ticker
from scripts.alpaca_live_listener import _split_symbols_by_asset, _symbols_match


def test_split_symbols_by_asset_uses_ticker_tags():
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=eng)
    Session = sessionmaker(bind=eng, future=True)
    session = Session()
    try:
        session.add_all(
            [
                Ticker(ticker="BTC/USD", provider="alpaca", tags=["crypto"]),
                Ticker(ticker="BTC/USDT", provider="alpaca", tags=["crypto"]),
                Ticker(ticker="SPY", provider="alpaca", tags=["stock"]),
            ]
        )
        session.commit()

        stocks, cryptos = _split_symbols_by_asset(["BTCUSD", "BTC/USDT", "SPY"], session=session)

        assert stocks == ["SPY"]
        assert cryptos == ["BTC/USD", "BTC/USDT"]
        assert _symbols_match("BTCUSD", "BTC/USD") is True
    finally:
        session.close()
