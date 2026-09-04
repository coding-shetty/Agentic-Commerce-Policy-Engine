# Agentic Commerce Policy Engine
 
A deterministic guardrail layer that sits between an AI shopping agent and
any money-moving action (discount, checkout, refund). Built for the
Razorpay AI Buildathon — Track 01: AI Growth & Agentic Commerce.
 
## Why this exists
 
An AI agent negotiating on behalf of a buyer should never have unbounded
authority over money. This service is the boring, rule-based layer that
every agent action gets checked against before it executes — no LLM in
this codepath, on purpose.
 
## Three-tier decisions, not binary allow/deny
 
| Tier | Meaning | Example |
|---|---|---|
| **Approved** | Within policy bounds, executes immediately | 8% discount, cap is 10% |
| **Escalated** | Outside bounds but plausible — held for a human | 15% discount, cap is 10% |
| **Denied** | Outside bounds and implausible — rejected outright | 60% discount, cap is 10% |
 
The `hard_deny_multiplier` in `policy.json` decides where "escalate" ends
and "deny outright" begins, so a human's queue only fills with genuinely
borderline calls, not bad-faith or malformed requests.
 
## Failure recovery: idempotent checkout
 
Every checkout call carries an `idempotency_key`. Retry the same call with
the same key (simulating a lost response / flaky connection) and the
service returns the *original* result instead of processing a second
charge. See `demo.py`, step 5.
 
## Running it
 
```bash
pip install -r requirements.txt
uvicorn main:app --reload
# in another terminal:
python demo.py
```
 
Endpoints:
- `POST /discount` — check a discount request
- `POST /checkout` — check + process a checkout (idempotent)
- `POST /refund` — check a refund request
- `GET /audit` — recent decisions, for a dashboard
- `POST /approve/{escalation_id}` — human resolves a pending escalation
## What's not built yet
 
- The buyer/seller LLM agents that would call these endpoints (this repo
  is the guardrail layer only)
- Real Razorpay test-mode order creation (checkout returns a policy
  decision; wiring the actual order API call is the next step)
- Escalation routes to a DB row + console log, not a real Slack/email hook
- No auth on the endpoints — fine for a local demo, not for anything beyond it
## Files
 
- `models.py` — request/decision/config schemas
- `policy_engine.py` — the deterministic rules (the actual guardrail)
- `audit_log.py` — SQLite-backed decision log
- `escalation.py` — human-in-the-loop routing stub
- `main.py` — FastAPI app wiring it together
- `policy.json` — the rulebook (edit this, not the code, to change limits)
- `demo.py` — scripted walkthrough of every decision path
 
