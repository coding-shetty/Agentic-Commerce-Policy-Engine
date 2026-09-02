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
"""

import json
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from schemas import DiscountRequest, CheckoutRequest, RefundRequest, PolicyConfig
import policy_engine
import audit_log
import escalation

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

    # Only "approved" checkouts would go on to actually call the Razorpay
    # test-mode order API in the full build. Cache the result either way
    # so a retry with the same key is always safe.
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
