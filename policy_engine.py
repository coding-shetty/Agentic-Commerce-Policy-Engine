"""
policy_engine.py

The core guardrail logic. Every function here is pure, deterministic Python —
no LLM calls, no ambiguity.
"""

import uuid
from decimal import Decimal

from schemas import (
    ActionType,
    CheckoutRequest,
    Decision,
    DiscountRequest,
    PolicyConfig,
    PolicyDecision,
    RefundRequest,
)


def check_discount(req: DiscountRequest, policy: PolicyConfig) -> PolicyDecision:
    cap = policy.max_discount_pct

    if req.sku not in policy.allowed_skus:
        return PolicyDecision(
            decision=Decision.DENIED,
            action=ActionType.DISCOUNT,
            reason=f"SKU '{req.sku}' is not in the allowed catalog for agent-initiated discounts.",
            bound_hit="allowed_skus",
            policy_version=policy.version,
        )

    if req.requested_discount_pct <= cap:
        return PolicyDecision(
            decision=Decision.APPROVED,
            action=ActionType.DISCOUNT,
            reason=f"{req.requested_discount_pct}% is within the {cap}% cap for {req.sku}.",
            policy_version=policy.version,
        )

    if req.requested_discount_pct <= cap * policy.hard_deny_multiplier:
        escalation_id = str(uuid.uuid4())
        return PolicyDecision(
            decision=Decision.ESCALATED,
            action=ActionType.DISCOUNT,
            reason=(
                f"Requested {req.requested_discount_pct}% exceeds the {cap}% cap "
                f"but is within escalation range — routed to a human for approval."
            ),
            bound_hit="max_discount_pct",
            escalation_id=escalation_id,
            policy_version=policy.version,
        )

    return PolicyDecision(
        decision=Decision.DENIED,
        action=ActionType.DISCOUNT,
        reason=(
            f"Requested {req.requested_discount_pct}% is more than "
            f"{policy.hard_deny_multiplier}x the {cap}% cap — denied outright, not escalated."
        ),
        bound_hit="max_discount_pct",
        policy_version=policy.version,
    )


def check_checkout(req: CheckoutRequest, policy: PolicyConfig) -> PolicyDecision:
    if req.sku not in policy.allowed_skus:
        return PolicyDecision(
            decision=Decision.DENIED,
            action=ActionType.CHECKOUT,
            reason=f"SKU '{req.sku}' is not in the allowed catalog.",
            bound_hit="allowed_skus",
            policy_version=policy.version,
        )

    gross = req.unit_price * req.quantity
    net = gross * (Decimal("1") - req.applied_discount_pct / Decimal("100"))

    if req.applied_discount_pct > policy.max_discount_pct:
        return PolicyDecision(
            decision=Decision.DENIED,
            action=ActionType.CHECKOUT,
            reason=(
                f"Checkout carries a {req.applied_discount_pct}% discount, "
                f"which exceeds the {policy.max_discount_pct}% cap and was never "
                f"separately approved — refusing to let a bad discount slip through at checkout."
            ),
            bound_hit="max_discount_pct",
            policy_version=policy.version,
        )

    if net > policy.max_order_value:
        escalation_id = str(uuid.uuid4())
        return PolicyDecision(
            decision=Decision.ESCALATED,
            action=ActionType.CHECKOUT,
            reason=(
                f"Order value ₹{net:.2f} exceeds the ₹{policy.max_order_value} "
                f"per-order cap — routed to a human for approval before charging."
            ),
            bound_hit="max_order_value",
            escalation_id=escalation_id,
            policy_version=policy.version,
        )

    return PolicyDecision(
        decision=Decision.APPROVED,
        action=ActionType.CHECKOUT,
        reason=f"Order value ₹{net:.2f} is within the ₹{policy.max_order_value} cap.",
        policy_version=policy.version,
    )


def check_refund(req: RefundRequest, policy: PolicyConfig) -> PolicyDecision:
    cap = policy.max_refund_amount

    if req.refund_amount <= cap:
        return PolicyDecision(
            decision=Decision.APPROVED,
            action=ActionType.REFUND,
            reason=f"Refund of ₹{req.refund_amount:.2f} is within the ₹{cap} auto-approve cap.",
            policy_version=policy.version,
        )

    if req.refund_amount <= cap * policy.hard_deny_multiplier:
        escalation_id = str(uuid.uuid4())
        return PolicyDecision(
            decision=Decision.ESCALATED,
            action=ActionType.REFUND,
            reason=(
                f"Refund of ₹{req.refund_amount:.2f} exceeds the ₹{cap} auto-approve cap "
                f"— routed to a human for approval."
            ),
            bound_hit="max_refund_amount",
            escalation_id=escalation_id,
            policy_version=policy.version,
        )

    return PolicyDecision(
        decision=Decision.DENIED,
        action=ActionType.REFUND,
        reason=(
            f"Refund of ₹{req.refund_amount:.2f} is more than "
            f"{policy.hard_deny_multiplier}x the ₹{cap} cap — denied outright, flagged for review."
        ),
        bound_hit="max_refund_amount",
        policy_version=policy.version,
    )
