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
from db.models import Candle, CandleTimeframe
from db.session import SessionLocal, engine
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


def _scale_to_candle_timeframe(scale: str) -> CandleTimeframe:
    key = (scale or "").strip().lower()
    mapping: dict[str, CandleTimeframe] = {
        "1m": CandleTimeframe.M1,
        "15m": CandleTimeframe.M15,
        "1h": CandleTimeframe.H1,
        "4h": CandleTimeframe.H4,
        "1d": CandleTimeframe.D1,
        "1w": CandleTimeframe.W1,
    }
    if key not in mapping:
        raise ValueError(
            f"Unsupported scale {scale!r}; expected one of {', '.join(sorted(mapping))}"
        )
    return mapping[key]


def _date_bounds_utc(start: date, end: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    s = pd.Timestamp(start.isoformat()).tz_localize("UTC")
    e = pd.Timestamp(end.isoformat()).tz_localize("UTC") + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    return s, e


def _df_from_candles(rows: list[Candle]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    ts = [pd.Timestamp(r.timestamp).tz_convert("UTC").tz_convert(None) for r in rows]
    df = pd.DataFrame(
        {
            "open": [float(r.open) for r in rows],
            "high": [float(r.high) for r in rows],
            "low": [float(r.low) for r in rows],
            "close": [float(r.close) for r in rows],
            "volume": [float(r.volume) for r in rows],
        },
        index=ts,
    )
    df = df.sort_index()
    df.index.name = None
    return df


def _records_from_df(ticker: str, tf: CandleTimeframe, df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    shaped = df.copy()
    shaped = shaped.rename(columns=str.lower)
    cols = [c for c in ["open", "high", "low", "close", "volume"] if c in shaped.columns]
    shaped = shaped[cols]
    shaped.index = pd.to_datetime(shaped.index)
    if getattr(shaped.index, "tz", None) is None:
        shaped.index = shaped.index.tz_localize("UTC")
    else:
        shaped.index = shaped.index.tz_convert("UTC")
    shaped = shaped.sort_index()

    out: list[dict] = []
    for idx, row in shaped.iterrows():
        o = float(row["open"]) if "open" in cols else float("nan")
        h = float(row["high"]) if "high" in cols else float("nan")
        l = float(row["low"]) if "low" in cols else float("nan")
        c = float(row["close"]) if "close" in cols else float("nan")
        v = float(row["volume"]) if "volume" in cols else 0.0
        if pd.isna(o) or pd.isna(h) or pd.isna(l) or pd.isna(c):
            continue
        out.append(
            {
                "timestamp": idx.to_pydatetime(),
                "ticker": ticker,
                "timeframe": tf,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v,
            }
        )
    return out


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

        # Postgres-backed candles cache. We only serve a DB hit if it fully covers the requested
        # range (including padding) to preserve warmup semantics.
        try:
            tf = _scale_to_candle_timeframe(key.scale)
            padded_start = (
                pd.Timestamp(key.start.isoformat()) - pd.Timedelta(days=int(key.padding_days))
            ).date()
            s_utc, e_utc = _date_bounds_utc(padded_start, key.end)
            s_naive = s_utc.tz_convert(None).to_pydatetime()
            e_naive = e_utc.tz_convert(None).to_pydatetime()

            session = SessionLocal()
            try:
                cached_rows: list[Candle] = (
                    session.query(Candle)
                    .filter(Candle.ticker == key.ticker, Candle.timeframe == tf)
                    .filter(Candle.timestamp >= s_naive, Candle.timestamp <= e_naive)
                    .order_by(Candle.timestamp.asc())
                    .all()
                )
            finally:
                session.close()

            if cached_rows:
                min_ts = pd.Timestamp(cached_rows[0].timestamp).tz_convert("UTC")
                max_ts = pd.Timestamp(cached_rows[-1].timestamp).tz_convert("UTC")
                if min_ts <= s_utc and max_ts >= e_utc:
                    df_hit = _df_from_candles(cached_rows)
                    with self._lock:
                        self._cache[key] = _CacheEntry(
                            df=df_hit, expires_at=time.monotonic() + self._cache_ttl
                        )
                    return df_hit
        except Exception:
            logger.exception("candles cache read failed ticker=%s scale=%s", key.ticker, key.scale)
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

        # Best-effort write-through to Postgres candles cache.
        try:
            tf = _scale_to_candle_timeframe(key.scale)
            records = _records_from_df(key.ticker, tf, df)
            if records:
                session = SessionLocal()
                try:
                    if engine.dialect.name == "postgresql":
                        from sqlalchemy.dialects.postgresql import insert as pg_insert

                        stmt = pg_insert(Candle).values(records)
                        stmt = stmt.on_conflict_do_update(
                            index_elements=["ticker", "timeframe", "timestamp"],
                            set_={
                                "open": stmt.excluded.open,
                                "high": stmt.excluded.high,
                                "low": stmt.excluded.low,
                                "close": stmt.excluded.close,
                                "volume": stmt.excluded.volume,
                            },
                        )
                        session.execute(stmt)
                    else:
                        for r in records:
                            session.merge(Candle(**r))
                    session.commit()
                finally:
                    session.close()
        except Exception:
            logger.exception("candles cache write failed ticker=%s scale=%s", key.ticker, key.scale)
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
