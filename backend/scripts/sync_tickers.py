from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest
from moexalgo import Market
from moexalgo import session as moex_session
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session
from tqdm import tqdm

dotenv.load_dotenv()

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from application.services import backtest_data as utils
from db.models import Candle, CandleTimeframe, Ticker
from db.session import SessionLocal, engine, init_database

logger = logging.getLogger(__name__)

_ROOT = _BACKEND_DIR.parent
_DEFAULT_SNP500_PATH = _ROOT / "snp500.txt"
_PROVIDER_ALPACA = "alpaca"
_PROVIDER_MOEX = "moex"
_SNP500_TAG = "SNP500"
_STOCK_TAG = "stock"
_CRYPTO_TAG = "crypto"
_ASSET_CLASS_ALL = "all"
_ASSET_CLASS_US_EQUITY = "us_equity"
_ASSET_CLASS_CRYPTO = "crypto"


@dataclass(frozen=True, order=True)
class TickerRecord:
    ticker: str
    provider: str
    tags: tuple[str, ...] = ()


def _require_env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} must be set")
    return value


def _normalize_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _ordered_tags(tags: Iterable[str]) -> list[str]:
    values = {str(tag or "").strip() for tag in tags if str(tag or "").strip()}
    preferred = [_STOCK_TAG, _CRYPTO_TAG, _SNP500_TAG]
    return [tag for tag in preferred if tag in values] + sorted(values.difference(preferred))


def _progress(iterable, **kwargs):
    return tqdm(iterable, disable=not sys.stderr.isatty(), **kwargs)


def _read_symbols_file(path: str | Path) -> set[str]:
    p = Path(path).expanduser().resolve()
    raw = p.read_text(encoding="utf-8")
    symbols: set[str] = set()
    for line in raw.splitlines():
        value = _normalize_symbol(line)
        if value and not value.startswith("#"):
            symbols.add(value)
    return symbols


def _list_alpaca_symbols(*, include_otc: bool, asset_class: AssetClass) -> list[str]:
    api_key = _require_env("ALPACA_API_KEY")
    secret_key = _require_env("ALPACA_SECRET_KEY")
    client = TradingClient(api_key=api_key, secret_key=secret_key, paper=True)
    req = GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=asset_class)
    assets = client.get_all_assets(req)
    symbols: set[str] = set()
    desc = f"alpaca {asset_class.value}"
    for asset in _progress(assets, desc=desc, unit="asset"):
        if not getattr(asset, "tradable", False):
            continue
        symbol = _normalize_symbol(getattr(asset, "symbol", ""))
        if not symbol:
            continue
        if asset_class == AssetClass.US_EQUITY:
            exchange = _normalize_symbol(getattr(asset, "exchange", ""))
            if not include_otc and exchange == "OTC":
                continue
        if asset_class == AssetClass.CRYPTO:
            symbol = utils.normalize_crypto_symbol(symbol)
        symbols.add(symbol)
    return sorted(symbols)


def _alpaca_asset_classes(asset_class: str) -> tuple[AssetClass, ...]:
    value = str(asset_class or "").strip().lower() or _ASSET_CLASS_ALL
    if value == _ASSET_CLASS_ALL:
        return (AssetClass.US_EQUITY, AssetClass.CRYPTO)
    if value == _ASSET_CLASS_US_EQUITY:
        return (AssetClass.US_EQUITY,)
    if value == _ASSET_CLASS_CRYPTO:
        return (AssetClass.CRYPTO,)
    raise ValueError("asset_class must be one of: all, us_equity, crypto")


def _list_alpaca_records(*, include_otc: bool, asset_class: str = _ASSET_CLASS_ALL) -> list[TickerRecord]:
    records: list[TickerRecord] = []
    for cls in _alpaca_asset_classes(asset_class):
        symbols = _list_alpaca_symbols(include_otc=include_otc, asset_class=cls)
        tag = _CRYPTO_TAG if cls == AssetClass.CRYPTO else _STOCK_TAG
        records.extend(
            TickerRecord(ticker=s, provider=_PROVIDER_ALPACA, tags=(tag,))
            for s in symbols
        )
    return sorted(records)


def _list_moex_symbols(markets: Sequence[str]) -> list[str]:
    token = (os.environ.get("MOEX_API_KEY") or "").strip()
    if token:
        moex_session.TOKEN = token

    symbols: set[str] = set()
    for market in _progress(markets, desc="moex markets", unit="market"):
        data = Market(str(market).strip()).tickers("SECID")
        if hasattr(data, "to_dict"):
            rows = data.to_dict("records")
        else:
            rows = list(data)
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = _normalize_symbol(row.get("ticker") or row.get("SECID") or row.get("secid"))
            if symbol:
                symbols.add(symbol)
    return sorted(symbols)


def _list_moex_records(markets: Sequence[str]) -> list[TickerRecord]:
    return [
        TickerRecord(ticker=s, provider=_PROVIDER_MOEX, tags=(_STOCK_TAG,))
        for s in _list_moex_symbols(markets)
    ]


def _include_moex_for_asset_class(asset_class: str) -> bool:
    value = str(asset_class or "").strip().lower() or _ASSET_CLASS_ALL
    if value not in {_ASSET_CLASS_ALL, _ASSET_CLASS_US_EQUITY, _ASSET_CLASS_CRYPTO}:
        raise ValueError("asset_class must be one of: all, us_equity, crypto")
    return value in {_ASSET_CLASS_ALL, _ASSET_CLASS_US_EQUITY}


def _chunked(values: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for idx in range(0, len(values), size):
        yield values[idx : idx + size]


def _latest_day_volume_usd(session: Session, tickers: Iterable[str]) -> dict[str, float]:
    symbols = sorted({_normalize_symbol(t) for t in tickers if _normalize_symbol(t)})
    volume_usd: dict[str, float] = {}
    batches = list(_chunked(symbols, 900))
    for batch in _progress(batches, desc="loading volumes", unit="batch"):
        latest = (
            select(Candle.ticker.label("ticker"), func.max(Candle.timestamp).label("timestamp"))
            .where(Candle.timeframe == CandleTimeframe.D1, Candle.ticker.in_(batch))
            .group_by(Candle.ticker)
            .subquery()
        )
        rows = session.execute(
            select(Candle.ticker, Candle.close, Candle.volume).join(
                latest,
                and_(
                    Candle.ticker == latest.c.ticker,
                    Candle.timestamp == latest.c.timestamp,
                    Candle.timeframe == CandleTimeframe.D1,
                ),
            )
        ).all()
        for ticker, close, volume in rows:
            volume_usd[str(ticker)] = float(close) * float(volume)
    return volume_usd


def _sync_tickers(
    session: Session,
    records: Iterable[TickerRecord],
    snp500_symbols: set[str],
    *,
    updated_at: datetime | None = None,
) -> int:
    tags_by_key: dict[tuple[str, str], set[str]] = {}
    for record in records:
        ticker = _normalize_symbol(record.ticker)
        provider = str(record.provider or "").strip().lower()
        if not ticker or not provider:
            continue
        tags_by_key.setdefault((ticker, provider), set()).update(record.tags)
    unique = [
        TickerRecord(ticker=ticker, provider=provider, tags=tuple(sorted(tags)))
        for (ticker, provider), tags in sorted(tags_by_key.items())
    ]
    volume_usd = _latest_day_volume_usd(session, (r.ticker for r in unique))
    now = updated_at or datetime.now(timezone.utc)
    rows = []
    for record in unique:
        tags = set(record.tags)
        if record.ticker in snp500_symbols:
            tags.add(_SNP500_TAG)
        rows.append(
            {
                "ticker": record.ticker,
                "provider": record.provider,
                "tags": _ordered_tags(tags),
                "updated_at": now,
                "last_day_volume_usd": volume_usd.get(record.ticker),
            }
        )
    if rows and session.bind is not None and session.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        batch_size = 1_000
        batches = list(_chunked(rows, batch_size))
        for batch in _progress(batches, desc="upserting tickers", unit="batch"):
            stmt = pg_insert(Ticker).values(list(batch))
            stmt = stmt.on_conflict_do_update(
                index_elements=["ticker", "provider"],
                set_={
                    "tags": stmt.excluded.tags,
                    "updated_at": stmt.excluded.updated_at,
                    "last_day_volume_usd": stmt.excluded.last_day_volume_usd,
                },
            )
            session.execute(stmt)
    else:
        for row in _progress(rows, desc="upserting tickers", unit="ticker"):
            session.merge(Ticker(**row))
    session.commit()
    return len(unique)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Sync Alpaca stock/crypto and MOEX symbols into the tickers table."
    )
    parser.add_argument("--snp500-file", default=str(_DEFAULT_SNP500_PATH))
    parser.add_argument("--include-otc", action="store_true")
    parser.add_argument("--skip-alpaca", action="store_true")
    parser.add_argument("--skip-moex", action="store_true")
    parser.add_argument(
        "--asset-class",
        default=_ASSET_CLASS_ALL,
        choices=[_ASSET_CLASS_ALL, _ASSET_CLASS_US_EQUITY, _ASSET_CLASS_CRYPTO],
        help="Assets to sync. crypto syncs Alpaca crypto only.",
    )
    parser.add_argument(
        "--moex-market",
        action="append",
        default=[],
        help="MOEX market alias to download; repeatable. Defaults to shares.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    snp500_symbols = _read_symbols_file(args.snp500_file)
    moex_markets = args.moex_market or ["shares"]
    asset_class = str(args.asset_class).strip().lower() or _ASSET_CLASS_ALL
    records: list[TickerRecord] = []

    if not args.skip_alpaca:
        alpaca_records = _list_alpaca_records(
            include_otc=bool(args.include_otc),
            asset_class=asset_class,
        )
        records.extend(alpaca_records)
        logger.info("downloaded alpaca tickers=%s", len(alpaca_records))

    if not args.skip_moex and _include_moex_for_asset_class(asset_class):
        moex_records = _list_moex_records(moex_markets)
        records.extend(moex_records)
        logger.info("downloaded moex tickers=%s markets=%s", len(moex_records), ",".join(moex_markets))

    if args.dry_run:
        for record in sorted(set(records))[:25]:
            logger.info(
                "dry-run ticker=%s provider=%s tags=%s",
                record.ticker,
                record.provider,
                ",".join(record.tags),
            )
        if len(set(records)) > 25:
            logger.info("dry-run ... and %s more", len(set(records)) - 25)
        return 0

    init_database(engine)
    session = SessionLocal()
    try:
        synced = _sync_tickers(session, records, snp500_symbols)
    finally:
        session.close()

    logger.info("synced tickers=%s snp500_symbols=%s", synced, len(snp500_symbols))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
