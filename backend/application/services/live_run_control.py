from __future__ import annotations

from db.models import LiveRun


def live_run_row_requests_stop(row: LiveRun | None) -> bool:
    if row is None:
        return True
    s = (row.status or "").strip().lower()
    return s in ("stopping", "stopped")
