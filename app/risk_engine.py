"""
app/risk_engine.py

A second, independent deterministic layer on top of the policy engine's
approve/escalate/deny call. The policy engine answers "is this within the
rules?"; the risk engine answers "how suspicious does this look, even if
it's technically within the rules?" Both are rule-based and explainable —
no opaque ML/AI scoring — so every point awarded can be read straight out
of `factors`.

Score bands:
    0-29   LOW
    30-59  MEDIUM
    60+    HIGH
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.policy_engine import PolicyConfig

LOW, MEDIUM, HIGH = "LOW", "MEDIUM", "HIGH"


@dataclass
class RiskResult:
    score: int
    level: str
    factors: list[str] = field(default_factory=list)


def _level_for(score: int) -> str:
    if score >= 60:
        return HIGH
    if score >= 30:
        return MEDIUM
    return LOW


def assess_discount_risk(
    sku: str,
    requested_discount_pct: float,
    order_value: float,
    policy: PolicyConfig,
    recent_denied_count: int = 0,
) -> RiskResult:
    score = 0
    factors: list[str] = []

    if sku not in policy.allowed_skus:
        score += 40
        factors.append("unknown_sku")

    if requested_discount_pct > policy.max_discount_pct:
        over_by = requested_discount_pct - policy.max_discount_pct
        pts = min(40, int(over_by))  # 1 point per pct over cap, capped
        score += pts
        factors.append(f"discount_over_cap:{over_by:.1f}pp")

    if order_value > policy.max_order_value:
        score += 20
        factors.append("order_value_over_cap")

    if recent_denied_count > 0:
        pts = min(30, recent_denied_count * 10)
        score += pts
        factors.append(f"recent_denials:{recent_denied_count}")

    score = min(score, 100)
    return RiskResult(score=score, level=_level_for(score), factors=factors)


def assess_checkout_risk(
    sku: str,
    quantity: int,
    net_value: float,
    applied_discount_pct: float,
    policy: PolicyConfig,
    recent_denied_count: int = 0,
) -> RiskResult:
    score = 0
    factors: list[str] = []

    if sku not in policy.allowed_skus:
        score += 40
        factors.append("unknown_sku")

    if quantity >= 100:
        score += 20
        factors.append(f"unusual_quantity:{quantity}")

    if net_value > policy.max_order_value:
        over_by_ratio = net_value / policy.max_order_value
        pts = min(35, int((over_by_ratio - 1) * 35))
        score += max(pts, 15)
        factors.append("order_value_over_cap")

    if applied_discount_pct > policy.max_discount_pct:
        score += 25
        factors.append("discount_over_cap")

    if recent_denied_count > 0:
        pts = min(30, recent_denied_count * 10)
        score += pts
        factors.append(f"recent_denials:{recent_denied_count}")

    score = min(score, 100)
    return RiskResult(score=score, level=_level_for(score), factors=factors)


def assess_refund_risk(
    refund_amount: float,
    policy: PolicyConfig,
    recent_refund_count: int = 0,
) -> RiskResult:
    score = 0
    factors: list[str] = []

    if refund_amount > policy.max_refund_amount:
        over_by_ratio = refund_amount / policy.max_refund_amount
        pts = min(40, int((over_by_ratio - 1) * 40))
        score += max(pts, 15)
        factors.append("refund_over_cap")

    if recent_refund_count >= 3:
        score += min(40, recent_refund_count * 10)
        factors.append(f"refund_frequency:{recent_refund_count}")

    score = min(score, 100)
    return RiskResult(score=score, level=_level_for(score), factors=factors)
