from __future__ import annotations

import queue
import threading
from typing import Any, Callable

from application.schemas.simulation_dto import simulation_event


class SimulationSession:
    """One in-memory simulation: events queue + worker thread + stop flag."""

    def __init__(
        self,
        *,
        user_id: str,
        thread_id: str,
        run_fn: Callable[[], None],
        initial_speed_bps: float = 1.0,
    ) -> None:
        self.user_id = user_id
        self.thread_id = thread_id
        self.events: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._stop = threading.Event()
        self.pause = threading.Event()
        self.pause.set()
        self._speed_lock = threading.Lock()
        self._speed_bps = float(initial_speed_bps)
        self._thread = threading.Thread(target=self._wrap_run, args=(run_fn,), daemon=True)

    def get_speed_bps(self) -> float:
        with self._speed_lock:
            return self._speed_bps

    def set_speed_bps(self, bps: float) -> None:
        with self._speed_lock:
            self._speed_bps = float(bps)

    def _wrap_run(self, run_fn: Callable[[], None]) -> None:
        try:
            run_fn()
        except Exception as exc:
            self.events.put(simulation_event("status", status="error", message=str(exc)))
        finally:
            self.events.put(simulation_event("status", status="done"))
            self.events.put(None)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.pause.set()

    @property
    def stop_requested(self) -> bool:
        return self._stop.is_set()

    def emit(self, payload: dict[str, Any]) -> None:
        self.events.put(payload)
