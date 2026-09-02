"""
models.py

Pydantic schemas for everything that flows through the policy engine:
- what an agent is asking to do (DiscountRequest, CheckoutRequest, RefundRequest)
- what the engine decides (PolicyDecision)
- the shape of the policy config itself (PolicyConfig)

Keeping these as typed models (not raw dicts) means the FastAPI layer gets
free request validation, and the engine never has to guess what fields exist.
"""

from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class Decision(str, Enum):
    """Every action funnels into exactly one of these three outcomes.
    No silent failures, no partial states — this is what makes the
    audit trail legible."""
    APPROVED = "approved"
    DENIED = "denied"
    ESCALATED = "escalated"  # within reach of policy, but needs a human sign-off


class ActionType(str, Enum):
    DISCOUNT = "discount"
    CHECKOUT = "checkout"
    REFUND = "refund"


# ---------- Inbound requests (what an agent asks the engine to approve) ----------

class DiscountRequest(BaseModel):
    agent_id: str
    sku: str
    requested_discount_pct: float = Field(..., ge=0, le=100)
    order_value: float = Field(..., gt=0)


class CheckoutRequest(BaseModel):
    agent_id: str
    sku: str
    quantity: int = Field(..., gt=0)
    unit_price: float = Field(..., gt=0)
    applied_discount_pct: float = Field(0, ge=0, le=100)
    idempotency_key: str  # required — this is what makes retries safe, see main.py


class RefundRequest(BaseModel):
    agent_id: str
    order_id: str
    refund_amount: float = Field(..., gt=0)
    reason: str


# ---------- Outbound decision (what the engine hands back) ----------

class PolicyDecision(BaseModel):
    decision: Decision
    action: ActionType
    reason: str  # plain-English explanation — this is what the audit dashboard shows
    bound_hit: Optional[str] = None  # which specific rule fired, if any
    escalation_id: Optional[str] = None  # set only when decision == ESCALATED


# ---------- Policy configuration (the deterministic rulebook) ----------

class PolicyConfig(BaseModel):
    """
    This is intentionally NOT generated or touched by an LLM. It's the
    boring, hardcoded rulebook that every agent action gets checked against.
    Loaded once at startup from policy.json.
    """
    max_discount_pct: float = 10.0
    max_order_value: float = 5000.0
    max_refund_amount: float = 2000.0
    allowed_skus: list[str] = Field(default_factory=list)
    # anything that breaches a bound by MORE than this margin gets denied outright
    # instead of escalated (e.g. someone asking for a 90% discount isn't a
    # borderline case, it's a bad-faith request — deny, don't waste a human's time)
    hard_deny_multiplier: float = 2.0
