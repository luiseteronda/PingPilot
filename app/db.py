import os, re
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from sqlmodel import SQLModel, Field, create_engine, select, Session
from sqlalchemy import Column, Float

# --- SQLite engine ---
engine = create_engine("sqlite:///pingpilot.db", connect_args={"check_same_thread": False})

# --- PRAGMAs ---
with engine.connect() as conn:
    conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
    conn.exec_driver_sql("PRAGMA synchronous=NORMAL;")
    conn.exec_driver_sql("PRAGMA foreign_keys=ON;")

# ---------- Models ----------
class Monitor(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    url: str
    selector: str = ""
    render_js: bool = False
    frequency_minutes: int = 60
    last_content_hash: str = ""
    last_image_hash: str = ""
    last_checked_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = True
    last_text: str = ""
    ignore_robots: bool = False
    last_blocks_json: str = "[]"

class CheckResult(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    monitor_id: int = Field(foreign_key="monitor.id")
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status_code: int = 0
    blocked_reason: str = ""
    raw_text_len: int = 0
    norm_text_len: int = 0
    text_change_ratio: float = Field(
        default=0.0,
        sa_column=Column("change_ratio", Float, nullable=False, server_default="0")
    )
    changed_text: bool = False
    changed_visual: bool = False
    diff_preview: str = ""
    visual_hamming: int = 0
    material_change: bool = False
    severity: str = "none"
    semantic_summary: str = ""
    llm_json: str = ""
    changes_json: str = "[]"
    note: str = ""

def create_db():
    SQLModel.metadata.create_all(engine)
    # safe boot-time migrations
    with engine.connect() as c:
        for stmt in [
            "ALTER TABLE monitor ADD COLUMN selector TEXT DEFAULT ''",
            "ALTER TABLE monitor ADD COLUMN last_text TEXT DEFAULT ''",
            "ALTER TABLE monitor ADD COLUMN last_blocks_json TEXT DEFAULT '[]'",
            "ALTER TABLE monitor ADD COLUMN last_content_hash TEXT DEFAULT ''",
            "ALTER TABLE monitor ADD COLUMN last_image_hash TEXT DEFAULT ''",
            "ALTER TABLE checkresult ADD COLUMN status_code INTEGER DEFAULT 0",
            "ALTER TABLE checkresult ADD COLUMN blocked_reason TEXT DEFAULT ''",
            "ALTER TABLE checkresult ADD COLUMN raw_text_len INTEGER DEFAULT 0",
            "ALTER TABLE checkresult ADD COLUMN norm_text_len INTEGER DEFAULT 0",
            "ALTER TABLE checkresult ADD COLUMN text_change_ratio FLOAT DEFAULT 0.0",
            "ALTER TABLE checkresult ADD COLUMN visual_hamming INTEGER DEFAULT 0",
            "ALTER TABLE checkresult ADD COLUMN material_change BOOLEAN DEFAULT 0",
            "ALTER TABLE checkresult ADD COLUMN severity TEXT DEFAULT 'none'",
            "ALTER TABLE checkresult ADD COLUMN semantic_summary TEXT DEFAULT ''",
            "ALTER TABLE checkresult ADD COLUMN llm_json TEXT DEFAULT ''",
            "ALTER TABLE checkresult ADD COLUMN changes_json TEXT DEFAULT '[]'",
            "ALTER TABLE checkresult ADD COLUMN note TEXT DEFAULT ''"
            "ALTER TABLE monitor ADD COLUMN render_js INTEGER DEFAULT 0;",
            "ALTER TABLE monitor ADD COLUMN wait_selector TEXT;",
            "ALTER TABLE monitor ADD COLUMN timeout_ms INTEGER;"
,
        ]:
            try: c.exec_driver_sql(stmt)
            except Exception: pass

def cleanup_old_results(days: int = 30):
    from sqlmodel import select
    print("[Cleanup] Starting old results cleanup...")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with Session(engine) as session:
        old = session.exec(select(CheckResult).where(CheckResult.checked_at < cutoff)).all()
        for r in old: session.delete(r)
        session.commit()
    with engine.connect() as conn:
        conn.exec_driver_sql("VACUUM;")
    print("[Cleanup] Done.")
