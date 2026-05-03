from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from application.services.alpaca_live_db import (
    LiveSubscriptionSpec,
    read_active_subscriptions,
    read_run_market_events_after,
    read_run_strategy_inputs_after,
    upsert_runner_subscriptions,
)
from db.models import LiveRun, LiveRunEvent
from db.session import init_database


def test_upsert_runner_subscriptions_records_run_id():
    eng = create_engine("sqlite:///:memory:")
    init_database(eng)
    Session = sessionmaker(bind=eng, autoflush=False, autocommit=False, future=True)
    now = datetime(2026, 5, 3, tzinfo=timezone.utc)
    with Session() as session:
        session.add(LiveRun(id="run-1", thread_id="thread", status="running"))
        upsert_runner_subscriptions(
            session,
            run_id="run-1",
            runner_id="runner-1",
            subs=[LiveSubscriptionSpec(channel="bars", symbol="spy", scale="1m")],
            now=now,
        )
        session.commit()
    with Session() as session:
        rows = read_active_subscriptions(session, now=now)
    assert rows == [
        LiveSubscriptionSpec(
            channel="bars",
            symbol="SPY",
            scale="1m",
            run_id="run-1",
            runner_id="runner-1",
        )
    ]


def test_read_run_input_events_filters_replayable_kinds():
    eng = create_engine("sqlite:///:memory:")
    init_database(eng)
    Session = sessionmaker(bind=eng, autoflush=False, autocommit=False, future=True)
    with Session() as session:
        session.add(LiveRun(id="run-1", thread_id="thread", status="running"))
        session.add(
            LiveRunEvent(
                run_id="run-1",
                event_type="input",
                kind="market_bar",
                payload={"symbol": "SPY"},
            )
        )
        session.add(
            LiveRunEvent(
                run_id="run-1",
                event_type="input",
                kind="input",
                payload={"input": {"unixtime": 1, "points": []}},
            )
        )
        session.add(
            LiveRunEvent(
                run_id="run-1",
                event_type="output",
                kind="output",
                payload={"output": []},
            )
        )
        session.commit()
    with Session() as session:
        market_events = read_run_market_events_after(session, run_id="run-1", after_id=0)
        strategy_inputs = read_run_strategy_inputs_after(session, run_id="run-1", after_id=0)
    assert [e.kind for e in market_events] == ["market_bar"]
    assert [e.kind for e in strategy_inputs] == ["input"]
