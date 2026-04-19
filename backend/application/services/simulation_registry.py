from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from application.services.simulation_session import SimulationSession


class SimulationRegistry:
    """At most one active session per (user_id, thread_id)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[tuple[str, str], SimulationSession] = {}

    def get(self, user_id: str, thread_id: str) -> SimulationSession | None:
        key = (user_id, thread_id)
        with self._lock:
            return self._sessions.get(key)

    def replace(self, user_id: str, thread_id: str, session: SimulationSession) -> None:
        key = (user_id, thread_id)
        with self._lock:
            old = self._sessions.get(key)
            if old is not None:
                old.stop()
            self._sessions[key] = session

    def remove(self, user_id: str, thread_id: str) -> None:
        key = (user_id, thread_id)
        with self._lock:
            old = self._sessions.pop(key, None)
            if old is not None:
                old.stop()
