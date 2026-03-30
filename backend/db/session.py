from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker


DB_PATH = Path(__file__).resolve().parent / "db.sqlite"
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def ensure_strategy_columns(eng: Engine) -> None:
    with eng.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(strategy)")).fetchall()
        colnames = {r[1] for r in rows}
        if "status" not in colnames:
            conn.execute(
                text("ALTER TABLE strategy ADD COLUMN status VARCHAR(32) NOT NULL DEFAULT 'success'")
            )
        if "status_text" not in colnames:
            conn.execute(
                text("ALTER TABLE strategy ADD COLUMN status_text VARCHAR(512) NOT NULL DEFAULT ''")
            )
