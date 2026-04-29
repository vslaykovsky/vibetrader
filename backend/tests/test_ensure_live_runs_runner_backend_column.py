from sqlalchemy import create_engine, inspect, text

from db.session import ensure_live_runs_runner_backend_column


def test_ensure_live_runs_runner_backend_column():
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE live_runs (id VARCHAR(36) PRIMARY KEY)"))
    ensure_live_runs_runner_backend_column(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("live_runs")}
    assert "runner_backend" in cols
    ensure_live_runs_runner_backend_column(eng)
