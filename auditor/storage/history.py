from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import List, Optional

_DB_PATH = Path.home() / ".urban_renewal_audit.db"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(_DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                case_name  TEXT NOT NULL,
                audit_time TEXT NOT NULL,
                rule_ver   TEXT NOT NULL,
                findings   TEXT NOT NULL
            )
        """)


def save_run(case_name: str, audit_time: str, rule_ver: str, findings_json: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO runs (case_name, audit_time, rule_ver, findings) VALUES (?,?,?,?)",
            (case_name, audit_time, rule_ver, findings_json),
        )


def get_prev_run(case_name: str) -> Optional[dict]:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM runs WHERE case_name = ? ORDER BY id DESC LIMIT 1",
            (case_name,),
        ).fetchone()
    return dict(row) if row else None


def get_run_history(case_name: str, limit: int = 10) -> List[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, case_name, audit_time, rule_ver FROM runs WHERE case_name = ? ORDER BY id DESC LIMIT ?",
            (case_name, limit),
        ).fetchall()
    return [dict(r) for r in rows]
