from __future__ import annotations

import argparse
import json
import os
import queue
import statistics
import sys
import threading
import time
import uuid
import dotenv

dotenv.load_dotenv()


def _ms(seconds: float) -> float:
    return seconds * 1000.0


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    rows = sorted(values)
    idx = min(len(rows) - 1, max(0, round((len(rows) - 1) * pct)))
    return rows[idx]


def _format_latency(values: list[float]) -> str:
    if not values:
        return "n/a"
    return (
        f"avg={statistics.fmean(values):.2f}ms "
        f"p50={_percentile(values, 0.50):.2f}ms "
        f"p95={_percentile(values, 0.95):.2f}ms "
        f"p99={_percentile(values, 0.99):.2f}ms "
        f"max={max(values):.2f}ms"
    )


def _run_pubsub_benchmark(client, channel: str, count: int, payload_bytes: int, timeout: float) -> bool:
    payload = "x" * max(0, payload_bytes)
    pubsub = client.pubsub(ignore_subscribe_messages=False)
    received: queue.Queue[tuple[int, float] | Exception] = queue.Queue()
    ready = threading.Event()
    stop = threading.Event()
    publish_ms: list[float] = []
    roundtrip_ms: list[float] = []

    def subscriber() -> None:
        try:
            pubsub.subscribe(channel)
            deadline = time.monotonic() + timeout
            while not ready.is_set() and time.monotonic() < deadline:
                message = pubsub.get_message(timeout=0.1)
                if message and message.get("type") == "subscribe":
                    ready.set()
                    break
            while not stop.is_set():
                message = pubsub.get_message(timeout=0.1)
                if message is None or message.get("type") != "message":
                    continue
                try:
                    data = json.loads(message.get("data") or "{}")
                    received.put((int(data["seq"]), time.perf_counter()))
                except Exception as exc:
                    received.put(exc)
        except Exception as exc:
            received.put(exc)

    thread = threading.Thread(target=subscriber, name="redis-pubsub-benchmark", daemon=True)
    try:
        thread.start()
        if not ready.wait(timeout):
            print("PUB/SUB benchmark: subscriber did not become ready", file=sys.stderr)
            return False

        for seq in range(count):
            msg = json.dumps(
                {
                    "seq": seq,
                    "payload": payload,
                },
                separators=(",", ":"),
            )
            before = time.perf_counter()
            subscribers = client.publish(channel, msg)
            publish_ms.append(_ms(time.perf_counter() - before))
            if subscribers < 1:
                print("PUB/SUB benchmark: no subscribers reported by Redis", file=sys.stderr)
                return False

            try:
                item = received.get(timeout=timeout)
            except queue.Empty:
                print(f"PUB/SUB benchmark: timed out waiting for seq {seq}", file=sys.stderr)
                return False
            if isinstance(item, Exception):
                print(f"PUB/SUB benchmark subscriber failed: {type(item).__name__}: {item}", file=sys.stderr)
                return False
            got_seq, received_at = item
            if got_seq != seq:
                print(f"PUB/SUB benchmark: expected seq {seq}, got {got_seq}", file=sys.stderr)
                return False
            roundtrip_ms.append(_ms(received_at - before))

        print(f"PUB/SUB roundtrips: {len(roundtrip_ms)}/{count}")
        print(f"PUBLISH latency: {_format_latency(publish_ms)}")
        print(f"PUB/SUB roundtrip latency: {_format_latency(roundtrip_ms)}")
        return len(roundtrip_ms) == count
    finally:
        stop.set()
        pubsub.close()
        thread.join(timeout=1.0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Redis connectivity for REDIS_URL.")
    parser.add_argument("url", nargs="?", help="Redis URL. Defaults to REDIS_URL.")
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--pubsub-benchmark-count", type=int, default=100)
    parser.add_argument("--pubsub-payload-bytes", type=int, default=128)
    parser.add_argument(
        "--skip-pubsub-benchmark",
        action="store_true",
        help="Only run the single connectivity checks.",
    )
    args = parser.parse_args()

    url = (args.url or os.getenv("REDIS_URL") or "").strip()
    if not url:
        print("REDIS_URL is not set and no URL argument was provided.", file=sys.stderr)
        return 2

    try:
        import redis
    except Exception as exc:
        print(f"Could not import redis package: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    client = redis.Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=args.timeout,
        socket_timeout=args.timeout,
        health_check_interval=30,
    )
    key = f"vibetrader:redis_url_test:{uuid.uuid4().hex}"
    channel = f"{key}:channel"
    value = f"ok:{time.time()}"

    try:
        print(f"URL: {url}")
        print(f"PING: {client.ping()}")

        client.set(key, value, ex=30)
        got = client.get(key)
        print(f"SET/GET: {got == value}")
        if got != value:
            print(f"Expected {value!r}, got {got!r}", file=sys.stderr)
            return 1

        pubsub = client.pubsub(ignore_subscribe_messages=True)
        try:
            pubsub.subscribe(channel)
            client.publish(channel, value)
            deadline = time.monotonic() + args.timeout
            message = None
            while time.monotonic() < deadline:
                message = pubsub.get_message(timeout=0.1)
                if message is not None:
                    break
            print(f"PUB/SUB: {message is not None and message.get('data') == value}")
            if message is None or message.get("data") != value:
                print(f"Expected pubsub message {value!r}, got {message!r}", file=sys.stderr)
                return 1
        finally:
            pubsub.close()

        if not args.skip_pubsub_benchmark:
            ok = _run_pubsub_benchmark(
                client,
                f"{channel}:benchmark",
                max(1, args.pubsub_benchmark_count),
                max(0, args.pubsub_payload_bytes),
                max(args.timeout, 5.0),
            )
            if not ok:
                return 1

        client.delete(key)
        print("Redis URL test passed.")
        return 0
    except Exception as exc:
        print(f"Redis URL test failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
