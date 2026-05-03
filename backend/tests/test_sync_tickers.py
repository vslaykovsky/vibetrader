from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Candle, CandleTimeframe, Ticker
from scripts.sync_tickers import TickerRecord, _sync_tickers


def test_sync_tickers_sets_tags_and_latest_daily_volume():
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=eng)
    Session = sessionmaker(bind=eng, future=True)
    session = Session()
    try:
        session.add_all(
            [
                Candle(
                    ticker="AAPL",
                    timeframe=CandleTimeframe.D1,
                    timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    open=1.0,
                    high=2.0,
                    low=0.5,
                    close=1.5,
                    volume=100.0,
                ),
                Candle(
                    ticker="AAPL",
                    timeframe=CandleTimeframe.D1,
                    timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
                    open=2.0,
                    high=3.0,
                    low=1.5,
                    close=2.5,
                    volume=250.0,
                ),
            ]
        )
        session.commit()

        _sync_tickers(
            session,
            [
                TickerRecord(ticker="AAPL", provider="alpaca", tags=("stock",)),
                TickerRecord(ticker="BTC/USD", provider="alpaca", tags=("crypto",)),
                TickerRecord(ticker="SBER", provider="moex", tags=("stock",)),
            ],
            {"AAPL"},
            updated_at=datetime(2024, 2, 1, tzinfo=timezone.utc),
        )

        rows = {
            (row.ticker, row.provider): row
            for row in session.query(Ticker).order_by(Ticker.provider, Ticker.ticker).all()
        }
    finally:
        session.close()

    assert set(rows) == {("AAPL", "alpaca"), ("BTC/USD", "alpaca"), ("SBER", "moex")}
    assert rows[("AAPL", "alpaca")].tags == ["stock", "SNP500"]
    assert rows[("AAPL", "alpaca")].last_daily_volume == 250.0
    assert rows[("BTC/USD", "alpaca")].tags == ["crypto"]
    assert rows[("BTC/USD", "alpaca")].last_daily_volume is None
    assert rows[("SBER", "moex")].tags == ["stock"]
