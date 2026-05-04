from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any


logger = logging.getLogger(__name__)

CHANNEL_PREFIX = "strategy_agent_stream"
BUFFER_PREFIX = "strategy_agent_stream_buffer"
DEFAULT_BUFFER_TTL_SECONDS = 60 * 60
DEFAULT_BUFFER_MAX_EVENTS = 2000


def _redis_client():
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        return None
    try:
        import redis
    except Exception:
        logger.exception("redis package is not available")
        return None
    try:
        return redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
        )
    except Exception:
        logger.exception("failed to create redis client")
        return None


def _channel(run_id: str) -> str:
    return f"{CHANNEL_PREFIX}:{run_id}"


def _buffer_key(run_id: str) -> str:
    return f"{BUFFER_PREFIX}:{run_id}"


def _buffer_ttl_seconds() -> int:
    raw = os.getenv("STRATEGY_STREAM_BUFFER_TTL_SECONDS", "").strip()
    try:
        return max(60, int(raw)) if raw else DEFAULT_BUFFER_TTL_SECONDS
    except ValueError:
        return DEFAULT_BUFFER_TTL_SECONDS


def _buffer_max_events() -> int:
    raw = os.getenv("STRATEGY_STREAM_BUFFER_MAX_EVENTS", "").strip()
    try:
        return max(100, int(raw)) if raw else DEFAULT_BUFFER_MAX_EVENTS
    except ValueError:
        return DEFAULT_BUFFER_MAX_EVENTS


def _delta_flush_interval_seconds() -> float | None:
    raw = os.getenv("STRATEGY_STREAM_DELTA_FLUSH_INTERVAL_SECONDS", "").strip()
    if not raw:
        return None
    try:
        return max(0.01, float(raw))
    except ValueError:
        return None


def _parse_event(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    try:
        seq = int(event.get("seq") or 0)
    except (TypeError, ValueError):
        return None
    kind = str(event.get("kind") or "").strip()
    run_id = str(event.get("run_id") or "").strip()
    if seq <= 0 or not kind or not run_id:
        return None
    event["seq"] = seq
    return event


class StrategyStreamPublisher:
    def __init__(self, run_id: str):
        self.run_id = str(run_id or "").strip()
        self.seq = 0
        self.client = _redis_client()
        self._lock = threading.Lock()
        self._pending_delta: list[str] = []
        self._delta_flush_interval = _delta_flush_interval_seconds()
        self._flush_event = threading.Event()
        self._stop_event = threading.Event()
        self._flush_thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def _next_seq(self) -> int:
        with self._lock:
            self.seq += 1
            return self.seq

    def _ensure_flush_thread(self) -> None:
        if self._flush_thread is not None or self.client is None or self._delta_flush_interval is None:
            return
        self._flush_thread = threading.Thread(
            target=self._flush_delta_loop,
            name=f"strategy-stream-flush-{self.run_id}",
            daemon=True,
        )
        self._flush_thread.start()

    def _flush_delta_loop(self) -> None:
        interval = self._delta_flush_interval
        if interval is None:
            return
        while not self._stop_event.is_set():
            self._flush_event.wait(interval)
            self._flush_event.clear()
            self.flush_deltas()
        self.flush_deltas()

    def flush_deltas(self) -> None:
        with self._lock:
            if not self._pending_delta:
                return
            delta = "".join(self._pending_delta)
            self._pending_delta = []
        self.publish("assistant_delta", {"delta": delta})

    def close(self) -> None:
        self._stop_event.set()
        self._flush_event.set()
        thread = self._flush_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._flush_thread = None

    def publish(self, kind: str, data: dict[str, Any] | None = None) -> None:
        kind = str(kind or "").strip()
        if not self.run_id or not kind or self.client is None:
            return
        event = {
            "run_id": self.run_id,
            "seq": self._next_seq(),
            "kind": kind,
            "unixtime": time.time(),
            "data": data if isinstance(data, dict) else {},
        }
        raw = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
        try:
            key = _buffer_key(self.run_id)
            pipe = self.client.pipeline()
            pipe.rpush(key, raw)
            pipe.ltrim(key, -_buffer_max_events(), -1)
            pipe.expire(key, _buffer_ttl_seconds())
            pipe.publish(_channel(self.run_id), raw)
            pipe.execute()
        except Exception:
            logger.exception("failed to publish strategy stream event")
            self.client = None

    def status(self, status_text: str) -> None:
        self.publish("agent_status", {"status_text": str(status_text or "")})

    def assistant_delta(self, delta: str) -> None:
        if not delta or self.client is None:
            return
        if self._delta_flush_interval is None:
            self.publish("assistant_delta", {"delta": str(delta)})
            return
        with self._lock:
            self._pending_delta.append(str(delta))
        self._ensure_flush_thread()

    def assistant_done(self) -> None:
        self.flush_deltas()
        self.publish("assistant_done", {})
        self.close()

    def error(self, message: str) -> None:
        self.flush_deltas()
        self.publish("agent_error", {"message": str(message or "")[:512]})
        self.close()


class StrategyStreamSubscriber:
    def __init__(self, run_id: str, after_seq: int = 0):
        self.run_id = str(run_id or "").strip()
        self.last_seq = max(0, int(after_seq or 0))
        self.client = _redis_client()
        self.pubsub = None
        self.buffered: list[dict[str, Any]] = []
        if not self.run_id or self.client is None:
            return
        try:
            self.pubsub = self.client.pubsub(ignore_subscribe_messages=True)
            self.pubsub.subscribe(_channel(self.run_id))
            self.buffered = self._read_buffered()
        except Exception:
            logger.exception("failed to subscribe to strategy stream events")
            self.close()

    def _read_buffered(self) -> list[dict[str, Any]]:
        if self.client is None:
            return []
        try:
            raw_events = self.client.lrange(_buffer_key(self.run_id), 0, -1)
        except Exception:
            logger.exception("failed to read strategy stream buffer")
            return []
        out: list[dict[str, Any]] = []
        for raw in raw_events:
            event = _parse_event(raw)
            if event is not None and int(event["seq"]) > self.last_seq:
                out.append(event)
        out.sort(key=lambda item: int(item.get("seq") or 0))
        return out

    def drain(self, timeout: float = 0.0) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        while self.buffered:
            event = self.buffered.pop(0)
            seq = int(event.get("seq") or 0)
            if seq > self.last_seq:
                self.last_seq = seq
                out.append(event)
        if self.pubsub is None:
            return out
        try:
            msg = self.pubsub.get_message(timeout=timeout)
            while msg is not None:
                event = _parse_event(msg.get("data"))
                if event is not None:
                    seq = int(event.get("seq") or 0)
                    if seq > self.last_seq:
                        self.last_seq = seq
                        out.append(event)
                msg = self.pubsub.get_message(timeout=0.0)
        except Exception:
            logger.exception("failed to read strategy stream event")
            self.close()
        return out

    def close(self) -> None:
        pubsub = self.pubsub
        self.pubsub = None
        if pubsub is None:
            return
        try:
            pubsub.close()
        except Exception:
            pass
