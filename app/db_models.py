"""
app/db_models.py

SQLAlchemy ORM models for the control plane. Money is always stored as an
integer number of minor currency units (paise for INR) — never a float —
to avoid precision errors on financial amounts. See app/money.py for the
conversion helpers used at the API boundary.

Tables:
    agents             - who is allowed to call the API (AGENT / ADMIN roles)
    transactions        - one row per discount/checkout/refund request, driven
                          through the state machine in app/state_machine.py
    idempotency_keys    - persistent idempotency store (never in-memory)
    audit_events        - append-only log of every security-sensitive event
    approval_requests   - human-in-the-loop escalation records
    payments            - payment-provider attempts tied to a transaction
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class Agent(Base):
    """A caller of the API — either an AI buyer agent or a human admin."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="AGENT")  # AGENT | ADMIN
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class Transaction(Base):
    """One row per policy-governed action (discount / checkout / refund),
    tracked through the explicit state machine defined in app/state_machine.py."""

    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    request_id: Mapped[str] = mapped_column(String(36), index=True, default=_uuid, nullable=False)
    agent_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    action_type: Mapped[str] = mapped_column(String(20), nullable=False)  # discount | checkout | refund

    sku: Mapped[str | None] = mapped_column(String(100), nullable=True)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit_price_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # discount percentage stored as an integer (pct * 100) to avoid float drift
    discount_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gross_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    net_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)

    status: Mapped[str] = mapped_column(String(30), index=True, nullable=False, default="CREATED")

    policy_decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    policy_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    policy_bound_hit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    policy_version: Mapped[str | None] = mapped_column(String(20), nullable=True)

    risk_level: Mapped[str | None] = mapped_column(String(10), nullable=True)  # LOW | MEDIUM | HIGH
    risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_factors: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list of strings

    idempotency_key: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    approval_requests: Mapped[list[ApprovalRequest]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )
    payments: Mapped[list[Payment]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )


class IdempotencyKey(Base):
    """Persistent idempotency store. Authoritative — never an in-memory dict.

    Same key + same payload hash -> return the original transaction/result.
    Same key + different payload hash -> reject as a conflict.
    """

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 hex of the payload
    transaction_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")  # pending | completed
    response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    """Append-only audit trail. Nothing in normal application flow updates
    or deletes rows here — only inserts."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    transaction_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    policy_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class ApprovalRequest(Base):
    """A human-in-the-loop escalation. Created when the policy engine
    returns ESCALATED; resolved by an ADMIN via /approve or /reject."""

    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    transaction_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("transactions.id"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")  # pending|approved|rejected
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    previous_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    resulting_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    approver: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    transaction: Mapped[Transaction] = relationship(back_populates="approval_requests")


class Payment(Base):
    """A payment-provider attempt tied to a transaction. Never created
    except by the orchestrator after policy has already approved the
    transaction (directly or via a resolved escalation)."""

    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    transaction_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("transactions.id"), index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)  # demo | razorpay
    provider_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="created")
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    raw_response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    transaction: Mapped[Transaction] = relationship(back_populates="payments")


class RateLimitBucket(Base):
    """DB-backed fixed-window rate limiter. Simple and reliable for a
    single-instance demo; document Redis for multi-instance production."""

    __tablename__ = "rate_limit_buckets"
    __table_args__ = (UniqueConstraint("agent_id", "window_start", name="uq_rate_limit_window"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
