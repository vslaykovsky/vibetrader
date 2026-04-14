from __future__ import annotations

from sqlalchemy import desc
from sqlalchemy.orm import Session
from langsmith import traceable

from db.models import Strategy


def get_strategy_by_id(session: Session, strategy_id: str) -> Strategy | None:
    return session.get(Strategy, strategy_id)


@traceable(name="latest_thread_strategy")
def latest_thread_strategy(session: Session, thread_id: str) -> Strategy | None:
    return (
        session.query(Strategy)
        .filter_by(thread_id=thread_id)
        .order_by(desc(Strategy.created_at))
        .first()
    )

@traceable(name="ensure_latest_thread_strategy")
def ensure_latest_thread_strategy(
    session: Session,
    thread_id: str,
    created_by: str | None,
    created_by_email: str | None,
) -> Strategy:
    strategy = latest_thread_strategy(session, thread_id)
    if strategy is not None:
        return strategy
    strategy = Strategy(
        thread_id=thread_id,
        created_by=created_by,
        created_by_email=created_by_email,
        messages=[],
        canvas={},
    )
    session.add(strategy)
    session.flush()
    return strategy
