from __future__ import annotations

import uuid
import enum
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Enum,
    UniqueConstraint,
    Float,
    Boolean,
    BigInteger,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, validates


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Strategy(Base):
    __tablename__ = "strategy"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    thread_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_by_email: Mapped[str | None] = mapped_column(String(512), nullable=True)
    messages: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    messages_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    canvas: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    code: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="success")
    status_text: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    langsmith_trace: Mapped[str] = mapped_column(Text, nullable=False, default="")
    strategy_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    algorithm: Mapped[str] = mapped_column(Text, nullable=False, default="")
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        Index("ix_strategy_thread_created", "thread_id", "created_at"),
    )

    @validates("messages")
    def _set_messages_count(self, _key, value):
        if isinstance(value, list):
            self.messages_count = len(value)
        else:
            self.messages_count = 0
        return value

    def __str__(self):
        def _short(value):
            s = str(value)
            return s[:50] + ("…" if len(s) > 50 else "")
        return (
            f"<Strategy id={self.id} "
            f"thread_id={self.thread_id} "
            f"messages={_short(self.messages)} "
            f"canvas={_short(self.canvas)} "
            f"code={_short(self.code)!r} "
            f"status={self.status} "
            f"status_text={_short(self.status_text)!r} "
            f"created_at={self.created_at}>"
        )


class CandleTimeframe(str, enum.Enum):
    M1 = "1m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"


class Candle(Base):
    __tablename__ = "candles"

    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, nullable=False)
    ticker: Mapped[str] = mapped_column(String(32), primary_key=True, nullable=False)
    timeframe: Mapped[CandleTimeframe] = mapped_column(
        Enum(CandleTimeframe, name="candle_timeframe", native_enum=True, validate_strings=True),
        primary_key=True,
        nullable=False,
    )
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    __table_args__ = (
        UniqueConstraint("ticker", "timeframe", "timestamp", name="uq_candles_ticker_tf_ts"),
        Index("ix_candles_ticker_timeframe_timestamp", "ticker", "timeframe", "timestamp"),
    )


class AlpacaLiveSubscription(Base):
    __tablename__ = "alpaca_live_subscriptions"

    runner_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel: Mapped[str] = mapped_column(String(32), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    scale: Mapped[str] = mapped_column(String(16), nullable=False, default="1m")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )

    __table_args__ = (
        Index(
            "ix_alpaca_live_subscriptions_channel_symbol",
            "channel",
            "symbol",
        ),
    )


class AlpacaLiveEvent(Base):
    __tablename__ = "alpaca_live_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    scale: Mapped[str | None] = mapped_column(String(16), nullable=True)
    unixtime: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_alpaca_live_events_channel_created", "channel", "created_at"),
        Index("ix_alpaca_live_events_symbol_created", "symbol", "created_at"),
    )


class LiveRun(Base):
    __tablename__ = "live_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    thread_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_by_email: Mapped[str | None] = mapped_column(String(512), nullable=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="paper")  # paper|live
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    status_text: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    entry_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    deployed_from_run_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    alpaca_account_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    runner_backend: Mapped[str] = mapped_column(String(16), nullable=False, default="kubernetes")
    runner_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    last_input_event_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, index=True)

    __table_args__ = (
        Index("ix_live_runs_thread_created", "thread_id", "created_at"),
    )


class LiveRunEvent(Base):
    __tablename__ = "live_run_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("live_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    unixtime: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_live_run_events_run_id_id", "run_id", "id"),
    )


class LiveRunOrder(Base):
    __tablename__ = "live_run_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("live_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    client_order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    alpaca_order_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )

    __table_args__ = (
        UniqueConstraint("run_id", "client_order_id", name="uq_live_run_orders_run_client_id"),
        Index("ix_live_run_orders_run_created", "run_id", "created_at"),
    )
