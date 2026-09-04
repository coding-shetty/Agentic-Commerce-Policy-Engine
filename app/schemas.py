"""
app/schemas.py

Pydantic request/response models for the API boundary. Money and
percentages arrive as decimal strings/numbers from the client and are
immediately converted to integer minor units via app/money.py — nothing
downstream of validation ever touches a float for a monetary value.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class Decision(str, Enum):
    APPROVED = "approved"
    DENIED = "denied"
    ESCALATED = "escalated"


class ActionType(str, Enum):
    DISCOUNT = "discount"
    CHECKOUT = "checkout"
    REFUND = "refund"


class Role(str, Enum):
    AGENT = "AGENT"
    ADMIN = "ADMIN"


# ---------- Auth ----------


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: Role
    expires_in_minutes: int


# ---------- Inbound requests ----------


class DiscountRequest(BaseModel):
    sku: str
    requested_discount_pct: float = Field(..., ge=0, le=100)
    order_value: float = Field(..., gt=0)


class CheckoutRequest(BaseModel):
    sku: str
    quantity: int = Field(..., gt=0, le=10_000)
    unit_price: float = Field(..., gt=0)
    applied_discount_pct: float = Field(0, ge=0, le=100)
    idempotency_key: str = Field(..., min_length=8, max_length=255)

    @field_validator("idempotency_key")
    @classmethod
    def _key_shape(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("idempotency_key must not be blank")
        return v.strip()


class RefundRequest(BaseModel):
    order_id: str
    refund_amount: float = Field(..., gt=0)
    reason: str = Field(..., min_length=1, max_length=500)


# ---------- Outbound ----------


class PolicyDecisionOut(BaseModel):
    transaction_id: str
    request_id: str
    decision: Decision
    action: ActionType
    reason: str
    bound_hit: str | None = None
    policy_version: str
    risk_level: str | None = None
    risk_score: int | None = None
    status: str
    approval_id: str | None = None
    replayed: bool = False


class ApprovalResolution(BaseModel):
    reason: str | None = Field(None, max_length=500)


class TransactionOut(BaseModel):
    id: str
    request_id: str
    agent_id: str
    action_type: str
    sku: str | None
    status: str
    policy_decision: str | None
    policy_reason: str | None
    policy_version: str | None
    risk_level: str | None
    risk_score: int | None
    net_amount_rupees: str | None = None
    currency: str
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class AgentChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    sku: str = Field(default="SKU-001")


class AgentChatResponse(BaseModel):
    reply: str
    transaction: PolicyDecisionOut | None = None
    llm_used: bool
    metadata: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error_code: str
    message: str
    request_id: str | None = None
