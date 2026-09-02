"""
escalation.py

When the policy engine returns Decision.ESCALATED, this is where that
gets routed to a human instead of silently blocking the agent. For the
buildathon demo this just logs + stores a pending-approval record, but
it's written so swapping in a real Slack webhook or email call is a
one-function change — that's worth saying explicitly in your pitch,
since it shows you scoped the demo deliberately rather than skipping
the human-in-the-loop step entirely.
"""

import sqlite3
from datetime import datetime, timezone
from audit_log import _connect

PENDING_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS pending_approvals (
    escalation_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    payload TEXT NOT NULL
)
"""


def init_pending_table():
    with _connect() as conn:
        conn.execute(PENDING_TABLE_SQL)


def route_to_human(escalation_id: str, payload: dict, reason: str):
    """
    Real version: POST to a Slack webhook / send an email with an
    Approve/Deny link. Demo version: write a pending row and print it,
    so the dashboard can show "1 item awaiting human approval" live.
    """
    import json

    with _connect() as conn:
        conn.execute(
            "INSERT INTO pending_approvals (escalation_id, created_at, status, payload) VALUES (?, ?, 'pending', ?)",
            (escalation_id, datetime.now(timezone.utc).isoformat(), json.dumps(payload)),
        )
    print(f"[ESCALATION] {escalation_id} routed to human review — reason: {reason}")


def resolve_approval(escalation_id: str, approve: bool) -> bool:
    """Called when a human clicks Approve/Deny on the escalation.
    Returns True if a matching pending record was found and updated."""
    status = "approved" if approve else "denied"
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE pending_approvals SET status = ? WHERE escalation_id = ? AND status = 'pending'",
            (status, escalation_id),
        )
        return cur.rowcount > 0
