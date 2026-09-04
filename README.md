# Agentic Commerce Control Plane

A deterministic guardrail layer that sits between an AI shopping agent and every money-moving action it can take — discount, checkout, refund. Built for the **Razorpay AI Buildathon — Track 01: AI Growth & Agentic Commerce.**

An AI agent negotiating on behalf of a buyer should never have unbounded authority over money. This is that boundary: an agent can *propose* an action, but a deterministic policy engine — not the LLM — decides whether it happens, and a real state machine makes sure a denied or escalated transaction can never quietly reach payment.

```
   USER
     |
     v
 AI BUYER AGENT  (LiteLLM/Ollama, with a deterministic fallback parser)
     |
     v
 COMMERCE ORCHESTRATOR   <-- the only code path allowed to move a transaction forward
     |
     v
 +------------------+       +----------------+
 |  POLICY ENGINE   |  -->  |  RISK ENGINE   |   (independent, explainable, rule-based)
 |  deterministic   |       |  score + level |
 +--------+---------+       +----------------+
          |
   +------+-------+--------------+
   |              |               |
   v              v               v
APPROVED      ESCALATED        DENIED  ← terminal, no transition out of this state
   |              |
   |      HUMAN APPROVAL (ADMIN role, JWT-gated)
   |              |
   +------+-------+
          v
   PAYMENT SERVICE  (DemoPaymentProvider, or RazorpayPaymentProvider — TEST MODE)
          |
          v
   AUDIT TRAIL + ADMIN DASHBOARD
```

## Why this exists

Most agentic-commerce demos wire an LLM straight to a payment API and hope. This project treats that as the actual risk: the agent here can *ask*, never *act*. Every money-moving request — however it originates — passes through the same deterministic policy engine, the same explainable risk scorer, and the same state machine before a payment provider is ever called.

## The core guarantee

`app/state_machine.py` defines the only legal transitions a transaction can make. `DENIED` and `CANCELLED` are terminal states with **no outgoing transitions at all** — this isn't a convention the rest of the code has to remember to respect, it's structurally enforced: calling `transition()` toward an illegal state raises `InvalidTransitionError` rather than silently succeeding. `app/orchestrator.py` is the only module allowed to call `PaymentService.charge()`, and it refuses to do so unless a transaction's status is already `APPROVED`.

## What happens on each action

| Action | Flow |
|---|---|
| **Discount** | Agent requests a % off → policy engine checks it against `policy.json` → risk engine scores it independently → `APPROVED` / `ESCALATED` / `DENIED`, all logged to the audit trail |
| **Checkout** | Same policy + risk pipeline, plus idempotency (below) and, on approval, a real payment attempt via `PaymentService` |
| **Refund** | Same three-tier decision against `max_refund_amount`, with its own risk factors (e.g. refund frequency) |

`hard_deny_multiplier` in `policy.json` sets where "escalate for a human" ends and "deny outright" begins, so a human reviewer's queue only fills with genuinely borderline calls — not bad-faith or malformed requests.

## Failure recovery: idempotent checkout, backed by the database

Every checkout carries an `idempotency_key`. Retrying with the same key returns the *original* result instead of processing a second charge — and this is enforced at the database layer (`app/idempotency.py`), not an in-memory dict that would forget on restart:

- Same key + same payload → returns the cached result (`replayed: true`)
- Same key + a **different** payload → `409 Conflict` — an agent can't quietly swap in a different order under the same key
- Two concurrent requests with the same brand-new key → the database's unique constraint lets only one insert win; the loser reads back the winner's row instead of racing it

## Money, handled correctly

Every rupee amount is converted to an integer number of paise via `Decimal` the moment it crosses into the system (`app/money.py`) and stays that way through the database and the payment call. Floats never touch a monetary value anywhere downstream of the API boundary — this avoids the classic `0.1 + 0.2 != 0.3` class of bug that could silently corrupt a checkout total.

## Payments: demo by default, real Razorpay test-mode on request

`DEMO_MODE=true` (the default) simulates a realistic payment lifecycle with no network calls, clearly labeled `"demo": true` in every response — safe to run and demo anywhere.

`DEMO_MODE=false` switches to `RazorpayPaymentProvider`, which creates a real order via Razorpay's **test-mode** Orders API. It never claims success unless Razorpay actually returned an order id: timeouts and network errors surface as `PAYMENT_FAILED`, not a guessed success, and an ambiguous 2xx response with no order id is treated as a hard error rather than assumed to have worked.

## Auth

Two roles: `AGENT` and `ADMIN`, enforced via JWT bearer tokens on every money-moving and approval endpoint. For local demo purposes, `POST /auth/login` accepts `agent`/`admin` with `DEMO_AUTH_PASSWORD` and issues a real token for that role — no separate user store needed to run the whole flow end to end. Only `ADMIN` tokens can resolve approvals or read the raw audit trail.

## Running it

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # defaults work out of the box for a demo

uvicorn app.main:app --reload
```

Then open:
- **http://localhost:8000/** — the demo UI: log in as the agent, negotiate a discount, check out
- **http://localhost:8000/dashboard** — the admin dashboard: pending approvals, transactions, audit trail (log in as `admin` / `demo-password`)
- **http://localhost:8000/docs** — interactive API docs (FastAPI's built-in Swagger UI)

To exercise a real Razorpay test-mode order instead of the simulated one, set `DEMO_MODE=false` and fill in `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` (from Razorpay Dashboard → Account & Settings → Websites & API keys → Generate Key, in Test Mode) in `.env`.

To exercise the real LLM agent instead of the deterministic fallback parser, run [Ollama](https://ollama.com) locally with a pulled model (default `llama3.1`) — otherwise `/agent/chat` degrades honestly to the fallback parser and says so in its response metadata.

## API

| Endpoint | Role | What it does |
|---|---|---|
| `POST /auth/login` | — | Demo login, returns a bearer token |
| `POST /agent/chat` | AGENT/ADMIN | Natural-language turn with the buyer agent |
| `POST /discount` | AGENT/ADMIN | Check a discount request against policy + risk |
| `POST /checkout` | AGENT/ADMIN | Check + process a checkout, idempotent, triggers payment on approval |
| `POST /refund` | AGENT/ADMIN | Check a refund request against policy + risk |
| `GET /transactions` | any | List transactions (agents see their own; admins see all) |
| `GET /audit` | ADMIN | Full audit trail |
| `GET /approvals` | ADMIN | Pending human-review escalations |
| `POST /approve/{id}` / `POST /reject/{id}` | ADMIN | Resolve an escalation; approving a checkout continues it to payment |
| `GET /health` | — | Liveness + DB check |

## Project structure

```
app/
├── main.py            FastAPI app: routing, auth wiring, middleware, exception handling
├── agent.py            The AI buyer agent (LiteLLM/Ollama + deterministic fallback)
├── orchestrator.py      The only path allowed to move a transaction toward payment
├── policy_engine.py     Deterministic approve/escalate/deny rules (no LLM, on purpose)
├── risk_engine.py        Independent, explainable risk scoring
├── state_machine.py      The transition table — DENIED/CANCELLED are structurally terminal
├── payment.py           Demo + Razorpay test-mode payment providers behind one interface
├── idempotency.py        DB-backed idempotency with payload-hash conflict detection
├── money.py              Decimal-based minor-unit money handling
├── security.py            JWT auth, password hashing, role enforcement
├── rate_limit.py           Per-agent, per-minute rate limiting
├── audit_log.py, escalation.py, db_models.py, database.py, config.py, schemas.py
alembic/                  Database migrations
templates/, static/       Demo UI + admin dashboard (server-rendered, vanilla JS)
policy.json                The rulebook — edit this, not the code, to change limits
docs/ARCHITECTURE_AUDIT.md  The audit that motivated this design
```

## What's not built yet

- **No automated tests** — `pytest` is configured (`pyproject.toml` points at `tests/`) but the directory doesn't exist yet. Highest-priority next step.
- Escalation resolution notifies no one externally (no Slack/email hook) — an admin has to check `/dashboard`.
- Rate limiting is a single DB row per agent per minute — fine for a demo, not for multi-instance production (noted in `app/rate_limit.py`).
- `RazorpayPaymentProvider` creates an order but doesn't yet handle the client-side payment capture step — this is the guardrail + order-creation layer, not a full checkout widget.

## Track fit

Every money action here is explainable (policy reason + risk factors on every decision), bounded (`policy.json` caps, structurally enforced by the state machine), and gated (human approval required for escalations, before payment). One concrete failure — a retried checkout after a dropped connection — is handled gracefully via the idempotency layer rather than assumed away.
