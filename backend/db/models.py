from __future__ import annotations

from sqlalchemy import JSON, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Strategy(Base):
    __tablename__ = "strategy"

    thread_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    messages: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    canvas: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="success")
    status_text: Mapped[str] = mapped_column(String(512), nullable=False, default="")
