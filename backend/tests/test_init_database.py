from sqlalchemy import create_engine, inspect

from db.session import init_database


def test_init_database():
    eng = create_engine("sqlite:///:memory:")
    init_database(eng)
    names = set(inspect(eng).get_table_names())
    insp = inspect(eng)
    assert names >= {
        "strategy",
        "candles",
        "tickers",
        "alpaca_live_subscriptions",
        "alpaca_live_events",
        "live_runs",
        "live_run_events",
        "live_run_orders",
    }
    subscription_cols = {c["name"] for c in insp.get_columns("alpaca_live_subscriptions")}
    live_event_cols = {c["name"] for c in insp.get_columns("live_run_events")}
    live_order_cols = {c["name"] for c in insp.get_columns("live_run_orders")}
    assert "run_id" in subscription_cols
    assert "event_type" in live_event_cols
    assert "position_before_order" in live_order_cols
    assert "position_after_order_filled" in live_order_cols
    init_database(eng)
