import enum
from datetime import UTC, datetime

from sqlalchemy import JSON, Column, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from database import Base


class TransactionState(str, enum.Enum):
    CREATED = "CREATED"
    POLICY_CHECKING = "POLICY_CHECKING"
    APPROVED = "APPROVED"
    ESCALATED = "ESCALATED"
    HUMAN_APPROVED = "HUMAN_APPROVED"
    HUMAN_DENIED = "HUMAN_DENIED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAYMENT_CREATED = "PAYMENT_CREATED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    DENIED = "DENIED"

class Agent(Base):
    __tablename__ = "agents"
    
    id = Column(String, primary_key=True, index=True)
    secret_hash = Column(String, nullable=False)
    permissions = Column(JSON, default=list)
    status = Column(String, default="active")
    trust_level = Column(Integer, default=50)

class Transaction(Base):
    __tablename__ = "transactions"
    
    id = Column(String, primary_key=True, index=True)
    agent_id = Column(String, ForeignKey("agents.id"))
    state = Column(Enum(TransactionState), default=TransactionState.CREATED)
    request_type = Column(String, nullable=False)  # discount, checkout, refund
    request_payload = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
    
    agent = relationship("Agent")
    idempotency_key = relationship("IdempotencyKey", back_populates="transaction", uselist=False)

class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    
    idempotency_key = Column(String, primary_key=True, index=True)
    transaction_id = Column(String, ForeignKey("transactions.id"), nullable=False)
    agent_id = Column(String, ForeignKey("agents.id"), nullable=False)
    request_hash = Column(String, nullable=False)
    status = Column(String, nullable=False)
    response_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    expires_at = Column(DateTime, nullable=True)
    
    transaction = relationship("Transaction", back_populates="idempotency_key")
    agent = relationship("Agent")

class PolicyDecisionLog(Base):
    __tablename__ = "policy_decisions"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    transaction_id = Column(String, ForeignKey("transactions.id"), nullable=False)
    policy_version = Column(String, nullable=False)
    action = Column(String, nullable=False)
    decision = Column(String, nullable=False)
    reason = Column(String, nullable=False)
    bound_hit = Column(String, nullable=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(UTC))

class Escalation(Base):
    __tablename__ = "escalations"
    
    id = Column(String, primary_key=True, index=True)
    transaction_id = Column(String, ForeignKey("transactions.id"), nullable=False)
    status = Column(String, default="pending")  # pending, approved, denied
    reason = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(String, nullable=True)

class AuditEvent(Base):
    __tablename__ = "audit_events"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    request_id = Column(String, index=True)
    transaction_id = Column(String, index=True)
    agent_id = Column(String, index=True)
    actor = Column(String, nullable=False)  # agent, admin, system
    action = Column(String, nullable=False)
    decision = Column(String, nullable=True)
    reason = Column(String, nullable=True)
    policy_version = Column(String, nullable=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(UTC))
    request_hash = Column(String, nullable=True)
    approval_status = Column(String, nullable=True)
    payment_status = Column(String, nullable=True)
