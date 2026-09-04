"""
app/agent.py

The AI buyer agent. It can hold a natural-language conversation and decide
WHAT to ask for (negotiate a discount, request checkout), but it never
decides whether that request is allowed — every tool call here goes
through app/orchestrator.py, which enforces the deterministic policy
engine. The agent literally cannot reach app/payment.py directly; it does
not import it.

LLM backend: Ollama by default, called through LiteLLM so switching to
OpenAI/Anthropic/etc. later is a one-line config change (see
app/config.py — LLM_PROVIDER/OLLAMA_BASE_URL/OLLAMA_MODEL). If the LLM is
unreachable (Ollama not running, no model pulled) or AGENT_FORCE_FALLBACK
is set, the agent falls back to a deterministic, regex-based intent parser
and templated replies — clearly labeled as such in the response metadata.
This is NOT "faking" an LLM: it's a documented, honest fallback path so
the demo still works in environments with no local model available (e.g.
CI), while the real LLM path is exercised whenever Ollama is up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app import orchestrator
from app.config import settings
from app.policy_engine import PolicyConfig

_DISCOUNT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_CHECKOUT_WORDS = {"checkout", "buy", "purchase", "proceed", "confirm", "pay"}


@dataclass
class AgentReply:
    reply: str
    transaction: dict | None
    llm_used: bool
    metadata: dict


def _try_llm(system_prompt: str, user_message: str) -> str | None:
    """Returns the LLM's text reply, or None if the LLM couldn't be reached."""
    if settings.agent_force_fallback:
        return None
    try:
        import litellm

        model = f"ollama/{settings.ollama_model}"
        resp = litellm.completion(
            model=model,
            api_base=settings.ollama_base_url,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            timeout=8,
        )
        return resp["choices"][0]["message"]["content"]
    except Exception:
        # Ollama not running, model not pulled, network error, whatever —
        # fail closed to the deterministic fallback rather than raising.
        return None


def _parse_intent(message: str) -> dict:
    """Deterministic fallback intent parser: pulls a discount percentage
    and/or checkout intent out of the raw text. Used both as the fallback
    path and to ground the LLM's tool call in a real number either way,
    since the policy engine needs a concrete percentage, not prose."""
    wants_checkout = any(w in message.lower() for w in _CHECKOUT_WORDS)
    match = _DISCOUNT_RE.search(message)
    discount_pct = float(match.group(1)) if match else None
    return {"wants_checkout": wants_checkout, "discount_pct": discount_pct}


def handle_message(db: Session, agent_id: str, message: str, sku: str, policy: PolicyConfig) -> AgentReply:
    intent = _parse_intent(message)
    llm_text = _try_llm(
        system_prompt=(
            "You are a buyer-side shopping assistant. You can negotiate a discount "
            "percentage and request checkout, but you never decide whether a request "
            "is approved — a separate policy system decides that. Keep replies short."
        ),
        user_message=message,
    )
    llm_used = llm_text is not None

    transaction_out: dict | None = None
    metadata: dict = {"parsed_intent": intent, "fallback_used": not llm_used}

    if intent["discount_pct"] is not None:
        txn, policy_result, risk_result, approval_id = orchestrator.process_discount(
            db, agent_id, sku, intent["discount_pct"], order_value=1000.0, policy=policy
        )
        db.commit()
        transaction_out = {
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
        reply = _explain_discount(intent["discount_pct"], policy_result.decision, policy_result.reason)
    elif intent["wants_checkout"]:
        reply = (
            "To check out, call POST /checkout with a quantity, unit price, the "
            "discount you were approved for, and an idempotency_key — I can't "
            "create a payment myself, that goes through the policy-gated checkout "
            "endpoint."
        )
    else:
        reply = (
            "I can negotiate a discount for you (tell me a percentage, e.g. "
            "\"can I get 8% off?\") or help you check out once a discount is approved."
        )

    if llm_used and llm_text:
        reply = llm_text.strip() + "\n\n" + reply if transaction_out else llm_text.strip()

    return AgentReply(reply=reply, transaction=transaction_out, llm_used=llm_used, metadata=metadata)


def _explain_discount(pct: float, decision: str, reason: str) -> str:
    if decision == "approved":
        return f"Good news — a {pct}% discount is within policy and is approved. You can proceed to checkout."
    if decision == "escalated":
        return f"A {pct}% discount is outside my automatic approval range, so I've routed it to a human reviewer. {reason}"
    return f"I can't get you {pct}% off — that's outside what's allowed, even for a human to override quickly. {reason}"
