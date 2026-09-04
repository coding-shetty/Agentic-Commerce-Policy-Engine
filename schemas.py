"""
schemas.py

Pydantic schemas for everything that flows through the policy engine:
- what an agent is asking to do (DiscountRequest, CheckoutRequest, RefundRequest)
- what the engine decides (PolicyDecision)
- the shape of the policy config itself (PolicyConfig)
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field


class Decision(str, Enum):
    APPROVED = "approved"
    DENIED = "denied"
    ESCALATED = "escalated"


class ActionType(str, Enum):
    DISCOUNT = "discount"
    CHECKOUT = "checkout"
    REFUND = "refund"


class TransactionState(str, Enum):
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

# ---------- Inbound requests ----------

class BaseAgentRequest(BaseModel):
    request_id: str
    agent_id: str


class DiscountRequest(BaseAgentRequest):
    sku: str
    requested_discount_pct: Decimal = Field(..., ge=0, le=100)
    order_value: Decimal = Field(..., gt=0)


class CheckoutRequest(BaseAgentRequest):
    sku: str
    quantity: int = Field(..., gt=0)
    unit_price: Decimal = Field(..., gt=0)
    applied_discount_pct: Decimal = Field(Decimal("0"), ge=0, le=100)
    idempotency_key: str


class RefundRequest(BaseAgentRequest):
    order_id: str
    refund_amount: Decimal = Field(..., gt=0)
    reason: str


# ---------- Outbound decision ----------

class PolicyDecision(BaseModel):
    decision: Decision
    action: ActionType
    reason: str
    bound_hit: str | None = None
    escalation_id: str | None = None
    policy_version: str


# ---------- Policy configuration ----------

class PolicyConfig(BaseModel):
    version: str = "1.0.0"
    max_discount_pct: Decimal = Decimal("10.0")
    max_order_value: Decimal = Decimal("5000.0")
    max_refund_amount: Decimal = Decimal("2000.0")
    allowed_skus: list[str] = Field(default_factory=list)
    hard_deny_multiplier: Decimal = Decimal("2.0")
