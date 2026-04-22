from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

_DEFAULT_PARAMS = Path(__file__).resolve().parents[2] / "strategies_v2" / "params.json"
# Upper bound on *estimated* bars per single provider fetch; long ranges are split into windows.
CHUNK_BAR_BUDGET = 100_000


def read_strategy_scale(params_path: Path | None = None) -> str:
    path = params_path or _DEFAULT_PARAMS
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        s = data.get("scale")
        if isinstance(s, str) and s.strip():
            return s.strip().lower()
    except Exception:
        pass
    return "1d"


def estimate_source_bar_count(start: date, end: date, scale: str) -> int:
    """Upper-bound estimate of bars the host will iterate for the given range and strategy scale."""
    days = max(1, (end - start).days + 1)
    sc = (scale or "1d").strip().lower()
    if sc == "1w":
        return max(1, (days + 6) // 7)
    per_day = {"1m": 1440, "15m": 96, "1h": 24, "4h": 6, "1d": 1}.get(sc)
    if per_day is None:
        per_day = 24
    return int(math.ceil(days * per_day))


def simulation_date_span_error(start: date, end: date) -> str | None:
    """Calendar / ordering limits shared by simulation start and display-bars."""
    if start > end:
        return "start_date must be on or before end_date"
    return None


def _max_chunk_end_inclusive(
    start_chunk: date, end_limit: date, scale: str, max_bars: int
) -> date:
    """Latest date ``d`` in ``[start_chunk, end_limit]`` with bar estimate under ``max_bars``."""
    if start_chunk > end_limit:
        return end_limit
    if estimate_source_bar_count(start_chunk, end_limit, scale) <= max_bars:
        return end_limit
    lo = 0
    hi = (end_limit - start_chunk).days
    best = start_chunk
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = start_chunk + timedelta(days=mid)
        if cand > end_limit:
            cand = end_limit
        n = estimate_source_bar_count(start_chunk, cand, scale)
        if n <= max_bars:
            best = cand
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def plan_display_bars_fetch_chunks(
    start: date,
    end: date,
    scale: str,
    *,
    max_bars_per_chunk: int = CHUNK_BAR_BUDGET,
) -> list[tuple[date, date]]:
    """Split ``[start, end]`` into contiguous calendar windows each within ``max_bars_per_chunk`` (estimate).

    Used for chunked provider fetches (simulation OHLC and ``GET /simulation/display_bars``).
    Chunks are inclusive on both ends; the next chunk starts the day after the previous chunk end
    to avoid duplicate calendar days.
    """
    span_err = simulation_date_span_error(start, end)
    if span_err is not None:
        return []
    sc = (scale or "1d").strip().lower()
    out: list[tuple[date, date]] = []
    cur = start
    while cur <= end:
        ce = _max_chunk_end_inclusive(cur, end, sc, max_bars_per_chunk)
        if ce < cur:
            ce = cur
        out.append((cur, ce))
        if ce >= end:
            break
        cur = ce + timedelta(days=1)
    return out


def simulation_start_validation_error(
    start: date, end: date, *, scale: str | None = None, params_path: Path | None = None
) -> str | None:
    """Only calendar span; OHLC is loaded in chunks at the strategy timeframe (see ``HistoricalBarsQuery``)."""
    _ = (scale, params_path)  # API compatibility / future use
    return simulation_date_span_error(start, end)
