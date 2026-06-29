from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import List, Optional

_DB_PATH = Path.home() / ".urban_renewal_audit.db"

_NEW_COLUMNS = [
    ("submission_type", "TEXT"),
    ("bonus_pct",       "REAL"),
    ("critical_count",  "INTEGER"),
    ("high_count",      "INTEGER"),
    ("warn_count",      "INTEGER"),
    ("parking_pass",    "INTEGER"),   # 1 = pass, 0 = fail, NULL = unknown
    ("pii_high_count",  "INTEGER"),
]


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(_DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def _migrate(c: sqlite3.Connection) -> None:
    """Add new metric columns if they don't exist yet (safe to run every startup)."""
    existing = {row[1] for row in c.execute("PRAGMA table_info(runs)")}
    for col_name, col_type in _NEW_COLUMNS:
        if col_name not in existing:
            c.execute(f"ALTER TABLE runs ADD COLUMN {col_name} {col_type}")


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
        _migrate(c)


def save_run(case_name: str, audit_time: str, rule_ver: str, findings_json: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO runs (case_name, audit_time, rule_ver, findings) VALUES (?,?,?,?)",
            (case_name, audit_time, rule_ver, findings_json),
        )


def save_run_metrics(
    case_name: str,
    submission_type: Optional[str],
    bonus_pct: Optional[float],
    critical_count: int,
    high_count: int,
    warn_count: int,
    parking_pass: Optional[int],
    pii_high_count: int,
) -> None:
    """Update the most-recent run row for case_name with comparison metrics."""
    with _conn() as c:
        c.execute(
            """UPDATE runs SET
                submission_type = ?,
                bonus_pct       = ?,
                critical_count  = ?,
                high_count      = ?,
                warn_count      = ?,
                parking_pass    = ?,
                pii_high_count  = ?
               WHERE id = (
                   SELECT id FROM runs WHERE case_name = ? ORDER BY id DESC LIMIT 1
               )""",
            (
                submission_type, bonus_pct,
                critical_count, high_count, warn_count,
                parking_pass, pii_high_count,
                case_name,
            ),
        )


def get_peer_stats(submission_type: Optional[str]) -> Optional[dict]:
    """Return aggregate stats for all runs with the given submission_type.

    Returns None when fewer than 2 peers exist (not enough for meaningful comparison).
    """
    if not submission_type:
        return None
    with _conn() as c:
        rows = c.execute(
            """SELECT
                submission_type, bonus_pct, critical_count, high_count,
                warn_count, parking_pass, pii_high_count
               FROM runs
               WHERE submission_type = ?
                 AND critical_count IS NOT NULL
               ORDER BY id DESC""",
            (submission_type,),
        ).fetchall()
    if len(rows) < 2:
        return None

    def avg(vals):
        clean = [v for v in vals if v is not None]
        return round(sum(clean) / len(clean), 1) if clean else None

    bonus_vals   = [r["bonus_pct"]      for r in rows]
    critical_vals= [r["critical_count"] for r in rows]
    high_vals    = [r["high_count"]     for r in rows]
    warn_vals    = [r["warn_count"]     for r in rows]
    pii_vals     = [r["pii_high_count"] for r in rows]
    parking_vals = [r["parking_pass"]   for r in rows if r["parking_pass"] is not None]

    return {
        "count":            len(rows),
        "submission_type":  submission_type,
        "avg_bonus_pct":    avg(bonus_vals),
        "avg_critical":     avg(critical_vals),
        "avg_high":         avg(high_vals),
        "avg_warn":         avg(warn_vals),
        "avg_pii_high":     avg(pii_vals),
        "parking_pass_rate": round(sum(parking_vals) / len(parking_vals), 2) if parking_vals else None,
    }


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
