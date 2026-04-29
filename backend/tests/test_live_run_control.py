from application.services.live_run_control import live_run_row_requests_stop
from db.models import LiveRun


def test_live_run_row_requests_stop():
    assert live_run_row_requests_stop(None) is True
    assert live_run_row_requests_stop(LiveRun(id="a", thread_id="b", status="stopping")) is True
    assert live_run_row_requests_stop(LiveRun(id="a", thread_id="b", status="stopped")) is True
    assert live_run_row_requests_stop(LiveRun(id="a", thread_id="b", status="running")) is False
    assert live_run_row_requests_stop(LiveRun(id="a", thread_id="b", status="starting")) is False
