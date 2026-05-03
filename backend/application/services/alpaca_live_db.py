from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from db.models import AlpacaLiveEvent, AlpacaLiveSubscription, LiveRun


@dataclass(frozen=True)
class LiveSubscriptionSpec:
    channel: str
    symbol: str
    scale: str = "1m"
    run_id: str = ""
    runner_id: str = ""


def upsert_runner_subscriptions(
    session: Session,
    *,
    run_id: str,
    runner_id: str,
    subs: list[LiveSubscriptionSpec],
    now: datetime | None = None,
) -> None:
    t = now or datetime.now(timezone.utc)
    desired = {
        (str(s.channel).strip().lower(), str(s.symbol).strip().upper(), str(s.scale).strip())
        for s in subs
    }
    existing = session.execute(
        select(AlpacaLiveSubscription).where(AlpacaLiveSubscription.runner_id == runner_id)
    ).scalars().all()

    existing_keys = {
        (str(r.channel).strip().lower(), str(r.symbol).strip().upper(), str(r.scale).strip())
        for r in existing
    }
    for r in existing:
        key = (str(r.channel).strip().lower(), str(r.symbol).strip().upper(), str(r.scale).strip())
        if key in desired:
            r.run_id = run_id
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
                run_id=run_id,
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


def read_active_subscriptions(
    session: Session,
    *,
    max_age_seconds: float = 60.0,
    now: datetime | None = None,
) -> list[LiveSubscriptionSpec]:
    t = now or datetime.now(timezone.utc)
    cutoff = t - timedelta(seconds=float(max_age_seconds))
    rows = (
        session.execute(
            select(AlpacaLiveSubscription).join(
                LiveRun,
                AlpacaLiveSubscription.run_id == LiveRun.id,
            ).where(
                AlpacaLiveSubscription.active.is_(True),
                AlpacaLiveSubscription.updated_at >= cutoff,
                LiveRun.status.in_(["starting", "running"]),
            )
        )
        .scalars()
        .all()
    )
    out: list[LiveSubscriptionSpec] = []
    for r in rows:
        out.append(
            LiveSubscriptionSpec(
                channel=str(r.channel).strip().lower(),
                symbol=str(r.symbol).strip().upper(),
                scale=str(r.scale).strip(),
                run_id=str(r.run_id or ""),
                runner_id=str(r.runner_id or ""),
            )
        )
    out.sort(key=lambda s: (s.channel, s.symbol, s.scale, s.run_id, s.runner_id))
    return out


def read_active_union_subscriptions(
    session: Session,
    *,
    max_age_seconds: float = 60.0,
    now: datetime | None = None,
) -> list[LiveSubscriptionSpec]:
    rows = read_active_subscriptions(
        session,
        max_age_seconds=max_age_seconds,
        now=now,
    )
    seen: set[tuple[str, str, str]] = set()
    out: list[LiveSubscriptionSpec] = []
    for r in rows:
        key = (r.channel, r.symbol, r.scale)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            LiveSubscriptionSpec(
                channel=r.channel,
                symbol=r.symbol,
                scale=r.scale,
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


def read_run_market_events_after(
    session: Session,
    *,
    run_id: str,
    after_id: int,
    limit: int = 500,
    through_id: int | None = None,
):
    from db.models import LiveRunEvent

    q = select(LiveRunEvent).where(
        LiveRunEvent.run_id == run_id,
        LiveRunEvent.event_type == "input",
        LiveRunEvent.kind == "market_bar",
        LiveRunEvent.id > int(after_id),
    )
    if through_id is not None:
        q = q.where(LiveRunEvent.id <= int(through_id))
    q = q.order_by(LiveRunEvent.id.asc()).limit(int(limit))
    return list(session.execute(q).scalars().all())


def read_run_strategy_inputs_after(
    session: Session,
    *,
    run_id: str,
    after_id: int,
    limit: int = 500,
):
    from db.models import LiveRunEvent

    q = (
        select(LiveRunEvent)
        .where(
            LiveRunEvent.run_id == run_id,
            LiveRunEvent.event_type == "input",
            LiveRunEvent.kind == "input",
            LiveRunEvent.id > int(after_id),
        )
        .order_by(LiveRunEvent.id.asc())
        .limit(int(limit))
    )
    return list(session.execute(q).scalars().all())

