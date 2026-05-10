from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

SimulationStatus = Literal["starting", "running", "paused", "done", "error", "stopped"]


@dataclass(init=False)
class InitSimulationCommand:
    """Start historical replay from ``start_date`` with a forward bar window sized by the host."""

    user_id: str
    thread_id: str
    start_date: date
    initial_speed_bps: float
    initial_deposit: float
    initial_scale: str | None
    adjust_for_dividends: bool
    lang: str

    def __init__(
        self,
        *,
        user_id: str,
        thread_id: str,
        start_date: date,
        initial_speed_bps: float = 1.0,
        initial_deposit: float = 10_000.0,
        initial_scale: str | None = None,
        adjust_for_dividends: bool = False,
        lang: str = "en",
    ) -> None:
        self.user_id = user_id
        self.thread_id = thread_id
        self.start_date = start_date
        self.initial_speed_bps = initial_speed_bps
        self.initial_deposit = initial_deposit
        self.initial_scale = initial_scale
        self.adjust_for_dividends = bool(adjust_for_dividends)
        self.lang = lang if lang in ("en", "ru") else "en"


def simulation_event(kind: str, **fields: Any) -> dict[str, Any]:
    return {"kind": kind, **fields}
