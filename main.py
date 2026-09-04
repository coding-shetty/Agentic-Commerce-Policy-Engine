"""
main.py

FastAPI service exposing the tools an agent (or your test harness) calls:
    POST /discount   -> check_discount
    POST /checkout   -> check_checkout, with idempotency (see below)
    POST /refund      -> check_refund
    GET  /audit       -> recent decisions, for the dashboard
    POST /approve/{id} -> a human resolving an escalated request

Idempotent checkout is the deliberate "failure recovery" demo piece:
if a checkout call is retried with the SAME idempotency_key (e.g. because
the first attempt's response got lost on a flaky connection, or Razorpay's
test API timed out), we return the ORIGINAL result instead of double-charging.
That's the concrete "one failure handled gracefully" the track asks for —
simulate it by calling /checkout twice with the same idempotency_key and
showing the second call short-circuits instead of creating a second order.

Approved checkouts now also create a real order against Razorpay's test-mode
Orders API via razorpay_client.py — see that file for the split: this file
decides WHETHER to charge, razorpay_client.py only knows HOW.
"""

import json

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import audit_log
import escalation
import policy_engine
import razorpay_client
from schemas import CheckoutRequest, DiscountRequest, PolicyConfig, RefundRequest

load_dotenv()  # picks up RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET from .env

app = FastAPI(title="Agentic Commerce Policy Engine")

# Wide-open CORS for the demo dashboard — tighten before this touches anything real.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory idempotency store: idempotency_key -> the PolicyDecision + order result
# that was returned the first time. A real system would put this in Redis/Postgres
# with a TTL; a dict is fine for a demo that runs for a few minutes.
_idempotency_store: dict[str, dict] = {}

POLICY: PolicyConfig


@app.on_event("startup")
def startup():
    global POLICY
    with open("policy.json") as f:
        POLICY = PolicyConfig(**json.load(f))
    audit_log.init_db()
    escalation.init_pending_table()


@app.post("/discount")
def discount(req: DiscountRequest):
    decision = policy_engine.check_discount(req, POLICY)
    audit_log.log_decision(req.agent_id, decision, req.model_dump())

    if decision.decision.value == "escalated":
        escalation.route_to_human(decision.escalation_id, req.model_dump(), decision.reason)

    return decision


@app.post("/checkout")
def checkout(req: CheckoutRequest):
    # --- Idempotency check: this is the failure-recovery moment ---
    if req.idempotency_key in _idempotency_store:
        cached = _idempotency_store[req.idempotency_key]
        return {
            **cached,
            "replayed": True,
            "note": "Duplicate request detected via idempotency_key — returning the original result instead of processing a second charge.",
        }

    decision = policy_engine.check_checkout(req, POLICY)
    audit_log.log_decision(req.agent_id, decision, req.model_dump())

    if decision.decision.value == "escalated":
        escalation.route_to_human(decision.escalation_id, req.model_dump(), decision.reason)

    result = decision.model_dump()
    result["replayed"] = False
    result["razorpay_order_id"] = None
    result["razorpay_error"] = None

    # Only approved checkouts actually touch Razorpay. Escalated/denied
    # checkouts never create an order — money doesn't move until a human
    # (or the policy engine outright) has signed off.
    if decision.decision.value == "approved":
        net_amount = req.unit_price * req.quantity * (1 - req.applied_discount_pct / 100)
        try:
            order = razorpay_client.create_test_order(
                amount_rupees=net_amount,
                receipt=req.idempotency_key,
                notes={"sku": req.sku, "agent_id": req.agent_id},
            )
            result["razorpay_order_id"] = order["id"]
        except razorpay_client.RazorpayOrderError as exc:
            # The policy engine approved the money action, but the actual charge
            # failed for an unrelated reason (bad keys, network, Razorpay downtime).
            # Surface this distinctly rather than silently pretending it succeeded —
            # an "approved but not actually charged" state must never look identical
            # to a normal success.
            result["razorpay_error"] = str(exc)

    # Cache the result either way so a retry with the same key is always safe,
    # including retries after a Razorpay-side failure above.
    _idempotency_store[req.idempotency_key] = result

    return result


@app.post("/refund")
def refund(req: RefundRequest):
    decision = policy_engine.check_refund(req, POLICY)
    audit_log.log_decision(req.agent_id, decision, req.model_dump())

    if decision.decision.value == "escalated":
        escalation.route_to_human(decision.escalation_id, req.model_dump(), decision.reason)

    return decision


@app.get("/audit")
def audit(limit: int = 50):
    return audit_log.get_recent(limit)


@app.post("/approve/{escalation_id}")
def approve(escalation_id: str, approve: bool = True):
    resolved = escalation.resolve_approval(escalation_id, approve)
    if not resolved:
        raise HTTPException(status_code=404, detail="No pending escalation with that id.")
    return {"escalation_id": escalation_id, "status": "approved" if approve else "denied"}