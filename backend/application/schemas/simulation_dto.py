from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

SimulationStatus = Literal["starting", "running", "paused", "done", "error", "stopped"]


@dataclass(frozen=True)
class StartSimulationCommand:
    user_id: str
    thread_id: str
    start_date: date
    end_date: date
    initial_speed_bps: float = 1.0
    initial_deposit: float = 10_000.0
    """Override for tests (default: ``backend/strategies_v2``)."""
    strategy_workspace: Path | None = None
    strategy_entry: str = "strategy.py"


def simulation_event(kind: str, **fields: Any) -> dict[str, Any]:
    return {"kind": kind, **fields}
