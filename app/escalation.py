"""
app/escalation.py

Storage layer for human-in-the-loop approval requests. Creation and
resolution of the *record* lives here; the actual continuation of the
transaction (resuming toward payment on approval, or finalizing as DENIED
on rejection) lives in app/orchestrator.py, since that's where the state
machine and payment layer are wired together.

Swapping the "print" for a real Slack/email webhook is a one-function
change (`notify_human`) — deliberately isolated so it's obvious where a
production integration would go.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db_models import ApprovalRequest


def notify_human(approval_id: str, transaction_id: str, reason: str) -> None:
    """Demo version: log it so it shows up as a pending item. Production
    version: POST to a Slack webhook / send an email with an Approve/Deny
    link that points at /approve/{id} and /reject/{id}."""
    print(f"[ESCALATION] approval={approval_id} transaction={transaction_id} reason={reason}")


def create(db: Session, transaction_id: str, reason: str, previous_status: str) -> ApprovalRequest:
    approval = ApprovalRequest(
        transaction_id=transaction_id,
        status="pending",
        reason=reason,
        previous_status=previous_status,
    )
    db.add(approval)
    db.flush()
    notify_human(approval.id, transaction_id, reason)
    return approval


def get(db: Session, approval_id: str) -> ApprovalRequest | None:
    return db.get(ApprovalRequest, approval_id)


def get_pending(db: Session, limit: int = 100) -> list[ApprovalRequest]:
    return (
        db.query(ApprovalRequest)
        .filter(ApprovalRequest.status == "pending")
        .order_by(ApprovalRequest.created_at.asc())
        .limit(limit)
        .all()
    )


def mark_resolved(db: Session, approval: ApprovalRequest, approve: bool, approver: str, resulting_status: str) -> None:
    approval.status = "approved" if approve else "rejected"
    approval.approver = approver
    approval.resulting_status = resulting_status
    approval.resolved_at = datetime.now(UTC)
    db.flush()
