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

from alpaca.data.live.stock import StockDataStream

from application.services.alpaca_live_db import read_active_union_subscriptions
from db.models import AlpacaLiveEvent
from db.session import SessionLocal

logger = logging.getLogger(__name__)


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

        self._stream: StockDataStream | None = None
        self._stream_thread: threading.Thread | None = None

        self._active_symbols: list[str] = []
        self._lock = threading.Lock()

    def request_stop(self) -> None:
        self._stop.set()
        self._stop_stream()

    def _stop_stream(self) -> None:
        s = self._stream
        self._stream = None
        if s is None:
            return
        try:
            s.stop()
        except Exception:
            pass

    def _start_stream(self, symbols: list[str]) -> None:
        key = _require_env("ALPACA_API_KEY")
        secret = _require_env("ALPACA_SECRET_KEY")
        stream = StockDataStream(api_key=key, secret_key=secret)

        async def on_bar(bar):
            try:
                sym, ut, payload = _bar_to_event_payload(bar)
                with SessionLocal() as session:
                    session.add(
                        AlpacaLiveEvent(
                            channel="bars",
                            symbol=sym,
                            scale="1m",
                            unixtime=ut,
                            payload=payload,
                        )
                    )
                    session.commit()
                logger.info("bar %s %s", sym, payload.get("t", ""))
            except Exception:
                logger.exception("failed to write bar event")

        if symbols:
            stream.subscribe_bars(on_bar, *symbols)

        def _run() -> None:
            try:
                stream.run()
            except Exception:
                logger.exception("alpaca stream failed")

        self._stream = stream
        self._stream_thread = threading.Thread(target=_run, daemon=True)
        self._stream_thread.start()

    def _desired_symbols_from_db(self) -> list[str]:
        with SessionLocal() as session:
            subs = read_active_union_subscriptions(
                session,
                max_age_seconds=self.subs_ttl_s,
            )
        bars = [s for s in subs if (s.channel or "").strip().lower() == "bars"]
        syms = sorted({(s.symbol or "").strip().upper() for s in bars if (s.symbol or "").strip()})
        if self.max_symbols > 0:
            syms = syms[: self.max_symbols]
        return syms

    def serve_forever(self) -> None:
        last_symbols: list[str] = []
        while not self._stop.is_set():
            desired = self._desired_symbols_from_db()
            if desired != last_symbols:
                logger.info(
                    "subscriptions changed bars=%s sample=%s",
                    len(desired),
                    desired[:10],
                )
                self._stop_stream()
                self._start_stream(desired)
                last_symbols = desired
                with self._lock:
                    self._active_symbols = list(desired)
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

