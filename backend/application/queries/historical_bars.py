from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import date
from typing import Optional

import pandas as pd
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from application.services.simulation_limits import CHUNK_BAR_BUDGET, plan_display_bars_fetch_chunks
from strategies import utils


def scale_to_timeframe(scale: str) -> TimeFrame:
    """Map strategies_v2-style scale string to Alpaca ``TimeFrame`` (also used for MOEX period mapping)."""
    key = (scale or "").strip().lower()
    mapping: dict[str, TimeFrame] = {
        "1m": TimeFrame.Minute,
        "15m": TimeFrame(15, TimeFrameUnit.Minute),
        "1h": TimeFrame.Hour,
        "4h": TimeFrame(4, TimeFrameUnit.Hour),
        "1d": TimeFrame.Day,
        "1w": TimeFrame.Week,
    }
    if key not in mapping:
        raise ValueError(
            f"Unsupported scale {scale!r}; expected one of {', '.join(sorted(mapping))}"
        )
    return mapping[key]


@dataclass(frozen=True)
class _CacheKey:
    ticker: str
    scale: str
    start: date
    end: date
    padding_days: int
    provider: str


@dataclass
class _CacheEntry:
    df: pd.DataFrame
    expires_at: float


class HistoricalBarsQuery:
    """Loads OHLCV history via existing ``strategies.utils`` market data helpers.

    In-memory cache (default TTL 10 minutes) keyed by fetch arguments to avoid duplicate provider calls.
    """

    def __init__(self, *, cache_ttl_seconds: float = 600.0) -> None:
        self._cache_ttl = float(cache_ttl_seconds)
        self._lock = threading.Lock()
        self._cache: dict[_CacheKey, _CacheEntry] = {}

    def fetch(
        self,
        ticker: str,
        scale: str,
        start: date,
        end: date,
        padding_days: int = 0,
        *,
        provider: Optional[str] = None,
    ) -> pd.DataFrame:
        timeframe = scale_to_timeframe(scale)
        start_s = start.isoformat()
        end_s = end.isoformat()
        prov = provider if isinstance(provider, str) and provider.strip() else ""
        key = _CacheKey(
            ticker=ticker.strip(),
            scale=(scale or "").strip().lower(),
            start=start,
            end=end,
            padding_days=int(padding_days),
            provider=prov,
        )
        now = time.monotonic()
        with self._lock:
            stale_keys = [k for k, e in self._cache.items() if e.expires_at <= now]
            for k in stale_keys:
                del self._cache[k]
            hit = self._cache.get(key)
            if hit is not None and hit.expires_at > now:
                return hit.df
        df = utils.fetch_stock_bars(
            ticker=key.ticker,
            start_test_date=start_s,
            end_test_date=end_s,
            history_padding_days=key.padding_days,
            timeframe=timeframe,
            provider=provider if prov else None,
        )
        with self._lock:
            self._cache[key] = _CacheEntry(df=df, expires_at=time.monotonic() + self._cache_ttl)
        return df

    def fetch_chunked_merge(
        self,
        ticker: str,
        scale: str,
        start: date,
        end: date,
        padding_days: int = 0,
        *,
        max_bars_per_chunk: int = CHUNK_BAR_BUDGET,
        provider: Optional[str] = None,
    ) -> tuple[pd.DataFrame, int]:
        """Load ``[start, end]`` in calendar windows under ``max_bars_per_chunk`` (estimate), merge, dedupe index.

        ``padding_days`` is applied only to the **first** window so warmup matches a single-range fetch.
        Returns ``(merged_df, num_windows)``.
        """
        chunks = plan_display_bars_fetch_chunks(
            start, end, scale, max_bars_per_chunk=max_bars_per_chunk
        )
        if not chunks:
            return pd.DataFrame(), 0
        frames: list[pd.DataFrame] = []
        for i, (cs, ce) in enumerate(chunks):
            pad = int(padding_days) if i == 0 else 0
            part = self.fetch(ticker, scale, cs, ce, padding_days=pad, provider=provider)
            if part is not None and not part.empty:
                frames.append(part)
        if not frames:
            return pd.DataFrame(), len(chunks)
        merged = pd.concat(frames).sort_index()
        merged = merged[~merged.index.duplicated(keep="first")]
        return merged, len(chunks)
