from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

try:
    import dotenv

    dotenv.load_dotenv(_BACKEND_ROOT / ".env")
except Exception:
    pass

from alpaca.data.live.crypto import CryptoDataStream
from alpaca.data.live.stock import StockDataStream

from application.queries.historical_bars import infer_asset_class
from application.services.alpaca_live_db import LiveSubscriptionSpec, read_active_subscriptions
from application.services.backtest_data import normalize_crypto_symbol
from db.models import AlpacaLiveEvent, LiveRunEvent
from db.session import SessionLocal

logger = logging.getLogger('alpaca_live_listener.py')


def _require_env(name: str) -> str:
    v = (os.environ.get(name) or "").strip()
    if not v:
        raise RuntimeError(f"{name} must be set")
    return v


def _bar_to_event_payload(bar: object) -> tuple[str, int, dict]:
    sym = str(getattr(bar, "symbol", "") or getattr(bar, "S", "") or "").strip().upper()
    ts = getattr(bar, "timestamp", None) or getattr(bar, "t", None)
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            ts = None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ut = int(ts.timestamp())
        iso = ts.astimezone(timezone.utc).isoformat()
    else:
        ut = int(time.time())
        iso = datetime.fromtimestamp(ut, tz=timezone.utc).isoformat()
    payload = {
        "symbol": sym,
        "o": float(getattr(bar, "open", 0.0) or getattr(bar, "o", 0.0) or 0.0),
        "h": float(getattr(bar, "high", 0.0) or getattr(bar, "h", 0.0) or 0.0),
        "l": float(getattr(bar, "low", 0.0) or getattr(bar, "l", 0.0) or 0.0),
        "c": float(getattr(bar, "close", 0.0) or getattr(bar, "c", 0.0) or 0.0),
        "v": float(getattr(bar, "volume", 0.0) or getattr(bar, "v", 0.0) or 0.0),
        "t": iso,
        "closed": True,
    }
    return sym, ut, payload


def _symbols_match(left: str, right: str) -> bool:
    a = str(left or "").strip().upper()
    b = str(right or "").strip().upper()
    if a == b:
        return True
    return normalize_crypto_symbol(a) == normalize_crypto_symbol(b)


def _split_symbols_by_asset(symbols: list[str], *, session) -> tuple[list[str], list[str]]:
    stocks: list[str] = []
    cryptos: list[str] = []
    for raw in symbols:
        sym = str(raw or "").strip().upper()
        if not sym:
            continue
        asset = infer_asset_class(sym, provider="alpaca", session=session)
        if asset == "crypto" or (asset is None and "/" in sym):
            cryptos.append(normalize_crypto_symbol(sym))
        else:
            stocks.append(sym)
    return sorted(dict.fromkeys(stocks)), sorted(dict.fromkeys(cryptos))


class _Listener:
    def __init__(
        self,
        *,
        subs_ttl_s: float,
        poll_subs_s: float,
        max_symbols: int,
    ) -> None:
        self.subs_ttl_s = float(subs_ttl_s)
        self.poll_subs_s = float(poll_subs_s)
        self.max_symbols = int(max_symbols)
        self._stop = threading.Event()

        self._streams: list[StockDataStream | CryptoDataStream] = []
        self._stream_threads: list[threading.Thread] = []

        self._active_symbols: list[str] = []
        self._active_subs: list[LiveSubscriptionSpec] = []
        self._lock = threading.Lock()

    def request_stop(self) -> None:
        self._stop.set()
        self._stop_stream()

    def _stop_stream(self) -> None:
        streams = list(self._streams)
        self._streams = []
        self._stream_threads = []
        for s in streams:
            try:
                s.stop()
            except Exception:
                pass

    def _start_stream(self, stock_symbols: list[str], crypto_symbols: list[str]) -> None:
        if not stock_symbols and not crypto_symbols:
            return
        key = _require_env("ALPACA_API_KEY")
        secret = _require_env("ALPACA_SECRET_KEY")

        async def on_bar(bar):
            try:
                sym, ut, payload = _bar_to_event_payload(bar)
                with self._lock:
                    active_subs = list(self._active_subs)
                matching_run_ids = sorted(
                    {
                        s.run_id
                        for s in active_subs
                        if s.run_id
                        and s.channel == "bars"
                        and _symbols_match(s.symbol, sym)
                        and s.scale == "1m"
                    }
                )
                with SessionLocal() as session:
                    raw = AlpacaLiveEvent(
                        channel="bars",
                        symbol=sym,
                        scale="1m",
                        unixtime=ut,
                        payload=payload,
                    )
                    session.add(raw)
                    session.flush()
                    for run_id in matching_run_ids:
                        event_payload = dict(payload)
                        event_payload["alpaca_live_event_id"] = int(raw.id)
                        session.add(
                            LiveRunEvent(
                                run_id=run_id,
                                event_type="input",
                                kind="market_bar",
                                unixtime=ut,
                                payload=event_payload,
                            )
                        )
                    session.commit()
                logger.info("bar %s %s runs=%s", sym, payload.get("t", ""), len(matching_run_ids))
            except Exception:
                logger.exception("failed to write bar event")

        streams: list[tuple[str, StockDataStream | CryptoDataStream, list[str]]] = []
        if stock_symbols:
            streams.append(("stock", StockDataStream(api_key=key, secret_key=secret), stock_symbols))
        if crypto_symbols:
            streams.append(("crypto", CryptoDataStream(api_key=key, secret_key=secret), crypto_symbols))

        def _run(label: str, stream: StockDataStream | CryptoDataStream) -> None:
            try:
                stream.run()
            except Exception:
                logger.exception("alpaca %s stream failed", label)

        self._streams = [stream for _, stream, _ in streams]
        self._stream_threads = []
        for label, stream, symbols in streams:
            stream.subscribe_bars(on_bar, *symbols)
            thread = threading.Thread(target=_run, args=(label, stream), daemon=True)
            self._stream_threads.append(thread)
            thread.start()

    def _desired_subscriptions_from_db(self) -> tuple[tuple[list[str], list[str]], list[LiveSubscriptionSpec]]:
        with SessionLocal() as session:
            subs = read_active_subscriptions(
                session,
                max_age_seconds=self.subs_ttl_s,
            )
            bars = [s for s in subs if (s.channel or "").strip().lower() == "bars"]
            syms = sorted({(s.symbol or "").strip().upper() for s in bars if (s.symbol or "").strip()})
            if self.max_symbols > 0:
                syms = syms[: self.max_symbols]
                allowed = set(syms)
                bars = [s for s in bars if s.symbol in allowed]
            return _split_symbols_by_asset(syms, session=session), bars

    def serve_forever(self) -> None:
        last_symbols: tuple[list[str], list[str]] = ([], [])
        while not self._stop.is_set():
            desired, subs = self._desired_subscriptions_from_db()
            with self._lock:
                self._active_subs = list(subs)
            if desired != last_symbols:
                stock_symbols, crypto_symbols = desired
                logger.info(
                    "subscriptions changed stock_bars=%s crypto_bars=%s stock_sample=%s crypto_sample=%s",
                    len(stock_symbols),
                    len(crypto_symbols),
                    stock_symbols[:10],
                    crypto_symbols[:10],
                )
                self._stop_stream()
                self._start_stream(stock_symbols, crypto_symbols)
                last_symbols = desired
                with self._lock:
                    self._active_symbols = list(stock_symbols) + list(crypto_symbols)
            time.sleep(max(0.25, self.poll_subs_s))


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        stream=sys.stderr,
    )
    p = argparse.ArgumentParser(
        description="Listen to Alpaca market data websocket, subscribe based on DB registry, and append events to alpaca_live_events."
    )
    p.add_argument("--subs-ttl-s", type=float, default=60.0, help="Subscription row TTL.")
    p.add_argument("--poll-subs-s", type=float, default=2.0, help="How often to recompute union subscriptions.")
    p.add_argument("--max-symbols", type=int, default=0, help="Optional cap on subscribed symbols (0=no cap).")
    args = p.parse_args(argv)

    listener = _Listener(
        subs_ttl_s=float(args.subs_ttl_s),
        poll_subs_s=float(args.poll_subs_s),
        max_symbols=int(args.max_symbols),
    )

    logger.info(
        "alpaca_live_listener start subs_ttl_s=%s poll_subs_s=%s max_symbols=%s",
        float(args.subs_ttl_s),
        float(args.poll_subs_s),
        int(args.max_symbols),
    )
    try:
        listener.serve_forever()
        return 0
    except KeyboardInterrupt:
        logger.info("received KeyboardInterrupt, stopping")
        listener.request_stop()
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

