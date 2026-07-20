from __future__ import annotations

import importlib.util
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import jwt

from db.models import Strategy
from db.session import SessionLocal
from services.message_quota import (
    MessageQuota,
    MessageQuotaDecision,
    consume_message_quota,
    get_message_quota,
    refund_message_quota,
)


class FakeRedis:
    def __init__(self):
        self.values: dict[str, int] = {}

    def mget(self, keys):
        return [self.values.get(key) for key in keys]

    def eval(self, script, num_keys, *args):
        keys = list(args[:num_keys])
        if "EXPIREAT" in script:
            limit = int(args[num_keys])
            counts = [self.values.get(key, 0) for key in keys]
            total = sum(counts)
            if total >= limit:
                return [0, total, *counts]
            self.values[keys[-1]] = self.values.get(keys[-1], 0) + 1
            counts[-1] = self.values[keys[-1]]
            return [1, total + 1, *counts]
        count = self.values.get(keys[0], 0)
        if count <= 1:
            self.values.pop(keys[0], None)
            return 0
        self.values[keys[0]] = count - 1
        return count - 1


def test_hourly_buckets_enforce_limit_and_report_exact_retry():
    redis = FakeRedis()
    now = datetime(2026, 7, 19, 12, 30, tzinfo=timezone.utc)

    decisions = [consume_message_quota("user-1", 5, now=now, client=redis) for _ in range(5)]
    assert all(decision.allowed for decision in decisions)
    assert decisions[-1].quota.to_dict() == {
        "limit": 5,
        "used": 5,
        "remaining": 0,
        "window_hours": 5,
        "bucket_seconds": 3600,
        "retry_at": "2026-07-19T17:00:00Z",
        "retry_after_seconds": 16200,
    }

    rejected = consume_message_quota("user-1", 5, now=now, client=redis)
    assert rejected.allowed is False
    assert rejected.quota.used == 5
    assert rejected.quota.remaining == 0


def test_oldest_hour_bucket_releases_capacity_at_next_boundary():
    redis = FakeRedis()
    prior = datetime(2026, 7, 19, 8, 5, tzinfo=timezone.utc)
    for _ in range(3):
        assert consume_message_quota("user-2", 5, now=prior, client=redis).allowed
    current = datetime(2026, 7, 19, 12, 40, tzinfo=timezone.utc)
    for _ in range(2):
        assert consume_message_quota("user-2", 5, now=current, client=redis).allowed

    quota = get_message_quota("user-2", 5, now=current, client=redis)
    assert quota.used == 5
    assert quota.remaining == 0
    assert quota.retry_at_epoch == int(datetime(2026, 7, 19, 13, 0, tzinfo=timezone.utc).timestamp())
    assert quota.retry_after_seconds == 20 * 60


def test_refund_removes_reserved_message_from_current_bucket():
    redis = FakeRedis()
    now = datetime(2026, 7, 19, 12, 30, tzinfo=timezone.utc)
    decision = consume_message_quota("user-3", 5, now=now, client=redis)
    assert decision.quota.used == 1

    refund_message_quota(decision, client=redis)

    assert get_message_quota("user-3", 5, now=now, client=redis).used == 0


_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("vibetrader_message_quota_app", _ROOT / "app.py")
assert _spec and _spec.loader
_flask = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_flask)
create_app = _flask.create_app


def _auth_headers(user_id: str) -> dict[str, str]:
    token = jwt.encode(
        {
            "sub": user_id,
            "email": "quota@example.com",
            "role": "authenticated",
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        os.environ["SUPABASE_JWT_SECRET"],
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def test_message_quota_endpoint_returns_current_usage(monkeypatch):
    previous_secret = os.environ.get("SUPABASE_JWT_SECRET")
    os.environ["SUPABASE_JWT_SECRET"] = "pytest-message-quota-secret-32-chars!!"
    quota = MessageQuota(limit=7, used=2, remaining=5, retry_at_epoch=None, retry_after_seconds=0)
    monkeypatch.setattr("api.routes.fetch_user_message_limit_5h", lambda _uid: 7)
    monkeypatch.setattr("api.routes.get_message_quota", lambda _uid, _limit: quota)
    try:
        response = create_app().test_client().get(
            "/message-quota",
            headers=_auth_headers("quota-status-user"),
        )
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert response.get_json() == {"quota": quota.to_dict()}
    finally:
        if previous_secret is None:
            os.environ.pop("SUPABASE_JWT_SECRET", None)
        else:
            os.environ["SUPABASE_JWT_SECRET"] = previous_secret


def test_post_strategy_returns_429_without_persisting_message(monkeypatch):
    previous_secret = os.environ.get("SUPABASE_JWT_SECRET")
    os.environ["SUPABASE_JWT_SECRET"] = "pytest-message-quota-secret-32-chars!!"
    user_id = f"quota-user-{uuid.uuid4()}"
    thread_id = str(uuid.uuid4())
    quota = MessageQuota(
        limit=5,
        used=5,
        remaining=0,
        retry_at_epoch=int(datetime(2026, 7, 19, 13, 0, tzinfo=timezone.utc).timestamp()),
        retry_after_seconds=1200,
    )
    decision = MessageQuotaDecision(False, quota, "unused")
    monkeypatch.setattr("api.routes.fetch_user_message_limit_5h", lambda _uid: 5)
    monkeypatch.setattr("api.routes.consume_message_quota", lambda _uid, _limit: decision)
    try:
        response = create_app().test_client().post(
            "/strategy",
            headers=_auth_headers(user_id),
            json={"thread_id": thread_id, "message": "blocked"},
        )
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "1200"
        assert response.get_json() == {
            "error": "Message limit reached.",
            "code": "message_limit_exceeded",
            "quota": quota.to_dict(),
        }
        session = SessionLocal()
        try:
            assert session.query(Strategy).filter(Strategy.thread_id == thread_id).count() == 0
        finally:
            session.close()
    finally:
        if previous_secret is None:
            os.environ.pop("SUPABASE_JWT_SECRET", None)
        else:
            os.environ["SUPABASE_JWT_SECRET"] = previous_secret
