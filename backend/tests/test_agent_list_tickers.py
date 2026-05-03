from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Ticker
from services.agent import _execute_ticker_listing_sql, _ticker_sql_prompt_vocabulary


def test_ticker_sql_prompt_vocabulary_matches_sync_tickers():
    providers, tags = _ticker_sql_prompt_vocabulary()

    assert providers == ["alpaca", "moex"]
    assert tags == ["SNP500", "crypto", "stock"]


def test_execute_ticker_listing_sql_returns_limited_tickers():
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=eng)
    Session = sessionmaker(bind=eng, future=True)
    session = Session()
    try:
        session.add_all(
            [
                Ticker(
                    ticker="AAPL",
                    provider="alpaca",
                    tags=["SNP500"],
                    last_daily_volume=250.0,
                ),
                Ticker(
                    ticker="MSFT",
                    provider="alpaca",
                    tags=["SNP500"],
                    last_daily_volume=200.0,
                ),
                Ticker(
                    ticker="SBER",
                    provider="moex",
                    tags=[],
                    last_daily_volume=500.0,
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    sql = (
        "SELECT ticker, provider, tags, last_daily_volume "
        "FROM tickers "
        "WHERE provider = 'alpaca' "
        "ORDER BY last_daily_volume IS NULL, last_daily_volume DESC"
    )
    result = _execute_ticker_listing_sql(sql, limit=2, session_factory=Session)

    assert result == {
        "ok": True,
        "sql": sql,
        "row_count": 2,
        "rows": [
            {
                "ticker": "AAPL",
                "provider": "alpaca",
                "tags": ["SNP500"],
                "last_daily_volume": 250.0,
            },
            {
                "ticker": "MSFT",
                "provider": "alpaca",
                "tags": ["SNP500"],
                "last_daily_volume": 200.0,
            },
        ],
        "tickers": ["AAPL", "MSFT"],
    }
