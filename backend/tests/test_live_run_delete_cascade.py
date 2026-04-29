from db.models import LiveRun, LiveRunEvent, LiveRunOrder
from db.session import SessionLocal, engine, init_database


def test_live_run_delete_cascades_child_rows():
    init_database(engine)
    sid = "00000000-0000-0000-0000-000000000001"
    with SessionLocal() as session:
        session.add(LiveRun(id=sid, thread_id="t", status="stopped"))
        session.add(LiveRunEvent(run_id=sid, kind="status", payload={}))
        session.add(LiveRunOrder(run_id=sid, client_order_id="c1"))
        session.commit()
    with SessionLocal() as session:
        row = session.get(LiveRun, sid)
        session.delete(row)
        session.commit()
    with SessionLocal() as session:
        assert session.query(LiveRunEvent).filter_by(run_id=sid).count() == 0
        assert session.query(LiveRunOrder).filter_by(run_id=sid).count() == 0
