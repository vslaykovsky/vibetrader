from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text
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
