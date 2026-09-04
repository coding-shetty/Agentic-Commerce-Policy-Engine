"""
app/orchestrator.py

The only place that is allowed to move a transaction toward payment. This
is where the architectural guarantee lives:

    agent -> orchestrator -> policy engine -> (approved) -> payment

never:

    agent -> payment

Every function here:
  1. creates/loads a Transaction row
  2. runs the deterministic policy engine (and risk engine)
  3. drives the transaction through app/state_machine.py — invalid
     transitions raise, they don't get silently skipped
  4. writes an audit event for every decision
  5. only calls PaymentService.charge() once a transaction has reached
     APPROVED (directly, or via a resolved escalation)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app import audit_log, escalation
from app import state_machine as sm
from app.db_models import Payment, Transaction
from app.idempotency import check_and_reserve
from app.idempotency import complete as complete_idem
from app.money import minor_to_rupees, rupees_to_minor
from app.payment import PaymentError, PaymentService
from app.policy_engine import PolicyConfig, PolicyResult, check_checkout, check_discount, check_refund
from app.risk_engine import RiskResult, assess_checkout_risk, assess_discount_risk, assess_refund_risk


class PolicyEngineViolation(Exception):
    """Raised if application code ever tries to move money without a
    policy-approved transaction. This should be unreachable in normal
    operation — it exists as a hard stop, not a UX message."""


def _recent_denied_count(db: Session, agent_id: str, minutes: int = 15) -> int:
    since = datetime.now(UTC) - timedelta(minutes=minutes)
    return (
        db.query(Transaction)
        .filter(
            Transaction.agent_id == agent_id,
            Transaction.status == sm.DENIED,
            Transaction.created_at >= since,
        )
        .count()
    )


def _recent_refund_count(db: Session, agent_id: str, minutes: int = 60) -> int:
    since = datetime.now(UTC) - timedelta(minutes=minutes)
    return (
        db.query(Transaction)
        .filter(
            Transaction.agent_id == agent_id,
            Transaction.action_type == "refund",
            Transaction.created_at >= since,
        )
        .count()
    )


def _apply_policy_outcome(
    db: Session,
    txn: Transaction,
    policy_result: PolicyResult,
    risk_result: RiskResult,
    request_id: str,
) -> str | None:
    """Moves txn CREATED -> POLICY_CHECKED -> {APPROVED,ESCALATED,DENIED} and
    writes the audit trail. Returns an approval_id if escalated, else None."""

    txn.status = sm.transition(txn.status, sm.POLICY_CHECKED)
    txn.policy_decision = policy_result.decision
    txn.policy_reason = policy_result.reason
    txn.policy_bound_hit = policy_result.bound_hit
    txn.policy_version = policy_result.policy_version
    txn.risk_level = risk_result.level
    txn.risk_score = risk_result.score
    txn.risk_factors = ",".join(risk_result.factors)

    audit_log.record(
        db,
        "policy_decision",
        request_id=request_id,
        transaction_id=txn.id,
        agent_id=txn.agent_id,
        decision=policy_result.decision,
        reason=policy_result.reason,
        policy_version=policy_result.policy_version,
        metadata={"bound_hit": policy_result.bound_hit, "action": policy_result.action},
    )
    audit_log.record(
        db,
        "risk_assessment",
        request_id=request_id,
        transaction_id=txn.id,
        agent_id=txn.agent_id,
        decision=risk_result.level,
        reason=", ".join(risk_result.factors) or "no risk factors triggered",
        metadata={"score": risk_result.score},
    )

    approval_id: str | None = None

    if policy_result.decision == "denied":
        txn.status = sm.transition(txn.status, sm.DENIED)
    elif policy_result.decision == "approved":
        txn.status = sm.transition(txn.status, sm.APPROVED)
    else:  # escalated
        txn.status = sm.transition(txn.status, sm.ESCALATED)
        approval = escalation.create(db, txn.id, policy_result.reason, previous_status=txn.status)
        approval_id = approval.id
        audit_log.record(
            db,
            "escalation_created",
            request_id=request_id,
            transaction_id=txn.id,
            agent_id=txn.agent_id,
            decision="escalated",
            reason=policy_result.reason,
            metadata={"approval_id": approval_id},
        )

    db.flush()
    return approval_id


# ---------------------------------------------------------------------------
# Discount
# ---------------------------------------------------------------------------


def process_discount(
    db: Session, agent_id: str, sku: str, requested_discount_pct: float, order_value: float, policy: PolicyConfig
) -> tuple[Transaction, PolicyResult, RiskResult, str | None]:
    request_id = str(uuid.uuid4())
    txn = Transaction(
        request_id=request_id,
        agent_id=agent_id,
        action_type="discount",
        sku=sku,
        gross_minor=rupees_to_minor(order_value),
        status=sm.CREATED,
    )
    db.add(txn)
    db.flush()

    audit_log.record(db, "agent_request", request_id=request_id, transaction_id=txn.id, agent_id=agent_id,
                      metadata={"action": "discount", "sku": sku, "requested_discount_pct": requested_discount_pct})

    policy_result = check_discount(sku, requested_discount_pct, order_value, policy)
    risk_result = assess_discount_risk(
        sku, requested_discount_pct, order_value, policy, _recent_denied_count(db, agent_id)
    )
    approval_id = _apply_policy_outcome(db, txn, policy_result, risk_result, request_id)
    return txn, policy_result, risk_result, approval_id


# ---------------------------------------------------------------------------
# Checkout (the money-moving path)
# ---------------------------------------------------------------------------


def process_checkout(
    db: Session,
    agent_id: str,
    sku: str,
    quantity: int,
    unit_price: float,
    applied_discount_pct: float,
    idempotency_key: str,
    policy: PolicyConfig,
    payment_service: PaymentService,
) -> dict:
    payload = {
        "sku": sku,
        "quantity": quantity,
        "unit_price": unit_price,
        "applied_discount_pct": applied_discount_pct,
    }

    idem_record, is_new = check_and_reserve(db, idempotency_key, agent_id, payload)
    if not is_new:
        # Replay: either still pending (a concurrent request is mid-flight)
        # or completed — either way, do not create a second transaction or
        # a second payment attempt.
        if idem_record.status == "completed" and idem_record.response_json:
            import json as _json

            cached = _json.loads(idem_record.response_json)
            cached["replayed"] = True
            audit_log.record(
                db,
                "idempotency_replay",
                agent_id=agent_id,
                transaction_id=idem_record.transaction_id,
                metadata={"idempotency_key": idempotency_key},
            )
            db.commit()
            return cached
        # Still pending — treat as a conflict-free short-circuit; the
        # original request owns this key until it completes.
        return {
            "status": "pending",
            "replayed": True,
            "note": "A request with this idempotency_key is already being processed.",
        }

    request_id = str(uuid.uuid4())
    txn = Transaction(
        request_id=request_id,
        agent_id=agent_id,
        action_type="checkout",
        sku=sku,
        quantity=quantity,
        unit_price_minor=rupees_to_minor(unit_price),
        idempotency_key=idempotency_key,
        status=sm.CREATED,
    )
    db.add(txn)
    db.flush()
    idem_record.transaction_id = txn.id
    db.flush()

    audit_log.record(db, "agent_request", request_id=request_id, transaction_id=txn.id, agent_id=agent_id,
                      metadata={"action": "checkout", **payload, "idempotency_key": idempotency_key})

    policy_result = check_checkout(sku, quantity, unit_price, applied_discount_pct, policy)
    txn.discount_pct = int(round(applied_discount_pct * 100))
    txn.gross_minor = policy_result.gross_minor
    txn.net_minor = policy_result.net_minor

    net_rupees = float(minor_to_rupees(policy_result.net_minor)) if policy_result.net_minor is not None else 0.0
    risk_result = assess_checkout_risk(
        sku, quantity, net_rupees, applied_discount_pct, policy, _recent_denied_count(db, agent_id)
    )
    approval_id = _apply_policy_outcome(db, txn, policy_result, risk_result, request_id)

    response: dict = {
        "transaction_id": txn.id,
        "request_id": request_id,
        "decision": policy_result.decision,
        "action": "checkout",
        "reason": policy_result.reason,
        "bound_hit": policy_result.bound_hit,
        "policy_version": policy_result.policy_version,
        "risk_level": risk_result.level,
        "risk_score": risk_result.score,
        "status": txn.status,
        "approval_id": approval_id,
        "replayed": False,
    }

    if policy_result.decision == "approved":
        _advance_to_payment(db, txn, payment_service, request_id)
        response["status"] = txn.status
        response["payment"] = _payment_summary(db, txn.id)

    complete_idem(db, idem_record, txn.id, response)
    db.commit()
    return response


def _payment_summary(db: Session, transaction_id: str) -> dict | None:
    payment = (
        db.query(Payment)
        .filter(Payment.transaction_id == transaction_id)
        .order_by(Payment.created_at.desc())
        .first()
    )
    if not payment:
        return None
    return {
        "provider": payment.provider,
        "status": payment.status,
        "provider_order_id": payment.provider_order_id,
        "provider_payment_id": payment.provider_payment_id,
    }


def _advance_to_payment(db: Session, txn: Transaction, payment_service: PaymentService, request_id: str) -> None:
    """The ONLY function in the codebase that calls PaymentService.charge().
    Only reachable once txn.status == APPROVED."""
    if txn.status != sm.APPROVED:
        raise PolicyEngineViolation(
            f"Refused to advance transaction {txn.id} to payment: status is {txn.status}, not APPROVED."
        )

    txn.status = sm.transition(txn.status, sm.AWAITING_PAYMENT)
    txn.status = sm.transition(txn.status, sm.PAYMENT_PENDING)
    db.flush()

    amount_minor = txn.net_minor if txn.net_minor is not None else (txn.gross_minor or 0)

    audit_log.record(
        db, "payment_attempt", request_id=request_id, transaction_id=txn.id, agent_id=txn.agent_id,
        metadata={"provider": payment_service.provider_name, "amount_minor": amount_minor},
    )

    try:
        result = payment_service.charge(
            transaction_id=txn.id, amount_minor=amount_minor, currency=txn.currency, receipt=txn.id
        )
    except PaymentError as exc:
        txn.status = sm.transition(txn.status, sm.PAYMENT_FAILED)
        db.add(Payment(
            transaction_id=txn.id, provider=payment_service.provider_name, status="failed",
            amount_minor=amount_minor, currency=txn.currency, raw_response_json=str(exc),
        ))
        audit_log.record(
            db, "payment_failed", request_id=request_id, transaction_id=txn.id, agent_id=txn.agent_id,
            decision="failed", reason=str(exc),
        )
        db.flush()
        return

    payment = Payment(
        transaction_id=txn.id,
        provider=result.provider,
        provider_order_id=result.provider_order_id,
        provider_payment_id=result.provider_payment_id,
        status=result.status,
        amount_minor=amount_minor,
        currency=txn.currency,
        raw_response_json=str(result.raw),
    )
    db.add(payment)

    if result.status == "created":
        txn.status = sm.transition(txn.status, sm.PAYMENT_CREATED)
        txn.status = sm.transition(txn.status, sm.COMPLETED)
        audit_log.record(
            db, "payment_succeeded", request_id=request_id, transaction_id=txn.id, agent_id=txn.agent_id,
            decision="created", reason="Payment provider returned a created order.",
            metadata={"provider_order_id": result.provider_order_id},
        )
    else:
        txn.status = sm.transition(txn.status, sm.PAYMENT_FAILED)
        audit_log.record(
            db, "payment_failed", request_id=request_id, transaction_id=txn.id, agent_id=txn.agent_id,
            decision="failed", reason="Payment provider returned a non-success status.",
        )

    db.flush()


# ---------------------------------------------------------------------------
# Refund
# ---------------------------------------------------------------------------


def process_refund(
    db: Session, agent_id: str, order_id: str, refund_amount: float, reason: str, policy: PolicyConfig
) -> tuple[Transaction, PolicyResult, RiskResult, str | None]:
    request_id = str(uuid.uuid4())
    txn = Transaction(
        request_id=request_id,
        agent_id=agent_id,
        action_type="refund",
        sku=order_id,
        net_minor=rupees_to_minor(refund_amount),
        status=sm.CREATED,
    )
    db.add(txn)
    db.flush()

    audit_log.record(db, "agent_request", request_id=request_id, transaction_id=txn.id, agent_id=agent_id,
                      metadata={"action": "refund", "order_id": order_id, "refund_amount": refund_amount, "reason": reason})

    policy_result = check_refund(refund_amount, policy)
    risk_result = assess_refund_risk(refund_amount, policy, _recent_refund_count(db, agent_id))
    approval_id = _apply_policy_outcome(db, txn, policy_result, risk_result, request_id)
    return txn, policy_result, risk_result, approval_id


# ---------------------------------------------------------------------------
# Escalation resolution — the human-approval continuation mechanism
# ---------------------------------------------------------------------------


def resolve_escalation(
    db: Session,
    approval_id: str,
    approve: bool,
    approver: str,
    reason: str | None,
    payment_service: PaymentService,
) -> dict:
    approval = escalation.get(db, approval_id)
    if approval is None:
        raise LookupError(f"No approval request with id {approval_id}")
    if approval.status != "pending":
        raise ValueError(f"Approval {approval_id} was already resolved ({approval.status}) — refusing to process it again.")

    txn = db.get(Transaction, approval.transaction_id)
    if txn is None:
        raise LookupError(f"Transaction {approval.transaction_id} not found for approval {approval_id}")
    if txn.status != sm.ESCALATED:
        raise ValueError(
            f"Transaction {txn.id} is in status {txn.status}, not ESCALATED — refusing duplicate/late approval processing."
        )

    request_id = str(uuid.uuid4())

    if approve:
        txn.status = sm.transition(txn.status, sm.APPROVED)
        escalation.mark_resolved(db, approval, True, approver, sm.APPROVED)
        audit_log.record(
            db, "approval_granted", request_id=request_id, transaction_id=txn.id, agent_id=txn.agent_id,
            decision="approved", reason=reason or "Approved by human reviewer.",
            metadata={"approval_id": approval_id, "approver": approver},
        )
        if txn.action_type == "checkout":
            _advance_to_payment(db, txn, payment_service, request_id)
    else:
        txn.status = sm.transition(txn.status, sm.DENIED)
        escalation.mark_resolved(db, approval, False, approver, sm.DENIED)
        audit_log.record(
            db, "approval_rejected", request_id=request_id, transaction_id=txn.id, agent_id=txn.agent_id,
            decision="denied", reason=reason or "Rejected by human reviewer.",
            metadata={"approval_id": approval_id, "approver": approver},
        )

    db.commit()
    return {
        "approval_id": approval_id,
        "transaction_id": txn.id,
        "status": txn.status,
        "payment": _payment_summary(db, txn.id) if txn.action_type == "checkout" else None,
    }
