from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from datetime import date
from datetime import datetime, timezone
from datetime import timedelta
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
    asset_class: str


@dataclass
class _CacheEntry:
    df: pd.DataFrame
    expires_at: float


class HistoricalBarsQuery:
    """Loads OHLCV history via existing ``strategies.utils`` market data helpers.

    In-memory cache (default TTL 10 minutes) keyed by fetch arguments to avoid duplicate provider calls.
    Also uses a Postgres-backed candles cache (``candle`` table) as best-effort read-through/write-through.
    """

    def __init__(
        self,
        *,
        cache_ttl_seconds: float = 600.0,
    ) -> None:
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
        asset_class: Optional[str] = None,
        drop_wide_spread_bars: bool = True,
    ) -> pd.DataFrame:
        timeframe = scale_to_timeframe(scale)
        start_s = start.isoformat()
        end_s = end.isoformat()
        prov = provider if isinstance(provider, str) and provider.strip() else ""
        asset = (asset_class or "").strip().lower()
        if asset and asset not in {"us_equity", "crypto"}:
            raise ValueError("asset_class must be one of: us_equity, crypto")
        asset = asset or "us_equity"
        norm_ticker = utils.normalize_crypto_symbol(ticker) if asset == "crypto" else ticker
        logger.info(
            "fetch begin ticker=%s scale=%s start=%s end=%s padding_days=%s provider=%s",
            norm_ticker.strip(),
            (scale or "").strip().lower(),
            start,
            end,
            padding_days,
            provider if isinstance(provider, str) and provider.strip() else provider,
        )
        key = _CacheKey(
            ticker=norm_ticker.strip(),
            scale=(scale or "").strip().lower(),
            start=start,
            end=end,
            padding_days=int(padding_days),
            provider=prov,
            asset_class=asset,
        )
        now = time.monotonic()
        with self._lock:
            stale_keys = [k for k, e in self._cache.items() if e.expires_at <= now]
            for k in stale_keys:
                del self._cache[k]
            hit = self._cache.get(key)
            if hit is not None and hit.expires_at > now:
                logger.info(
                    "fetch cache hit (memory) ticker=%s scale=%s start=%s end=%s padding_days=%s provider=%s",
                    key.ticker,
                    key.scale,
                    key.start,
                    key.end,
                    key.padding_days,
                    provider if isinstance(provider, str) and provider.strip() else provider,
                )
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
                    logger.info(
                        "fetch cache hit (db_full) ticker=%s scale=%s start=%s end=%s padding_days=%s provider=%s rows=%s",
                        key.ticker,
                        key.scale,
                        padded_start,
                        key.end,
                        key.padding_days,
                        provider if isinstance(provider, str) and provider.strip() else provider,
                        len(cached_rows),
                    )
                    df_hit = _df_from_candles(cached_rows)
                    with self._lock:
                        self._cache[key] = _CacheEntry(
                            df=df_hit, expires_at=time.monotonic() + self._cache_ttl
                        )
                    return df_hit
                if _covers_all_whole_weeks(cached_rows, start=padded_start, end=key.end):
                    logger.info(
                        "fetch cache hit (db_weekly) ticker=%s scale=%s start=%s end=%s padding_days=%s provider=%s rows=%s",
                        key.ticker,
                        key.scale,
                        padded_start,
                        key.end,
                        key.padding_days,
                        provider if isinstance(provider, str) and provider.strip() else provider,
                        len(cached_rows),
                    )
                    df_hit = _df_from_candles(cached_rows)
                    with self._lock:
                        self._cache[key] = _CacheEntry(
                            df=df_hit, expires_at=time.monotonic() + self._cache_ttl
                        )
                    return df_hit
        except Exception:
            logger.exception("candles cache read failed ticker=%s scale=%s", key.ticker, key.scale)
        if asset == "crypto":
            df = utils.fetch_crypto_bars(
                ticker=key.ticker,
                start_test_date=start_s,
                end_test_date=end_s,
                timeframe=timeframe,
                drop_wide_spread_bars=drop_wide_spread_bars,
            )
        else:
            df = utils.fetch_stock_bars(
                ticker=key.ticker,
                start_test_date=start_s,
                end_test_date=end_s,
                history_padding_days=key.padding_days,
                timeframe=timeframe,
                provider=provider if prov else None,
                drop_wide_spread_bars=drop_wide_spread_bars,
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

                        # Avoid "too many parameters" errors for large backfills (e.g. 10y of 1h bars).
                        # Each row binds multiple params; keep batches comfortably below typical driver limits.
                        batch_size = 5_000
                        for i in range(0, len(records), batch_size):
                            batch = records[i : i + batch_size]
                            stmt = pg_insert(Candle).values(batch)
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


def _covers_all_whole_weeks(cached_rows: list[Candle], *, start: date, end: date) -> bool:
    """Return True if we have at least one cached bar for every *whole* week fully contained
    in the inclusive calendar range [start, end].

    This is used to avoid refetching for weekend gaps (US equities don't trade on weekends).
    """
    if not cached_rows:
        return False

    # Whole weeks are Monday..Sunday that are fully inside [start, end].
    first_monday = start + timedelta(days=(7 - start.weekday()) % 7)
    last_sunday = end - timedelta(days=(end.weekday() + 1) % 7)
    if first_monday > last_sunday:
        return False

    required: set[tuple[int, int]] = set()
    cur = first_monday
    while cur <= last_sunday:
        iso = cur.isocalendar()
        required.add((int(iso.year), int(iso.week)))
        cur = cur + timedelta(days=7)

    present: set[tuple[int, int]] = set()
    for r in cached_rows:
        ts = r.timestamp
        if isinstance(ts, datetime):
            dt = ts
        else:
            dt = pd.Timestamp(ts).to_pydatetime()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        iso = dt.date().isocalendar()
        present.add((int(iso.year), int(iso.week)))

    return required.issubset(present)
