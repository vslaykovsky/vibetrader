from __future__ import annotations

import uuid
import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker


DB_PATH = Path(__file__).resolve().parent / "db.sqlite"
DEFAULT_DATABASE_URL = URL.create(
    drivername="postgresql+psycopg",
    username="postgres",
    password=os.getenv("POSTGRES_PASSWORD", ""),
    host=os.getenv("POSTGRES_HOST", "localhost"),
    port=5432,
    database="postgres",
)
DATABASE_URL = os.getenv("DATABASE_URL", "").strip() or DEFAULT_DATABASE_URL

engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def ensure_strategy_columns(eng: Engine) -> None:
    if eng.dialect.name != "sqlite":
        return
    with eng.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(strategy)")).fetchall()
        if not rows:
            return
        colnames = {r[1] for r in rows}

        if "id" not in colnames:
            conn.execute(text("ALTER TABLE strategy RENAME TO strategy_old"))
            conn.execute(text(
                "CREATE TABLE strategy ("
                "  id VARCHAR(36) PRIMARY KEY,"
                "  thread_id VARCHAR(36) NOT NULL,"
                "  messages JSON NOT NULL DEFAULT '[]',"
                "  canvas JSON NOT NULL DEFAULT '{}',"
                "  code TEXT NOT NULL DEFAULT '',"
                "  status VARCHAR(32) NOT NULL DEFAULT 'success',"
                "  status_text VARCHAR(512) NOT NULL DEFAULT '',"
                "  created_at DATETIME NOT NULL DEFAULT (datetime('now'))"
                ")"
            ))
            conn.execute(text("CREATE INDEX ix_strategy_thread_id ON strategy (thread_id)"))
            conn.execute(text("CREATE INDEX ix_strategy_thread_created ON strategy (thread_id, created_at)"))
            old_cols = conn.execute(text("PRAGMA table_info(strategy_old)")).fetchall()
            old_colnames = {r[1] for r in old_cols}
            has_status = "status" in old_colnames
            has_status_text = "status_text" in old_colnames
            has_code = "code" in old_colnames
            if has_status and has_status_text and has_code:
                src_cols = "thread_id, messages, canvas, code, status, status_text"
                dst_cols = src_cols
            elif has_status and has_status_text:
                src_cols = "thread_id, messages, canvas, status, status_text"
                dst_cols = src_cols
            elif has_status and has_code:
                src_cols = "thread_id, messages, canvas, code, status"
                dst_cols = src_cols
            elif has_status:
                src_cols = "thread_id, messages, canvas, status"
                dst_cols = src_cols
            elif has_code:
                src_cols = "thread_id, messages, canvas, code"
                dst_cols = src_cols
            else:
                src_cols = "thread_id, messages, canvas"
                dst_cols = src_cols
            rows_old = conn.execute(text(f"SELECT {src_cols} FROM strategy_old")).fetchall()
            for row in rows_old:
                new_id = str(uuid.uuid4())
                vals = {"id": new_id}
                col_list = dst_cols.split(", ")
                for i, col in enumerate(col_list):
                    vals[col] = row[i]
                placeholders = ", ".join(f":{c}" for c in ["id"] + col_list)
                col_names = ", ".join(["id"] + col_list)
                conn.execute(text(f"INSERT INTO strategy ({col_names}) VALUES ({placeholders})"), vals)
            conn.execute(text("DROP TABLE strategy_old"))
            return

        if "status" not in colnames:
            conn.execute(
                text("ALTER TABLE strategy ADD COLUMN status VARCHAR(32) NOT NULL DEFAULT 'success'")
            )
        if "status_text" not in colnames:
            conn.execute(
                text("ALTER TABLE strategy ADD COLUMN status_text VARCHAR(512) NOT NULL DEFAULT ''")
            )
        if "created_at" not in colnames:
            conn.execute(
                text("ALTER TABLE strategy ADD COLUMN created_at DATETIME NOT NULL DEFAULT (datetime('now'))")
            )
        if "code" not in colnames:
            conn.execute(
                text("ALTER TABLE strategy ADD COLUMN code TEXT NOT NULL DEFAULT ''")
            )


def ensure_strategy_created_by_column(eng: Engine) -> None:
    from sqlalchemy import inspect

    insp = inspect(eng)
    if not insp.has_table("strategy"):
        return
    cols = {c["name"] for c in insp.get_columns("strategy")}
    if "created_by" in cols:
        return
    added = False
    with eng.begin() as conn:
        if "user_id" in cols:
            conn.execute(text("ALTER TABLE strategy RENAME COLUMN user_id TO created_by"))
        else:
            conn.execute(text("ALTER TABLE strategy ADD COLUMN created_by VARCHAR(255)"))
            added = True
    if added:
        with eng.begin() as conn:
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_strategy_created_by ON strategy (created_by)")
            )


def ensure_strategy_created_by_email_column(eng: Engine) -> None:
    from sqlalchemy import inspect

    insp = inspect(eng)
    if not insp.has_table("strategy"):
        return
    cols = {c["name"] for c in insp.get_columns("strategy")}
    if "created_by_email" in cols:
        return
    with eng.begin() as conn:
        conn.execute(text("ALTER TABLE strategy ADD COLUMN created_by_email VARCHAR(512)"))


def ensure_strategy_langsmith_trace_column(eng: Engine) -> None:
    from sqlalchemy import inspect

    insp = inspect(eng)
    if not insp.has_table("strategy"):
        return
    cols = {c["name"] for c in insp.get_columns("strategy")}
    if "langsmith_trace" in cols:
        return
    with eng.begin() as conn:
        conn.execute(text("ALTER TABLE strategy ADD COLUMN langsmith_trace TEXT NOT NULL DEFAULT ''"))


def ensure_strategy_strategy_name_column(eng: Engine) -> None:
    from sqlalchemy import inspect

    insp = inspect(eng)
    if not insp.has_table("strategy"):
        return
    cols = {c["name"] for c in insp.get_columns("strategy")}
    if "strategy_name" in cols:
        return
    with eng.begin() as conn:
        conn.execute(text("ALTER TABLE strategy ADD COLUMN strategy_name VARCHAR(512) NOT NULL DEFAULT ''"))


def ensure_strategy_algorithm_column(eng: Engine) -> None:
    from sqlalchemy import inspect

    insp = inspect(eng)
    if not insp.has_table("strategy"):
        return
    cols = {c["name"] for c in insp.get_columns("strategy")}
    if "algorithm" in cols:
        return
    with eng.begin() as conn:
        conn.execute(text("ALTER TABLE strategy ADD COLUMN algorithm TEXT NOT NULL DEFAULT ''"))


def ensure_strategy_language_column(eng: Engine) -> None:
    from sqlalchemy import inspect

    insp = inspect(eng)
    if not insp.has_table("strategy"):
        return
    cols = {c["name"] for c in insp.get_columns("strategy")}
    if "language" in cols:
        return
    with eng.begin() as conn:
        conn.execute(text("ALTER TABLE strategy ADD COLUMN language VARCHAR(8) NOT NULL DEFAULT ''"))


def ensure_strategy_messages_count_column(eng: Engine) -> None:
    from sqlalchemy import inspect

    insp = inspect(eng)
    if not insp.has_table("strategy"):
        return
    cols = {c["name"] for c in insp.get_columns("strategy")}
    if "messages_count" in cols:
        return
    with eng.begin() as conn:
        conn.execute(text("ALTER TABLE strategy ADD COLUMN messages_count INTEGER NOT NULL DEFAULT 0"))
        if "messages" not in cols:
            return
        if eng.dialect.name == "postgresql":
            conn.execute(
                text(
                    "UPDATE strategy SET messages_count = CASE "
                    "WHEN messages IS NULL THEN 0 "
                    "WHEN jsonb_typeof(messages::jsonb) = 'array' THEN jsonb_array_length(messages::jsonb) "
                    "ELSE 0 END"
                )
            )
        else:
            conn.execute(
                text(
                    "UPDATE strategy SET messages_count = CASE "
                    "WHEN messages IS NULL THEN 0 "
                    "WHEN json_type(messages) = 'array' THEN json_array_length(messages) "
                    "ELSE 0 END"
                )
            )
