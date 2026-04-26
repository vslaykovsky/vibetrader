from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from typing import Any

from application.schemas.simulation_dto import simulation_event


class SimulationSession:
    """One in-memory simulation: events queue, optional worker thread, stop flag.

    After ``POST /simulation/init`` the session exists without a worker; ``play`` starts the
    worker that streams bars.
    """

    def __init__(
        self,
        *,
        user_id: str,
        thread_id: str,
        initial_speed_bps: float = 1.0,
        pending_cmd: Any = None,
    ) -> None:
        self.user_id = user_id
        self.thread_id = thread_id
        self.pending_cmd = pending_cmd
        self.events: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._stop = threading.Event()
        self.pause = threading.Event()
        self.pause.set()
        self._speed_lock = threading.Lock()
        self._speed_bps = float(initial_speed_bps)
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()
        self._anchor_cond = threading.Condition()
        self._display_anchor_chart_unix = 0
        self._display_anchor_chart_scale = ""

    def set_display_anchor(self, chart_bar_open_unix: int, chart_scale: str) -> None:
        """Chart OHLC anchor from ``GET /simulation/display_bars`` (last candle open, chart ``scale``)."""
        with self._anchor_cond:
            self._display_anchor_chart_unix = int(chart_bar_open_unix)
            self._display_anchor_chart_scale = (chart_scale or "").strip().lower()
            self._anchor_cond.notify_all()

    def get_display_anchor(self) -> tuple[int, str]:
        with self._anchor_cond:
            return self._display_anchor_chart_unix, self._display_anchor_chart_scale

    def wait_until_base_row_allowed(
        self, get_cap_base_row: Callable[[], int], target_base_row: int
    ) -> bool:
        """Block until ``target_base_row <= get_cap_base_row()`` or stop. Returns False if stopped."""
        with self._anchor_cond:
            while not self._stop.is_set():
                if target_base_row <= get_cap_base_row():
                    return True
                self._anchor_cond.wait(timeout=0.25)
            return False

    def get_speed_bps(self) -> float:
        with self._speed_lock:
            return self._speed_bps

    def set_speed_bps(self, bps: float) -> None:
        with self._speed_lock:
            self._speed_bps = float(bps)

    def begin_run(self, run_fn: Callable[[], None]) -> bool:
        """Start the worker thread once (or again after a previous run finished). Returns False if a run is already in progress."""
        with self._thread_lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            self.pause.set()
            self._thread = threading.Thread(target=self._wrap_run, args=(run_fn,), daemon=True)
            self._thread.start()
            return True

    def is_worker_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _wrap_run(self, run_fn: Callable[[], None]) -> None:
        try:
            run_fn()
        except Exception as exc:
            self.events.put(simulation_event("status", status="error", message=str(exc)))
        finally:
            self.events.put(simulation_event("status", status="done"))
            self.events.put(None)

    def stop(self) -> None:
        self._stop.set()
        self.pause.set()

    @property
    def stop_requested(self) -> bool:
        return self._stop.is_set()

    def emit(self, payload: dict[str, Any]) -> None:
        self.events.put(payload)
