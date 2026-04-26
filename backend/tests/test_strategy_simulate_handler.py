import queue
import time
from datetime import date
from pathlib import Path

import pandas as pd

from application.schemas.simulation_dto import StartSimulationCommand
from application.services.simulation_registry import SimulationRegistry
from application.use_cases.strategy_simulate import StrategySimulateCommandHandler

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class InstantPace:
    def wait_next(self) -> None:
        return

    def pause(self) -> None:
        return

    def resume(self) -> None:
        return

    def stop(self) -> None:
        return

    def change_speed(self, bps: float) -> None:
        return


class FakeBarsQuery:
    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df

    def fetch(
        self,
        ticker: str,
        scale: str,
        start: date,
        end: date,
        padding_days: int = 0,
        *,
        provider: str | None = None,
    ) -> pd.DataFrame:
        return self._df

    def fetch_chunked_merge(
        self,
        ticker: str,
        scale: str,
        start: date,
        end: date,
        padding_days: int = 0,
        *,
        max_bars_per_chunk: int = 100_000,
        provider: str | None = None,
    ) -> tuple[pd.DataFrame, int]:
        _ = (ticker, scale, start, end, padding_days, max_bars_per_chunk, provider)
        return self._df, 1


def _collect_events(session, timeout: float = 10.0) -> list[dict]:
    out: list[dict] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            ev = session.events.get(timeout=0.2)
        except queue.Empty:
            continue
        if ev is None:
            break
        out.append(ev)
    return out


def test_simulation_handler_echo_emits_bars_in_date_range():
    n = 10
    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    df = pd.DataFrame(
        {
            "open": [100.0] * n,
            "high": [101.0] * n,
            "low": [99.0] * n,
            "close": [100.5] * n,
            "volume": [1_000.0] * n,
        },
        index=idx,
    )
    registry = SimulationRegistry()
    handler = StrategySimulateCommandHandler(
        registry,
        FakeBarsQuery(df),
        pacing_factory=lambda _bps: InstantPace(),
    )
    cmd = StartSimulationCommand(
        user_id="user-1",
        thread_id="thread-1",
        start_date=date(2024, 1, 3),
        end_date=date(2024, 1, 5),
        initial_speed_bps=10.0,
        strategy_workspace=FIXTURES_DIR,
        strategy_entry="echo_strategy.py",
    )
    handler.start(cmd)
    sess = registry.get("user-1", "thread-1")
    assert sess is not None
    events = _collect_events(sess, timeout=15.0)
    kinds = [e.get("kind") for e in events]
    assert kinds.count("bar") == 3
    bar_events = [e for e in events if e.get("kind") == "bar"]
    assert bar_events[0]["ohlc"]["volume"] == 1_000.0
    assert "done" in [e.get("status") for e in events if e.get("kind") == "status"]
