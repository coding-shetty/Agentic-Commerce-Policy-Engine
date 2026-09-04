# Architecture Audit: Agentic Commerce Policy Engine

## Current Architecture
The current project is a FastAPI-based proof-of-concept for a deterministic guardrail system designed to govern the financial actions of AI agents. It intercepts requests for discounts, checkouts, and refunds, and classifies them into one of three states: `APPROVED`, `ESCALATED`, or `DENIED` based on a hardcoded JSON policy file.

Key components include:
- **FastAPI application (`main.py`)**: Defines endpoints and in-memory idempotency.
- **Pydantic models (`models.py`)**: Data validation for inbound requests and outbound decisions.
- **Policy Engine (`policy_engine.py`)**: Pure python functions checking conditions.
- **Audit Logging (`audit_log.py`)**: SQLite-backed audit trails.
- **Escalation Routing (`escalation.py`)**: SQLite-backed pending approval storage.

## Existing Strengths
- **Clear separation of concerns**: The policy engine (`policy_engine.py`) is completely decoupled from any LLM, ensuring that financial authorization remains deterministic and strictly bounded.
- **Data validation**: Pydantic models are used effectively to validate inbound payload structure.
- **Three-tier decisioning**: The `APPROVED`, `ESCALATED`, `DENIED` flow models a realistic operational environment where some tasks need human review instead of absolute rejection.
- **Local DB**: SQLite is a good start for keeping state trackable in local environments.

## Existing Weaknesses
- **Floating-Point Arithmetic**: The use of `float` for `order_value`, `requested_discount_pct`, and `refund_amount` introduces precision risks when dealing with financial transactions.
- **In-Memory Idempotency**: The `_idempotency_store` is a simple Python dictionary in `main.py`, meaning it is lost on server restart, making failure recovery incomplete in real-world scenarios.
- **Shallow "Escalation" Handling**: A human approving an escalation simply flips a string in SQLite to 'approved' but does not trigger any continuation of the transaction (e.g. actually creating the Razorpay order).
- **Missing AI Agent**: There is no actual AI agent implemented. The current system expects JSON payloads directly, but the project needs a natural language-to-action agent layer.
- **Missing Payment Integration**: There is no actual payment processing layer integrated.
- **Hardcoded Policy Versioning**: Policy is unversioned. Decisions do not record which version of the policy authorized them, making historical audits unreliable if the policy changes.
- **No Transaction State Machine**: A transaction has no explicit state lifecycle like `CREATED -> POLICY_CHECKING -> APPROVED -> PAYMENT_PENDING -> COMPLETED`.

## Security Risks
- **No Authentication/Authorization**: All endpoints are fully exposed, meaning anyone can approve an escalation, request a checkout, or approve a refund.
- **Wide-open CORS**: Currently set to `allow_origins=["*"]`, a security risk in production.
- **Incomplete Idempotency Validation**: An attacker or buggy agent could submit a different payload with the same idempotency key and the system would blindly return the old response instead of raising a conflict error.
- **Missing Request/Correlation IDs**: Hard to trace a single request comprehensively through the system.
- **No Rate Limiting**: The system is susceptible to automated abuse and brute-forcing.

## Reliability Risks
- **Data Loss on Restart**: In-memory state (idempotency keys) is lost if the application crashes.
- **No Payment Failure Handling**: Without an explicit state machine, a timeout from Razorpay wouldn't be handled cleanly.

## Scalability Limitations
- **SQLite Database**: Sufficient for a buildathon, but limits concurrent writes if deployed at scale. (However, acceptable for a modular monolith).

## Missing Production Components
- Real AI Buyer Agent layer with LangChain/LiteLLM.
- Razorpay Test API Integration.
- Risk Scoring mechanism for a second-layer assessment.
- Proper Transaction State Machine and Workflow orchestrator.
- Authentication (JWT for Agent/Admin).
- Rate Limiting.
- Frontend Admin Dashboard.
- User-facing Demo UI.

## Recommended Architecture
```text
                         USER
                           |
                           v
                    AI BUYER AGENT
                           |
                           v
                 COMMERCE ORCHESTRATOR
                           |
                           v
                +----------------------+
                |   POLICY ENGINE      |
                |                      |
                | deterministic rules  |
                | limits               |
                | catalog              |
                | transaction checks   |
                +----------+-----------+
                           |
             +-------------+-------------+
             |             |             |
             v             v             v
         APPROVED      ESCALATED       DENIED
             |             |             |
             |             v             |
             |      HUMAN APPROVAL      |
             |             |             |
             +-------------+-------------+
                           |
                           v
                    PAYMENT SERVICE
                           |
                           v
                    RAZORPAY TEST API
                           |
                           v
                     AUDIT TRAIL
                           |
                           v
                    ADMIN DASHBOARD
```

## Migration Plan
1. **Model Updates**: Switch floats to integer minor units or `Decimal`. Add correlation IDs and policy versioning.
2. **Database Schema Setup**: Upgrade SQLite to support strict state tracking (Transactions, Idempotency Keys, Agents, Approvals, Payments). Use migrations (Alembic) if necessary, or a robust DB init script.
3. **Core Services Refactoring**:
    - **Policy Engine**: Enhance the policy schema to support agent-specific limits and risk scoring.
    - **Idempotency Service**: Move from in-memory dict to SQLite DB, verifying payload hashes.
4. **Auth & Security**: Add a lightweight JWT auth layer for agents and admins. Implement rate limiting.
5. **Payment Integration**: Build the Razorpay Test API client module and fake demo mode.
6. **Transaction Orchestrator**: Build a service that coordinates Agent -> Policy -> Human Approval -> Payment, ensuring explicit state transitions.
7. **AI Agent Development**: Create an LLM wrapper supporting Ollama/OpenAI to process user natural language to actionable intents (discount, checkout).
8. **UI Development**: Create a frontend for both the user demo and the admin dashboard (can use lightweight Jinja2 templates, Gradio, Streamlit, or a React static build served by FastAPI).
9. **Testing & Observability**: Introduce structlog, write robust pytest coverage covering failure recovery scenarios.

## What will be changed and why
- We are going to preserve the core deterministic policy enforcement idea as it works well, but we will wrap it in a proper `Commerce Orchestrator` that drives the entire transaction lifecycle.
- Idempotency will be migrated to the database to survive restarts and prevent payload spoofing.
- Floats will be eliminated for financial safety.
- The project will get a real LLM integration to demonstrate true "Agentic Commerce".
- A Human-in-the-loop mechanism will be expanded to actively resume transactions, proving the system's ability to halt, pause, and safely resume financial workflows.
