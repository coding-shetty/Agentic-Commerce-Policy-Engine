"""
policy_engine.py

The core guardrail logic. Every function here is pure, deterministic Python —
no LLM calls, no ambiguity. This is the module you point to in your pitch
video when you explain "AI Judgment": the agent can reason in natural
language about WHAT to request, but whether that request is ALLOWED is
decided here, by rules a human wrote and can audit.

Design principle: three-tier response, not a binary allow/deny.
  1. Within bounds          -> APPROVED, executes immediately
  2. Outside bounds, close  -> ESCALATED, held for human approval
  3. Outside bounds, way off -> DENIED outright (protects against abuse/bad-faith asks)

This tiering is itself a judgment call worth defending in your pitch:
a request for 12% when the cap is 10% is a normal negotiation edge case
and worth a human's 30 seconds. A request for 80% is not a negotiation,
it's either a bug or an attack, and should never reach a human queue.
"""

import uuid
from models import (
    DiscountRequest,
    CheckoutRequest,
    RefundRequest,
    PolicyDecision,
    PolicyConfig,
    Decision,
    ActionType,
)


def check_discount(req: DiscountRequest, policy: PolicyConfig) -> PolicyDecision:
    cap = policy.max_discount_pct

    if req.sku not in policy.allowed_skus:
        return PolicyDecision(
            decision=Decision.DENIED,
            action=ActionType.DISCOUNT,
            reason=f"SKU '{req.sku}' is not in the allowed catalog for agent-initiated discounts.",
            bound_hit="allowed_skus",
        )

    if req.requested_discount_pct <= cap:
        return PolicyDecision(
            decision=Decision.APPROVED,
            action=ActionType.DISCOUNT,
            reason=f"{req.requested_discount_pct}% is within the {cap}% cap for {req.sku}.",
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
        )

    return PolicyDecision(
        decision=Decision.DENIED,
        action=ActionType.DISCOUNT,
        reason=(
            f"Requested {req.requested_discount_pct}% is more than "
            f"{policy.hard_deny_multiplier}x the {cap}% cap — denied outright, not escalated."
        ),
        bound_hit="max_discount_pct",
    )


def check_checkout(req: CheckoutRequest, policy: PolicyConfig) -> PolicyDecision:
    if req.sku not in policy.allowed_skus:
        return PolicyDecision(
            decision=Decision.DENIED,
            action=ActionType.CHECKOUT,
            reason=f"SKU '{req.sku}' is not in the allowed catalog.",
            bound_hit="allowed_skus",
        )

    gross = req.unit_price * req.quantity
    net = gross * (1 - req.applied_discount_pct / 100)

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
        )

    return PolicyDecision(
        decision=Decision.APPROVED,
        action=ActionType.CHECKOUT,
        reason=f"Order value ₹{net:.2f} is within the ₹{policy.max_order_value} cap.",
    )


def check_refund(req: RefundRequest, policy: PolicyConfig) -> PolicyDecision:
    cap = policy.max_refund_amount

    if req.refund_amount <= cap:
        return PolicyDecision(
            decision=Decision.APPROVED,
            action=ActionType.REFUND,
            reason=f"Refund of ₹{req.refund_amount:.2f} is within the ₹{cap} auto-approve cap.",
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
        )

    return PolicyDecision(
        decision=Decision.DENIED,
        action=ActionType.REFUND,
        reason=(
            f"Refund of ₹{req.refund_amount:.2f} is more than "
            f"{policy.hard_deny_multiplier}x the ₹{cap} cap — denied outright, flagged for review."
        ),
        bound_hit="max_refund_amount",
    )
