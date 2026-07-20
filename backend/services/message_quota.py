from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


logger = logging.getLogger(__name__)

WINDOW_HOURS = 5
BUCKET_SECONDS = 60 * 60
KEY_PREFIX = "vibetrader:message_quota"

_CONSUME_SCRIPT = """
local limit = tonumber(ARGV[1])
local expires_at = tonumber(ARGV[2])
local counts = {}
local total = 0

for i, key in ipairs(KEYS) do
  local count = tonumber(redis.call('GET', key) or '0')
  counts[i] = count
  total = total + count
end

if total >= limit then
  local result = {0, total}
  for i = 1, #counts do result[#result + 1] = counts[i] end
  return result
end

local current = redis.call('INCR', KEYS[#KEYS])
redis.call('EXPIREAT', KEYS[#KEYS], expires_at)
counts[#counts] = current

local result = {1, total + 1}
for i = 1, #counts do result[#result + 1] = counts[i] end
return result
"""

_REFUND_SCRIPT = """
local count = tonumber(redis.call('GET', KEYS[1]) or '0')
if count <= 1 then
  redis.call('DEL', KEYS[1])
  return 0
end
return redis.call('DECR', KEYS[1])
"""


class MessageQuotaUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class MessageQuota:
    limit: int
    used: int
    remaining: int
    retry_at_epoch: int | None
    retry_after_seconds: int

    def to_dict(self) -> dict[str, Any]:
        retry_at = None
        if self.retry_at_epoch is not None:
            retry_at = (
                datetime.fromtimestamp(self.retry_at_epoch, tz=timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
        return {
            "limit": self.limit,
            "used": self.used,
            "remaining": self.remaining,
            "window_hours": WINDOW_HOURS,
            "bucket_seconds": BUCKET_SECONDS,
            "retry_at": retry_at,
            "retry_after_seconds": self.retry_after_seconds,
        }


@dataclass(frozen=True)
class MessageQuotaDecision:
    allowed: bool
    quota: MessageQuota
    current_bucket_key: str


def _redis_client():
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        raise MessageQuotaUnavailable("REDIS_URL is not configured")
    try:
        import redis

        return redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
        )
    except Exception as exc:
        raise MessageQuotaUnavailable("Redis client is unavailable") from exc


def _epoch_seconds(now: datetime | float | int | None) -> float:
    if now is None:
        return time.time()
    if isinstance(now, datetime):
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.timestamp()
    return float(now)


def _bucket_starts(now_epoch: float) -> list[int]:
    current = int(now_epoch // BUCKET_SECONDS) * BUCKET_SECONDS
    return [current - offset * BUCKET_SECONDS for offset in range(WINDOW_HOURS - 1, -1, -1)]


def _bucket_key(user_id: str, bucket_start: int) -> str:
    # The hash tag keeps all buckets for one user in one Redis Cluster slot.
    return f"{KEY_PREFIX}:{{{user_id}}}:{bucket_start}"


def _quota_from_counts(
    limit: int,
    counts: list[int],
    *,
    now_epoch: float,
    current_bucket_start: int,
) -> MessageQuota:
    normalized = [max(0, int(value)) for value in counts]
    used = sum(normalized)
    remaining = max(0, int(limit) - used)
    retry_at_epoch: int | None = None
    retry_after_seconds = 0

    if remaining == 0:
        for expired_bucket_count in range(1, WINDOW_HOURS + 1):
            future_used = sum(normalized[expired_bucket_count:])
            if future_used < limit:
                retry_at_epoch = current_bucket_start + expired_bucket_count * BUCKET_SECONDS
                retry_after_seconds = max(1, int(math.ceil(retry_at_epoch - now_epoch)))
                break

    return MessageQuota(
        limit=int(limit),
        used=used,
        remaining=remaining,
        retry_at_epoch=retry_at_epoch,
        retry_after_seconds=retry_after_seconds,
    )


def get_message_quota(
    user_id: str,
    limit: int,
    *,
    now: datetime | float | int | None = None,
    client=None,
) -> MessageQuota:
    uid = str(user_id or "").strip()
    if not uid or int(limit) < 1:
        raise ValueError("user_id and a positive message limit are required")
    now_epoch = _epoch_seconds(now)
    starts = _bucket_starts(now_epoch)
    keys = [_bucket_key(uid, start) for start in starts]
    redis_client = client or _redis_client()
    try:
        raw_counts = redis_client.mget(keys)
        counts = [int(value or 0) for value in raw_counts]
    except Exception as exc:
        logger.exception("failed to read message quota user_id=%s", uid)
        raise MessageQuotaUnavailable("Could not read message quota") from exc
    return _quota_from_counts(
        int(limit),
        counts,
        now_epoch=now_epoch,
        current_bucket_start=starts[-1],
    )


def consume_message_quota(
    user_id: str,
    limit: int,
    *,
    now: datetime | float | int | None = None,
    client=None,
) -> MessageQuotaDecision:
    uid = str(user_id or "").strip()
    if not uid or int(limit) < 1:
        raise ValueError("user_id and a positive message limit are required")
    now_epoch = _epoch_seconds(now)
    starts = _bucket_starts(now_epoch)
    keys = [_bucket_key(uid, start) for start in starts]
    redis_client = client or _redis_client()
    expires_at = starts[-1] + WINDOW_HOURS * BUCKET_SECONDS
    try:
        result = redis_client.eval(
            _CONSUME_SCRIPT,
            len(keys),
            *keys,
            int(limit),
            expires_at,
        )
        allowed = bool(int(result[0]))
        counts = [int(value or 0) for value in result[2 : 2 + WINDOW_HOURS]]
    except Exception as exc:
        logger.exception("failed to consume message quota user_id=%s", uid)
        raise MessageQuotaUnavailable("Could not update message quota") from exc

    quota = _quota_from_counts(
        int(limit),
        counts,
        now_epoch=now_epoch,
        current_bucket_start=starts[-1],
    )
    return MessageQuotaDecision(
        allowed=allowed,
        quota=quota,
        current_bucket_key=keys[-1],
    )


def refund_message_quota(decision: MessageQuotaDecision, *, client=None) -> None:
    if not decision.allowed or not decision.current_bucket_key:
        return
    try:
        redis_client = client or _redis_client()
        redis_client.eval(_REFUND_SCRIPT, 1, decision.current_bucket_key)
    except Exception:
        logger.exception("failed to refund message quota key=%s", decision.current_bucket_key)
