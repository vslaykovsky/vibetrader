from sqlalchemy import create_engine, inspect, text

from db.session import ensure_strategy_langsmith_trace_column


def test_ensure_strategy_langsmith_trace_column():
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE strategy (id VARCHAR(36) PRIMARY KEY)"))
    ensure_strategy_langsmith_trace_column(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("strategy")}
    assert "langsmith_trace" in cols
    ensure_strategy_langsmith_trace_column(eng)
