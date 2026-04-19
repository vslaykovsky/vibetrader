from __future__ import annotations

import threading
import time
from typing import Callable


class ClockStopped(Exception):
    pass


class SpeedClock:
    """Wall-clock pacing: ``wait_next()`` blocks for ``1/bps`` seconds unless paused or stopped."""

    def __init__(
        self,
        bps: float,
        *,
        monotonic: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        if bps <= 0:
            raise ValueError("bps must be positive")
        self._mono = monotonic or time.monotonic
        self._sleep = sleeper or time.sleep
        self._lock = threading.Lock()
        self._bps = float(bps)
        self._paused = threading.Event()
        self._paused.set()
        self._stop = threading.Event()

    def _interval(self) -> float:
        with self._lock:
            return 1.0 / self._bps

    @property
    def interval_seconds(self) -> float:
        return self._interval()

    def change_speed(self, bps: float) -> None:
        if bps <= 0:
            raise ValueError("bps must be positive")
        with self._lock:
            self._bps = float(bps)

    def pause(self) -> None:
        self._paused.clear()

    def resume(self) -> None:
        self._paused.set()

    def stop(self) -> None:
        self._stop.set()
        self._paused.set()

    def wait_next(self) -> None:
        if self._stop.is_set():
            raise ClockStopped
        while not self._paused.is_set():
            if self._stop.is_set():
                raise ClockStopped
            self._paused.wait(timeout=0.05)
        if self._stop.is_set():
            raise ClockStopped
        deadline = self._mono() + self._interval()
        while self._mono() < deadline:
            if self._stop.is_set():
                raise ClockStopped
            if not self._paused.is_set():
                while not self._paused.is_set():
                    if self._stop.is_set():
                        raise ClockStopped
                    self._paused.wait(timeout=0.05)
                deadline = self._mono() + self._interval()
                continue
            remaining = deadline - self._mono()
            if remaining > 0:
                self._sleep(min(remaining, 0.05))
        if self._stop.is_set():
            raise ClockStopped
