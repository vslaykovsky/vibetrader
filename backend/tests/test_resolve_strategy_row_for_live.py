from __future__ import annotations

import uuid

from db.models import Strategy
from db.session import SessionLocal
from db.strategy_queries import resolve_strategy_row_for_live


def test_resolve_strategy_row_for_live():
    tid = str(uuid.uuid4())
    session = SessionLocal()
    try:
        row, err = resolve_strategy_row_for_live(session, thread_id=tid, strategy_id="")
        assert row is None and err == "no saved strategy for this thread"

        session.add(Strategy(thread_id=tid, code="a = 1\n"))
        session.commit()
        row2, err2 = resolve_strategy_row_for_live(session, thread_id=tid, strategy_id="")
        assert err2 is None and row2 is not None and row2.code.strip() == "a = 1"

        snap = Strategy(thread_id=tid, code="b = 2\n")
        session.add(snap)
        session.commit()
        sid = snap.id
        row3, err3 = resolve_strategy_row_for_live(session, thread_id=tid, strategy_id=sid)
        assert err3 is None and row3 is not None and row3.id == sid

        row4, err4 = resolve_strategy_row_for_live(
            session, thread_id=str(uuid.uuid4()), strategy_id=sid
        )
        assert row4 is None and err4 == "strategy id does not match thread_id"
    finally:
        session.close()
