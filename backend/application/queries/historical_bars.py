from __future__ import annotations

import hashlib
import logging
import pickle
import threading
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from application.services.simulation_limits import CHUNK_BAR_BUDGET, plan_display_bars_fetch_chunks
from strategies import utils

logger = logging.getLogger(__name__)


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


def _disk_cache_path(cache_dir: Path, key: _CacheKey) -> Path:
    raw = "|".join(
        (
            key.ticker,
            key.scale,
            key.start.isoformat(),
            key.end.isoformat(),
            str(key.padding_days),
            key.provider,
        )
    )
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return cache_dir / f"{h}.pkl"


class HistoricalBarsQuery:
    """Loads OHLCV history via existing ``strategies.utils`` market data helpers.

    In-memory cache (default TTL 10 minutes) keyed by fetch arguments to avoid duplicate provider calls.
    Optional ``cache_dir`` stores the same keyed payloads on disk (pickle); disk entries respect the same TTL via file mtime.
    """

    def __init__(
        self,
        *,
        cache_ttl_seconds: float = 600.0,
        cache_dir: str | Path | None = None,
    ) -> None:
        self._cache_ttl = float(cache_ttl_seconds)
        self._cache_dir = Path(cache_dir).expanduser().resolve() if cache_dir else None
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
        logger.info(
            "fetch begin ticker=%s scale=%s start=%s end=%s padding_days=%s provider=%s",
            ticker.strip(),
            (scale or "").strip().lower(),
            start,
            end,
            padding_days,
            provider if isinstance(provider, str) and provider.strip() else provider,
        )
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
        if self._cache_dir is not None:
            path = _disk_cache_path(self._cache_dir, key)
            try:
                if path.is_file():
                    age = time.time() - path.stat().st_mtime
                    if age < self._cache_ttl:
                        disk_df = pickle.loads(path.read_bytes())
                        if isinstance(disk_df, pd.DataFrame):
                            with self._lock:
                                self._cache[key] = _CacheEntry(
                                    df=disk_df, expires_at=time.monotonic() + self._cache_ttl
                                )
                            return disk_df
            except Exception:
                logger.exception("disk cache read failed path=%s", path)
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
        if self._cache_dir is not None:
            path = _disk_cache_path(self._cache_dir, key)
            try:
                self._cache_dir.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(path.name + ".tmp")
                tmp.write_bytes(pickle.dumps(df, protocol=4))
                tmp.replace(path)
            except Exception:
                logger.exception("disk cache write failed path=%s", path)
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
        logger.info(
            "fetch_chunked_merge begin ticker=%s scale=%s start=%s end=%s chunks=%s max_bars_per_chunk=%s provider=%s",
            ticker.strip(),
            (scale or "").strip().lower(),
            start,
            end,
            len(chunks),
            max_bars_per_chunk,
            provider if isinstance(provider, str) and provider.strip() else provider,
        )
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
