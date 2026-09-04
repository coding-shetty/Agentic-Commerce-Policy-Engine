"""
app/main.py

FastAPI service wiring together every module: auth, the policy-gated
discount/checkout/refund endpoints, human approval, the AI agent chat
endpoint, the audit trail, and a small admin dashboard + demo UI.

Architectural guarantee enforced here: AGENT-role tokens can call
/discount, /checkout, /refund, and /agent/chat. Only ADMIN-role tokens can
resolve approvals or read the raw audit trail. Nothing is reachable
without a valid bearer token except /health, /auth/login, and the demo/
dashboard HTML pages (which themselves call the authenticated JSON API
from the browser).
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import audit_log, escalation, orchestrator
from app.config import settings
from app.database import Base, engine, get_db
from app.idempotency import IdempotencyConflictError
from app.money import minor_to_rupees
from app.payment import PaymentService
from app.policy_engine import PolicyConfig
from app.rate_limit import enforce as enforce_rate_limit
from app.schemas import (
    AgentChatRequest,
    AgentChatResponse,
    ApprovalResolution,
    CheckoutRequest,
    DiscountRequest,
    LoginRequest,
    RefundRequest,
    TokenResponse,
)
from app.security import CurrentPrincipal, create_access_token, demo_login, get_current_principal, require_role

structlog.configure(processors=[structlog.processors.TimeStamper(fmt="iso"), structlog.processors.JSONRenderer()])
logger = structlog.get_logger("app")

app = FastAPI(
    title="Agentic Commerce Control Plane",
    description=(
        "A deterministic guardrail layer that sits between an AI shopping agent "
        "and any money-moving action. The LLM proposes; deterministic policy decides."
    ),
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

_policy_cache: PolicyConfig | None = None


def get_policy() -> PolicyConfig:
    global _policy_cache
    if _policy_cache is None:
        _policy_cache = PolicyConfig.load()
    return _policy_cache


def get_payment_service() -> PaymentService:
    return PaymentService()


@app.on_event("startup")
def startup() -> None:
    # Convenience for local/demo/test runs: ensures tables exist even if
    # `alembic upgrade head` hasn't been run yet. In a real deployment,
    # migrations are the source of truth (see alembic/versions/).
    Base.metadata.create_all(bind=engine)
    get_policy()


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    start = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("unhandled_error", request_id=request_id, path=request.url.path)
        raise
    duration_ms = int((time.monotonic() - start) * 1000)
    response.headers["X-Request-ID"] = request_id
    logger.info("request", request_id=request_id, path=request.url.path, status=response.status_code, duration_ms=duration_ms)
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error_code": f"http_{exc.status_code}", "message": exc.detail, "request_id": request.headers.get("X-Request-ID")},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Never leak a stack trace to the client.
    return JSONResponse(
        status_code=500,
        content={"error_code": "internal_error", "message": "An internal error occurred.", "request_id": request.headers.get("X-Request-ID")},
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health")
def health(db: Session = Depends(get_db)) -> dict[str, Any]:
    db_ok = True
    try:
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
    except Exception:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "ok" if db_ok else "unreachable",
        "demo_mode": settings.demo_mode,
        "policy_version": get_policy().version,
    }


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@app.post("/auth/login", response_model=TokenResponse)
def login(req: LoginRequest):
    role = demo_login(req.username, req.password)
    if role is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials.")
    token, expiry = create_access_token(subject=req.username, role=role)
    return TokenResponse(access_token=token, role=role, expires_in_minutes=expiry)


# ---------------------------------------------------------------------------
# Policy-gated commerce endpoints (AGENT or ADMIN)
# ---------------------------------------------------------------------------


@app.post("/discount")
def discount(
    req: DiscountRequest,
    principal: CurrentPrincipal = Depends(require_role("AGENT", "ADMIN")),
    db: Session = Depends(get_db),
    policy: PolicyConfig = Depends(get_policy),
):
    enforce_rate_limit(db, principal.agent_id)
    txn, policy_result, risk_result, approval_id = orchestrator.process_discount(
        db, principal.agent_id, req.sku, req.requested_discount_pct, req.order_value, policy
    )
    db.commit()
    return {
        "transaction_id": txn.id,
        "request_id": txn.request_id,
        "decision": policy_result.decision,
        "action": "discount",
        "reason": policy_result.reason,
        "bound_hit": policy_result.bound_hit,
        "policy_version": policy_result.policy_version,
        "risk_level": risk_result.level,
        "risk_score": risk_result.score,
        "status": txn.status,
        "approval_id": approval_id,
        "replayed": False,
    }


@app.post("/checkout")
def checkout(
    req: CheckoutRequest,
    principal: CurrentPrincipal = Depends(require_role("AGENT", "ADMIN")),
    db: Session = Depends(get_db),
    policy: PolicyConfig = Depends(get_policy),
    payment_service: PaymentService = Depends(get_payment_service),
):
    enforce_rate_limit(db, principal.agent_id)
    try:
        result = orchestrator.process_checkout(
            db,
            principal.agent_id,
            req.sku,
            req.quantity,
            req.unit_price,
            req.applied_discount_pct,
            req.idempotency_key,
            policy,
            payment_service,
        )
    except IdempotencyConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return result


@app.post("/refund")
def refund(
    req: RefundRequest,
    principal: CurrentPrincipal = Depends(require_role("AGENT", "ADMIN")),
    db: Session = Depends(get_db),
    policy: PolicyConfig = Depends(get_policy),
):
    enforce_rate_limit(db, principal.agent_id)
    txn, policy_result, risk_result, approval_id = orchestrator.process_refund(
        db, principal.agent_id, req.order_id, req.refund_amount, req.reason, policy
    )
    db.commit()
    return {
        "transaction_id": txn.id,
        "request_id": txn.request_id,
        "decision": policy_result.decision,
        "action": "refund",
        "reason": policy_result.reason,
        "bound_hit": policy_result.bound_hit,
        "policy_version": policy_result.policy_version,
        "risk_level": risk_result.level,
        "risk_score": risk_result.score,
        "status": txn.status,
        "approval_id": approval_id,
        "replayed": False,
    }


# ---------------------------------------------------------------------------
# Agent chat
# ---------------------------------------------------------------------------


@app.post("/agent/chat", response_model=AgentChatResponse)
def agent_chat(
    req: AgentChatRequest,
    principal: CurrentPrincipal = Depends(require_role("AGENT", "ADMIN")),
    db: Session = Depends(get_db),
    policy: PolicyConfig = Depends(get_policy),
):
    from app import agent as agent_module

    enforce_rate_limit(db, principal.agent_id)
    result = agent_module.handle_message(db, principal.agent_id, req.message, req.sku, policy)
    return AgentChatResponse(reply=result.reply, transaction=result.transaction, llm_used=result.llm_used, metadata=result.metadata)


# ---------------------------------------------------------------------------
# Transactions / audit (read paths)
# ---------------------------------------------------------------------------


@app.get("/transactions")
def list_transactions(
    limit: int = 50,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    from app.db_models import Transaction

    q = db.query(Transaction).order_by(Transaction.created_at.desc())
    if principal.role != "ADMIN":
        q = q.filter(Transaction.agent_id == principal.agent_id)
    rows = q.limit(limit).all()
    return [_transaction_out(t) for t in rows]


@app.get("/transactions/{transaction_id}")
def get_transaction(
    transaction_id: str,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    from app.db_models import Transaction

    txn = db.get(Transaction, transaction_id)
    if txn is None or (principal.role != "ADMIN" and txn.agent_id != principal.agent_id):
        raise HTTPException(status_code=404, detail="Transaction not found.")
    return _transaction_out(txn)


def _transaction_out(t) -> dict:
    return {
        "id": t.id,
        "request_id": t.request_id,
        "agent_id": t.agent_id,
        "action_type": t.action_type,
        "sku": t.sku,
        "status": t.status,
        "policy_decision": t.policy_decision,
        "policy_reason": t.policy_reason,
        "policy_version": t.policy_version,
        "risk_level": t.risk_level,
        "risk_score": t.risk_score,
        "net_amount_rupees": str(minor_to_rupees(t.net_minor)) if t.net_minor is not None else None,
        "currency": t.currency,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }


@app.get("/audit")
def audit(
    limit: int = 50,
    principal: CurrentPrincipal = Depends(require_role("ADMIN")),
    db: Session = Depends(get_db),
):
    events = audit_log.get_recent(db, limit)
    return [
        {
            "id": e.id,
            "timestamp": e.timestamp.isoformat(),
            "request_id": e.request_id,
            "transaction_id": e.transaction_id,
            "agent_id": e.agent_id,
            "event_type": e.event_type,
            "decision": e.decision,
            "reason": e.reason,
            "policy_version": e.policy_version,
        }
        for e in events
    ]


# ---------------------------------------------------------------------------
# Human approval (ADMIN only)
# ---------------------------------------------------------------------------


@app.get("/approvals")
def list_approvals(
    principal: CurrentPrincipal = Depends(require_role("ADMIN")),
    db: Session = Depends(get_db),
):
    pending = escalation.get_pending(db)
    return [
        {
            "id": a.id,
            "transaction_id": a.transaction_id,
            "status": a.status,
            "reason": a.reason,
            "created_at": a.created_at.isoformat(),
        }
        for a in pending
    ]


@app.post("/approve/{approval_id}")
def approve(
    approval_id: str,
    body: ApprovalResolution = ApprovalResolution(),
    principal: CurrentPrincipal = Depends(require_role("ADMIN")),
    db: Session = Depends(get_db),
    payment_service: PaymentService = Depends(get_payment_service),
):
    try:
        return orchestrator.resolve_escalation(db, approval_id, True, principal.agent_id, body.reason, payment_service)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/reject/{approval_id}")
def reject(
    approval_id: str,
    body: ApprovalResolution = ApprovalResolution(),
    principal: CurrentPrincipal = Depends(require_role("ADMIN")),
    db: Session = Depends(get_db),
    payment_service: PaymentService = Depends(get_payment_service),
):
    try:
        return orchestrator.resolve_escalation(db, approval_id, False, principal.agent_id, body.reason, payment_service)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# HTML: admin dashboard + demo UI
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def demo_ui(request: Request):
    return templates.TemplateResponse("demo.html", {"request": request})


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_ui(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})
