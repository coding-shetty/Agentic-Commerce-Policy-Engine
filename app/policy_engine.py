"""
app/policy_engine.py

The core guardrail. Every function here is pure, deterministic Python —
no LLM calls, no network calls, no ambiguity. This module never imports
app.agent, and app.agent never bypasses it: the agent can reason in
natural language about WHAT to request, but whether that request is
ALLOWED is decided here, by versioned rules a human wrote and can audit.

Three-tier response, not binary allow/deny:
  1. Within bounds          -> APPROVED, proceeds toward payment
  2. Outside bounds, close  -> ESCALATED, held for a human
  3. Outside bounds, way off -> DENIED outright (protects against abuse)

All money/percentage math happens in integer minor units / bp100 via
app/money.py — never float.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.money import apply_discount_minor, rupees_to_minor

POLICY_PATH = Path(__file__).resolve().parent.parent / "policy.json"


@dataclass
class PolicyConfig:
    version: str = "1.0.0"
    max_discount_pct: float = 10.0
    max_order_value: float = 5000.0
    max_refund_amount: float = 2000.0
    allowed_skus: list[str] = field(default_factory=list)
    hard_deny_multiplier: float = 2.0

    @classmethod
    def load(cls, path: Path = POLICY_PATH) -> PolicyConfig:
        with open(path) as f:
            data = json.load(f)
        return cls(**data)


@dataclass
class PolicyResult:
    decision: str  # approved | escalated | denied
    action: str  # discount | checkout | refund
    reason: str
    bound_hit: str | None = None
    policy_version: str = "1.0.0"
    gross_minor: int | None = None
    net_minor: int | None = None
    discount_bp100: int | None = None


def check_discount(sku: str, requested_discount_pct: float, order_value: float, policy: PolicyConfig) -> PolicyResult:
    cap = policy.max_discount_pct

    if sku not in policy.allowed_skus:
        return PolicyResult(
            decision="denied",
            action="discount",
            reason=f"SKU '{sku}' is not in the allowed catalog for agent-initiated discounts.",
            bound_hit="allowed_skus",
            policy_version=policy.version,
        )

    if requested_discount_pct <= cap:
        return PolicyResult(
            decision="approved",
            action="discount",
            reason=f"{requested_discount_pct}% is within the {cap}% cap for {sku}.",
            policy_version=policy.version,
        )

    if requested_discount_pct <= cap * policy.hard_deny_multiplier:
        return PolicyResult(
            decision="escalated",
            action="discount",
            reason=(
                f"Requested {requested_discount_pct}% exceeds the {cap}% cap "
                f"but is within escalation range — routed to a human for approval."
            ),
            bound_hit="max_discount_pct",
            policy_version=policy.version,
        )

    return PolicyResult(
        decision="denied",
        action="discount",
        reason=(
            f"Requested {requested_discount_pct}% is more than "
            f"{policy.hard_deny_multiplier}x the {cap}% cap — denied outright, not escalated."
        ),
        bound_hit="max_discount_pct",
        policy_version=policy.version,
    )


def check_checkout(
    sku: str,
    quantity: int,
    unit_price: float,
    applied_discount_pct: float,
    policy: PolicyConfig,
) -> PolicyResult:
    if sku not in policy.allowed_skus:
        return PolicyResult(
            decision="denied",
            action="checkout",
            reason=f"SKU '{sku}' is not in the allowed catalog.",
            bound_hit="allowed_skus",
            policy_version=policy.version,
        )

    unit_price_minor = rupees_to_minor(unit_price)
    gross_minor = unit_price_minor * quantity
    discount_bp100 = int(round(applied_discount_pct * 100))  # pct -> bp100
    net_minor = apply_discount_minor(gross_minor, discount_bp100)

    if applied_discount_pct > policy.max_discount_pct:
        return PolicyResult(
            decision="denied",
            action="checkout",
            reason=(
                f"Checkout carries a {applied_discount_pct}% discount, which exceeds the "
                f"{policy.max_discount_pct}% cap and was never separately approved — "
                f"refusing to let a bad discount slip through at checkout."
            ),
            bound_hit="max_discount_pct",
            policy_version=policy.version,
            gross_minor=gross_minor,
            net_minor=net_minor,
            discount_bp100=discount_bp100,
        )

    max_order_minor = rupees_to_minor(policy.max_order_value)
    if net_minor > max_order_minor:
        return PolicyResult(
            decision="escalated",
            action="checkout",
            reason=(
                f"Order value exceeds the ₹{policy.max_order_value} per-order cap — "
                f"routed to a human for approval before charging."
            ),
            bound_hit="max_order_value",
            policy_version=policy.version,
            gross_minor=gross_minor,
            net_minor=net_minor,
            discount_bp100=discount_bp100,
        )

    return PolicyResult(
        decision="approved",
        action="checkout",
        reason=f"Order value is within the ₹{policy.max_order_value} cap.",
        policy_version=policy.version,
        gross_minor=gross_minor,
        net_minor=net_minor,
        discount_bp100=discount_bp100,
    )


def check_refund(refund_amount: float, policy: PolicyConfig) -> PolicyResult:
    cap = policy.max_refund_amount
    refund_minor = rupees_to_minor(refund_amount)
    cap_minor = rupees_to_minor(cap)

    if refund_minor <= cap_minor:
        return PolicyResult(
            decision="approved",
            action="refund",
            reason=f"Refund of ₹{refund_amount:.2f} is within the ₹{cap} auto-approve cap.",
            policy_version=policy.version,
            net_minor=refund_minor,
        )

    if refund_minor <= cap_minor * policy.hard_deny_multiplier:
        return PolicyResult(
            decision="escalated",
            action="refund",
            reason=(
                f"Refund of ₹{refund_amount:.2f} exceeds the ₹{cap} auto-approve cap — "
                f"routed to a human for approval."
            ),
            bound_hit="max_refund_amount",
            policy_version=policy.version,
            net_minor=refund_minor,
        )

    return PolicyResult(
        decision="denied",
        action="refund",
        reason=(
            f"Refund of ₹{refund_amount:.2f} is more than {policy.hard_deny_multiplier}x "
            f"the ₹{cap} cap — denied outright, flagged for review."
        ),
        bound_hit="max_refund_amount",
        policy_version=policy.version,
        net_minor=refund_minor,
    )
