from sqlalchemy import create_engine, inspect, text

from db.session import ensure_live_runs_alpaca_account_id_column


def test_ensure_live_runs_alpaca_account_id_column():
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE live_runs (id VARCHAR(36) PRIMARY KEY)"))
    ensure_live_runs_alpaca_account_id_column(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("live_runs")}
    assert "alpaca_account_id" in cols
    ensure_live_runs_alpaca_account_id_column(eng)
