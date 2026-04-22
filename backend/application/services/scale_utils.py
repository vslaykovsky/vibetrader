"""Scale (timeframe) helpers shared between simulation paths.

Supports the canonical ``strategies_v2`` scales: ``1m``, ``15m``, ``1h``, ``4h``, ``1d``, ``1w``.
"""

from __future__ import annotations

from typing import Final

import pandas as pd

_SCALE_MINUTES: Final[dict[str, int]] = {
    "1m": 1,
    "15m": 15,
    "1h": 60,
    "4h": 240,
    "1d": 60 * 24,
    "1w": 60 * 24 * 7,
}

_SCALE_FREQ: Final[dict[str, str]] = {
    "1m": "1min",
    "15m": "15min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1D",
    "1w": "7D",
}


def normalize_scale(scale: str) -> str:
    s = (scale or "").strip().lower()
    if s not in _SCALE_MINUTES:
        raise ValueError(
            f"Unsupported scale {scale!r}; expected one of {', '.join(sorted(_SCALE_MINUTES))}"
        )
    return s


def scale_minutes(scale: str) -> int:
    return _SCALE_MINUTES[normalize_scale(scale)]


def scale_freq(scale: str) -> str:
    return _SCALE_FREQ[normalize_scale(scale)]


def is_finer_or_equal(a: str, b: str) -> bool:
    """``a`` is at most as coarse as ``b`` (``a`` minutes ≤ ``b`` minutes)."""
    return scale_minutes(a) <= scale_minutes(b)


def scale_divides(finer: str, coarser: str) -> bool:
    """Finer scale divides coarser (so aggregation aligns). E.g. 1h divides 4h and 1d."""
    fm = scale_minutes(finer)
    cm = scale_minutes(coarser)
    if fm <= 0 or cm <= 0:
        return False
    return cm % fm == 0


def floor_ts_to_scale(ts: pd.Timestamp, scale: str) -> pd.Timestamp:
    """Floor ``ts`` to the start of its ``scale`` bucket (UTC-anchored)."""
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    else:
        t = t.tz_convert("UTC")
    return t.floor(scale_freq(scale))
