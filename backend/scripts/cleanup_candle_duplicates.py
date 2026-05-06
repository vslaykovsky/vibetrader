from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

try:
    import dotenv

    dotenv.load_dotenv(_BACKEND_ROOT / ".env")
except Exception:
    pass

from db.session import engine


TIMEFRAMES = ("D1", "W1")
FAILED_INDEX_NAMES = (
    "ix_candles_d1_ticker_utc_date_tmp",
    "ix_candles_w1_ticker_utc_week_tmp",
    "ix_candles_d1_ticker_dividend_utc_date_tmp",
    "ix_candles_w1_ticker_dividend_utc_week_tmp",
)
INDEX_DDL = (
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_candles_d1_ticker_dividend_utc_date
    ON candles (ticker, dividend_adjusted, ((timestamp AT TIME ZONE 'UTC')::date))
    WHERE timeframe = 'D1'::candle_timeframe
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_candles_w1_ticker_dividend_utc_week
    ON candles (ticker, dividend_adjusted, (date_trunc('week', timestamp AT TIME ZONE 'UTC')::date))
    WHERE timeframe = 'W1'::candle_timeframe
    """,
)


def _bucket_expr(timeframe: str) -> str:
    if timeframe == "D1":
        return "(timestamp AT TIME ZONE 'UTC')::date"
    if timeframe == "W1":
        return "date_trunc('week', timestamp AT TIME ZONE 'UTC')::date"
    raise ValueError(f"unsupported timeframe {timeframe!r}")


def _timeframes(raw: str) -> tuple[str, ...]:
    if raw == "all":
        return TIMEFRAMES
    value = raw.upper()
    if value not in TIMEFRAMES:
        raise SystemExit(f"--timeframe must be one of: all, {', '.join(TIMEFRAMES)}")
    return (value,)


def print_timestamp_profile() -> None:
    sql = text(
        """
        SELECT
            timeframe::text AS timeframe,
            dividend_adjusted,
            EXTRACT(hour FROM timestamp AT TIME ZONE 'UTC')::int AS utc_hour,
            COUNT(*) AS rows,
            MIN(timestamp) AS first_timestamp,
            MAX(timestamp) AS last_timestamp
        FROM candles
        GROUP BY timeframe, dividend_adjusted, utc_hour
        ORDER BY timeframe, dividend_adjusted, rows DESC
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql).mappings().all()
    print("timestamp profile (stored as timestamptz, displayed here in UTC):")
    for row in rows:
        print(
            f"  {row['timeframe']:>3} dividend_adjusted={row['dividend_adjusted']} hour={row['utc_hour']:02d} "
            f"rows={row['rows']} first={row['first_timestamp']} last={row['last_timestamp']}"
        )


def create_indexes() -> None:
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for name in FAILED_INDEX_NAMES:
            conn.execute(text(f"DROP INDEX CONCURRENTLY IF EXISTS {name}"))
        for ddl in INDEX_DDL:
            conn.execute(text(ddl))


def duplicate_buckets(timeframe: str, ticker: str) -> list[dict]:
    bucket = _bucket_expr(timeframe)
    where_ticker = "AND ticker = :ticker" if ticker else ""
    sql = text(
        f"""
        SELECT
            ticker,
            timeframe::text AS timeframe,
            dividend_adjusted,
            {bucket} AS bucket_start,
            COUNT(*) AS bucket_rows
        FROM candles
        WHERE timeframe = CAST(:timeframe AS candle_timeframe)
        {where_ticker}
        GROUP BY ticker, timeframe, dividend_adjusted, {bucket}
        HAVING COUNT(*) > 1
        ORDER BY ticker, dividend_adjusted, bucket_start
        """
    )
    params = {"timeframe": timeframe, "ticker": ticker}
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(sql, params).mappings().all()]


def rows_for_bucket(
    timeframe: str, ticker: str, bucket_start: object, dividend_adjusted: bool
) -> list[dict]:
    bucket = _bucket_expr(timeframe)
    sql = text(
        f"""
        SELECT
            ticker,
            timeframe::text AS timeframe,
            dividend_adjusted,
            timestamp,
            {bucket} AS bucket_start,
            open,
            high,
            low,
            close,
            volume
        FROM candles
        WHERE ticker = :ticker
            AND timeframe = CAST(:timeframe AS candle_timeframe)
            AND dividend_adjusted = :dividend_adjusted
            AND {bucket} = :bucket_start
        ORDER BY
            CASE
                WHEN EXTRACT(hour FROM timestamp AT TIME ZONE 'UTC') IN (4, 5)
                    AND EXTRACT(minute FROM timestamp AT TIME ZONE 'UTC') = 0
                    AND EXTRACT(second FROM timestamp AT TIME ZONE 'UTC') = 0
                THEN 0
                WHEN EXTRACT(hour FROM timestamp AT TIME ZONE 'UTC') = 0
                    AND EXTRACT(minute FROM timestamp AT TIME ZONE 'UTC') = 0
                    AND EXTRACT(second FROM timestamp AT TIME ZONE 'UTC') = 0
                THEN 2
                ELSE 1
            END,
            timestamp DESC
        """
    )
    params = {
        "timeframe": timeframe,
        "ticker": ticker,
        "bucket_start": bucket_start,
        "dividend_adjusted": bool(dividend_adjusted),
    }
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(sql, params).mappings().all()]


def invalid_rows_for_buckets(timeframe: str, buckets: list[dict], *, progress: bool) -> list[dict]:
    buckets_by_ticker: dict[str, list[dict]] = {}
    for bucket in buckets:
        buckets_by_ticker.setdefault(str(bucket["ticker"]), []).append(bucket)
    tickers = sorted(buckets_by_ticker)
    iterator = tickers
    if progress and tqdm is not None and tickers:
        iterator = tqdm(tickers, desc=f"{timeframe} inspect tickers", unit="ticker")
    rows: list[dict] = []
    for ticker in iterator:
        for bucket in buckets_by_ticker[ticker]:
            bucket_rows = rows_for_bucket(
                timeframe,
                ticker,
                bucket["bucket_start"],
                bool(bucket["dividend_adjusted"]),
            )
            rows.extend(bucket_rows[1:])
    return rows


def delete_rows(rows: list[dict]) -> int:
    if not rows:
        return 0
    sql = text(
        """
        DELETE FROM candles
        WHERE ticker = :ticker
            AND timeframe = CAST(:timeframe AS candle_timeframe)
            AND timestamp = :timestamp
            AND dividend_adjusted = :dividend_adjusted
        """
    )
    deleted = 0
    with engine.begin() as conn:
        for row in rows:
            result = conn.execute(
                sql,
                {
                    "ticker": row["ticker"],
                    "timeframe": row["timeframe"],
                    "timestamp": row["timestamp"],
                    "dividend_adjusted": bool(row["dividend_adjusted"]),
                },
            )
            deleted += int(result.rowcount or 0)
    return deleted


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Audit and remove duplicate coarse-timeframe candle rows by normalized UTC bucket."
    )
    parser.add_argument("--timeframe", default="all", help="all, D1, or W1")
    parser.add_argument("--ticker", default="", help="Optional ticker filter, e.g. SPY")
    parser.add_argument("--limit", type=int, default=50, help="Rows to print per timeframe")
    parser.add_argument("--apply", action="store_true", help="Delete invalid duplicate rows")
    parser.add_argument("--skip-profile", action="store_true", help="Do not print timestamp-hour profile")
    parser.add_argument("--no-progress", action="store_true", help="Disable tqdm progress bars")
    parser.add_argument("--create-indexes", action="store_true", help="Create cleanup helper indexes first")
    args = parser.parse_args(argv)

    if engine.dialect.name != "postgresql":
        raise SystemExit("cleanup_candle_duplicates.py requires PostgreSQL")

    if args.create_indexes:
        print("creating helper indexes concurrently if needed")
        create_indexes()

    if not args.skip_profile:
        print_timestamp_profile()

    ticker = str(args.ticker or "").strip().upper()
    total_deleted = 0
    total_invalid = 0
    progress = not bool(args.no_progress)

    for timeframe in _timeframes(str(args.timeframe or "all")):
        buckets = duplicate_buckets(timeframe, ticker)
        invalid = sum(int(bucket["bucket_rows"]) - 1 for bucket in buckets)
        print(f"{timeframe}: duplicate buckets={len(buckets)} invalid duplicate rows={invalid}")
        rows = invalid_rows_for_buckets(timeframe, buckets, progress=progress)
        total_invalid += len(rows)
        for row in rows[: max(0, int(args.limit))]:
            print(
                f"  remove ticker={row['ticker']} dividend_adjusted={row['dividend_adjusted']} "
                f"bucket={row['bucket_start']} "
                f"timestamp={row['timestamp']} o={row['open']} h={row['high']} "
                f"l={row['low']} c={row['close']} v={row['volume']}"
            )
        if len(rows) > max(0, int(args.limit)):
            print(f"  ... {len(rows) - max(0, int(args.limit))} more rows not shown")
        if args.apply and rows:
            deleted = delete_rows(rows)
            total_deleted += deleted
            print(f"{timeframe}: deleted={deleted}")

    if args.apply:
        print(f"deleted total={total_deleted}")
    else:
        print(f"dry run only; pass --apply to delete {total_invalid} invalid rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
