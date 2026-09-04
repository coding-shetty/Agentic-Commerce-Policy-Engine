"""
app/audit_log.py

Append-only audit trail, backed by the same relational database as
everything else (see app/db_models.AuditEvent). Every security-sensitive
event in the system — agent request, policy decision, risk decision,
escalation, approval, payment attempt/result, idempotency conflict, auth
failure — lands a row here before or immediately after the action it
describes. Nothing here mutates or deletes existing rows.

Never log secrets, passwords, or payment credentials — only IDs, decisions,
reasons, and non-sensitive metadata.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.db_models import AuditEvent

logger = structlog.get_logger("audit")

_SENSITIVE_KEYS = {"password", "hashed_password", "razorpay_key_secret", "jwt_secret", "token", "authorization"}


def _scrub(metadata: dict[str, Any]) -> dict[str, Any]:
    return {k: ("***" if k.lower() in _SENSITIVE_KEYS else v) for k, v in metadata.items()}


def record(
    db: Session,
    event_type: str,
    *,
    request_id: str | None = None,
    transaction_id: str | None = None,
    agent_id: str | None = None,
    decision: str | None = None,
    reason: str | None = None,
    policy_version: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    clean_metadata = _scrub(metadata or {})
    event = AuditEvent(
        request_id=request_id,
        transaction_id=transaction_id,
        agent_id=agent_id,
        event_type=event_type,
        decision=decision,
        reason=reason,
        policy_version=policy_version,
        metadata_json=json.dumps(clean_metadata, default=str),
    )
    db.add(event)
    db.flush()

    logger.info(
        event_type,
        request_id=request_id,
        transaction_id=transaction_id,
        agent_id=agent_id,
        decision=decision,
        status=decision,
    )
    return event


def get_recent(db: Session, limit: int = 50) -> list[AuditEvent]:
    return (
        db.query(AuditEvent)
        .order_by(AuditEvent.timestamp.desc())
        .limit(limit)
        .all()
    )
