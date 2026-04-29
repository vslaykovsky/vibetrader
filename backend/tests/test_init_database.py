from sqlalchemy import create_engine, inspect

from db.session import init_database


def test_init_database():
    eng = create_engine("sqlite:///:memory:")
    init_database(eng)
    names = set(inspect(eng).get_table_names())
    assert names >= {
        "strategy",
        "candles",
        "alpaca_live_subscriptions",
        "alpaca_live_events",
        "live_runs",
        "live_run_events",
        "live_run_orders",
    }
    init_database(eng)
