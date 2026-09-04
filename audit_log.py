"""
audit_log.py

Every single decision the policy engine makes gets written here — approved,
denied, or escalated — with the plain-English reason attached. This is what
your Next.js dashboard reads from, and it's the artifact that proves the
"visible audit trail" requirement isn't just a claim in your README.

Uses SQLite so the whole thing runs with zero external dependencies —
fine for a buildathon demo, trivial to swap for Postgres later.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime

from schemas import PolicyDecision

DB_PATH = "audit_trail.db"


def init_db():
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                action TEXT NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT NOT NULL,
                bound_hit TEXT,
                escalation_id TEXT,
                request_payload TEXT NOT NULL
            )
            """
        )


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def log_decision(agent_id: str, decision: PolicyDecision, request_payload: dict):
    """Called right after the policy engine returns a decision — no action
    is taken (money moves, discount applies) without first landing a row here."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO audit_log
                (timestamp, agent_id, action, decision, reason, bound_hit, escalation_id, request_payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(UTC).isoformat(),
                agent_id,
                decision.action.value,
                decision.decision.value,
                decision.reason,
                decision.bound_hit,
                decision.escalation_id,
                json.dumps(request_payload),
            ),
        )


def get_recent(limit: int = 50) -> list[dict]:
    """Powers the audit dashboard endpoint. Newest first."""
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
