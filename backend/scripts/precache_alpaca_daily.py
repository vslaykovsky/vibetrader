from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import dotenv

dotenv.load_dotenv()

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest

from application.queries.historical_bars import HistoricalBarsQuery
from strategies import utils

logger = logging.getLogger(__name__)


from tqdm import tqdm  # type: ignore


@dataclass(frozen=True)
class _Job:
    symbol: str
    scale: str
    start: date
    end: date
    asset_class: str


def _require_env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} must be set")
    return value


def _compute_window(*, years: int, end: date | None) -> tuple[date, date]:
    end_d = end or (date.today() - timedelta(days=1))
    start_d = end_d - timedelta(days=int(years) * 365)
    return start_d, end_d


def _list_alpaca_symbols(*, include_otc: bool, asset_class: str) -> list[str]:
    api_key = _require_env("ALPACA_API_KEY")
    secret_key = _require_env("ALPACA_SECRET_KEY")
    client = TradingClient(api_key=api_key, secret_key=secret_key, paper=True)
    asset = (asset_class or "").strip().lower()
    if asset == "crypto":
        req = GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.CRYPTO)
    else:
        req = GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY)
    assets = client.get_all_assets(req)
    logger.info("found %s assets", len(assets))
    syms: list[str] = []
    for a in assets:
        if not getattr(a, "tradable", False):
            continue
        symbol = getattr(a, "symbol", None)
        if symbol is None:
            continue
        if asset != "crypto" and (not include_otc) and str(getattr(a, "exchange", "")).upper() == "OTC":
            continue
        s = str(symbol).strip().upper()
        if asset == "crypto":
            s = utils.normalize_crypto_symbol(s)
        syms.append(s)

    syms = sorted({s for s in syms if s})
    logger.info("found %s symbols", len(syms))
    return syms


def _read_symbols_file(path: str | Path) -> list[str]:
    p = Path(path).expanduser().resolve()
    raw = p.read_text(encoding="utf-8")
    out: list[str] = []
    for line in raw.splitlines():
        s = (line or "").strip()
        if not s or s.startswith("#"):
            continue
        out.append(s.upper())
    return sorted({s for s in out if s})


def _write_symbols_file(path: str | Path, symbols: list[str]) -> None:
    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(f"{s}\n" for s in symbols), encoding="utf-8")


def _precache_one(job: _Job, *, provider: str, sleep_seconds: float) -> tuple[str, int]:
    if sleep_seconds > 0:
        time.sleep(float(sleep_seconds))

    q = HistoricalBarsQuery(cache_ttl_seconds=0.0)
    df = q.fetch(
        ticker=job.symbol,
        scale=job.scale,
        start=job.start,
        end=job.end,
        padding_days=0,
        provider=provider,
        asset_class=job.asset_class,
        drop_wide_spread_bars=False,
    )
    n = 0 if df is None else int(getattr(df, "shape", [0])[0] or 0)
    return job.symbol, n


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Precache last N years of candles for all Alpaca tickers into the DB-backed candles cache."
    )
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--end", default="", help="YYYY-MM-DD (default: yesterday)")
    parser.add_argument(
        "--timeframe",
        default="1d",
        choices=["1m", "15m", "1h", "4h", "1d", "1w"],
        help="Candle timeframe / scale to precache.",
    )
    parser.add_argument("--max-tickers", type=int, default=0, help="Limit tickers processed (0 = no limit)")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--sleep-seconds", type=float, default=0.0, help="Optional per-ticker delay")
    parser.add_argument("--include-otc", action="store_true")
    parser.add_argument("--provider", default="alpaca", choices=["alpaca", "auto"])
    parser.add_argument(
        "--asset-class",
        default="us_equity",
        choices=["us_equity", "crypto"],
        help="What to precache. crypto uses Alpaca crypto bars; us_equity uses stock bars.",
    )
    parser.add_argument(
        "--symbols-file",
        default="",
        help="Optional newline-delimited symbols file (skips Alpaca assets listing).",
    )
    parser.add_argument(
        "--symbols-out",
        default="",
        help="Optional path to write the resolved symbol list.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    end = date.fromisoformat(args.end) if str(args.end).strip() else None
    start_d, end_d = _compute_window(years=int(args.years), end=end)
    scale = str(args.timeframe).strip().lower()
    asset_class = str(args.asset_class).strip().lower() or "us_equity"
    if str(args.symbols_file).strip():
        symbols = _read_symbols_file(args.symbols_file)
        if asset_class == "crypto":
            symbols = [utils.normalize_crypto_symbol(s) for s in symbols]
    else:
        symbols = _list_alpaca_symbols(include_otc=bool(args.include_otc), asset_class=asset_class)

    if args.max_tickers and int(args.max_tickers) > 0:
        symbols = symbols[: int(args.max_tickers)]

    if str(args.symbols_out).strip():
        _write_symbols_file(args.symbols_out, symbols)

    logger.info(
        "precache plan symbols=%s scale=%s window=%s..%s workers=%s provider=%s asset_class=%s dry_run=%s",
        len(symbols),
        scale,
        start_d.isoformat(),
        end_d.isoformat(),
        int(args.workers),
        args.provider,
        asset_class,
        bool(args.dry_run),
    )
    if not symbols:
        logger.warning("no symbols returned from Alpaca assets endpoint")
        return 0

    if args.dry_run:
        for s in symbols[:20]:
            logger.info("dry-run symbol=%s", s)
        if len(symbols) > 20:
            logger.info("dry-run ... and %s more", len(symbols) - 20)
        return 0

    jobs = [
        _Job(symbol=s, scale=scale, start=start_d, end=end_d, asset_class=asset_class) for s in symbols
    ]
    ok = 0
    failed = 0
    total_rows = 0

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as ex:
        futs = [
            ex.submit(
                _precache_one,
                j,
                provider=str(args.provider),
                sleep_seconds=float(args.sleep_seconds),
            )
            for j in jobs
        ]
        bar = tqdm(total=len(futs), desc="precaching", unit="ticker")
        try:
            for fut in as_completed(futs):
                try:
                    sym, nrows = fut.result()
                except Exception:
                    for f in futs:
                        f.cancel()
                    raise

                ok += 1
                total_rows += int(nrows)
                bar.update(1)
                bar.set_postfix(ok=ok, failed=failed, rows=total_rows)
        finally:
            bar.close()

    elapsed = time.monotonic() - started
    logger.info(
        "done ok=%s failed=%s total_rows=%s elapsed_s=%.1f",
        ok,
        failed,
        total_rows,
        elapsed,
    )
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

