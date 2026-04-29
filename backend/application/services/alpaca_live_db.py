from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from db.models import AlpacaLiveEvent, AlpacaLiveSubscription


@dataclass(frozen=True)
class LiveSubscriptionSpec:
    channel: str
    symbol: str
    scale: str = "1m"


def upsert_runner_subscriptions(
    session: Session,
    *,
    runner_id: str,
    subs: list[LiveSubscriptionSpec],
    now: datetime | None = None,
) -> None:
    t = now or datetime.now(timezone.utc)
    desired = {(s.channel, s.symbol, s.scale) for s in subs}
    existing = session.execute(
        select(AlpacaLiveSubscription).where(AlpacaLiveSubscription.runner_id == runner_id)
    ).scalars().all()

    existing_keys = {(r.channel, r.symbol, r.scale) for r in existing if r.active}
    for r in existing:
        if (r.channel, r.symbol, r.scale) in desired:
            r.active = True
            r.updated_at = t
        else:
            r.active = False
            r.updated_at = t

    missing = desired - existing_keys
    for channel, symbol, scale in sorted(missing):
        session.add(
            AlpacaLiveSubscription(
                runner_id=runner_id,
                channel=channel,
                symbol=symbol,
                scale=scale,
                active=True,
                updated_at=t,
            )
        )


def touch_runner_subscriptions(
    session: Session,
    *,
    runner_id: str,
    now: datetime | None = None,
) -> None:
    t = now or datetime.now(timezone.utc)
    rows = session.execute(
        select(AlpacaLiveSubscription).where(AlpacaLiveSubscription.runner_id == runner_id)
    ).scalars().all()
    for r in rows:
        if r.active:
            r.updated_at = t


def delete_runner_subscriptions(session: Session, *, runner_id: str) -> None:
    session.execute(
        delete(AlpacaLiveSubscription).where(AlpacaLiveSubscription.runner_id == runner_id)
    )


def prune_stale_subscriptions(
    session: Session,
    *,
    max_age_seconds: float = 60.0,
    now: datetime | None = None,
) -> int:
    t = now or datetime.now(timezone.utc)
    cutoff = t - timedelta(seconds=float(max_age_seconds))
    res = session.execute(
        delete(AlpacaLiveSubscription).where(AlpacaLiveSubscription.updated_at < cutoff)
    )
    return int(getattr(res, "rowcount", 0) or 0)


def read_active_union_subscriptions(
    session: Session,
    *,
    max_age_seconds: float = 60.0,
    now: datetime | None = None,
) -> list[LiveSubscriptionSpec]:
    t = now or datetime.now(timezone.utc)
    cutoff = t - timedelta(seconds=float(max_age_seconds))
    rows = (
        session.execute(
            select(AlpacaLiveSubscription).where(
                AlpacaLiveSubscription.active.is_(True),
                AlpacaLiveSubscription.updated_at >= cutoff,
            )
        )
        .scalars()
        .all()
    )
    seen: set[tuple[str, str, str]] = set()
    out: list[LiveSubscriptionSpec] = []
    for r in rows:
        key = (str(r.channel), str(r.symbol), str(r.scale))
        if key in seen:
            continue
        seen.add(key)
        out.append(
            LiveSubscriptionSpec(
                channel=str(r.channel),
                symbol=str(r.symbol),
                scale=str(r.scale),
            )
        )
    out.sort(key=lambda s: (s.channel, s.symbol, s.scale))
    return out


def read_events_after_id(
    session: Session,
    *,
    after_id: int,
    limit: int = 500,
) -> list[AlpacaLiveEvent]:
    q = (
        select(AlpacaLiveEvent)
        .where(AlpacaLiveEvent.id > int(after_id))
        .order_by(AlpacaLiveEvent.id.asc())
        .limit(int(limit))
    )
    return list(session.execute(q).scalars().all())

