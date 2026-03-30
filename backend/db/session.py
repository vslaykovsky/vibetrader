from __future__ import annotations

import uuid
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
            if has_status and has_status_text:
                src_cols = "thread_id, messages, canvas, status, status_text"
                dst_cols = src_cols
            elif has_status:
                src_cols = "thread_id, messages, canvas, status"
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
