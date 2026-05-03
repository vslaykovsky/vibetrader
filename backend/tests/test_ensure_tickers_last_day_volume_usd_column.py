from sqlalchemy import create_engine, inspect, text

from db.session import ensure_tickers_last_day_volume_usd_column


def test_ensure_tickers_last_day_volume_usd_column():
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE tickers ("
                "ticker VARCHAR(32) NOT NULL, "
                "provider VARCHAR(16) NOT NULL, "
                "tags JSON NOT NULL, "
                "updated_at DATETIME NOT NULL, "
                "PRIMARY KEY (ticker, provider)"
                ")"
            )
        )
    ensure_tickers_last_day_volume_usd_column(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("tickers")}
    assert "last_day_volume_usd" in cols
    ensure_tickers_last_day_volume_usd_column(eng)
