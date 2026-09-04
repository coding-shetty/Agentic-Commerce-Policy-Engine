"""
app/payment.py

Payment abstraction. `PaymentService.charge()` is the ONLY entry point the
orchestrator ever calls, and the orchestrator only calls it after a
transaction is APPROVED (directly, or via a resolved escalation) and has
moved to AWAITING_PAYMENT — never before. There is no code path from the
agent or the API layer directly to a payment provider.

Two providers:
  DemoPaymentProvider     - DEMO_MODE=true (default). Simulates a realistic
                             payment lifecycle deterministically, with no
                             network calls. Every response is clearly
                             labeled `"demo": true`.
  RazorpayPaymentProvider - DEMO_MODE=false. Creates a real Razorpay TEST
                             MODE order via the Orders API. Requires
                             RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET. Never
                             claims success unless Razorpay actually
                             returned one; network/timeout errors surface
                             as PAYMENT_FAILED, never as a guessed success.
"""

from __future__ import annotations

import hashlib
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

import requests
from requests.auth import HTTPBasicAuth

from app.config import settings


@dataclass
class PaymentResult:
    provider: str
    status: str  # created | failed
    provider_order_id: str | None
    provider_payment_id: str | None
    raw: dict


class PaymentError(Exception):
    """Raised on ambiguous/unknown payment state (timeout, network error,
    provider error). The orchestrator treats this as PAYMENT_FAILED and
    never retries automatically — a human/agent must explicitly retry."""


class PaymentProvider(ABC):
    name: str

    @abstractmethod
    def charge(self, *, transaction_id: str, amount_minor: int, currency: str, receipt: str) -> PaymentResult:
        ...


class DemoPaymentProvider(PaymentProvider):
    """Deterministic, offline simulation. Clearly labeled as demo in every
    response so nobody can mistake it for a real charge."""

    name = "demo"

    def charge(self, *, transaction_id: str, amount_minor: int, currency: str, receipt: str) -> PaymentResult:
        # Deterministic "order id" derived from the transaction id, so the
        # same transaction always maps to the same simulated order — useful
        # for demoing idempotent retries without introducing real randomness.
        order_id = "demo_order_" + hashlib.sha256(transaction_id.encode()).hexdigest()[:16]
        payment_id = "demo_pay_" + uuid.uuid4().hex[:16]
        return PaymentResult(
            provider=self.name,
            status="created",
            provider_order_id=order_id,
            provider_payment_id=payment_id,
            raw={
                "demo": True,
                "note": "Simulated payment — DEMO_MODE=true, no real money or network call involved.",
                "amount_minor": amount_minor,
                "currency": currency,
            },
        )


class RazorpayPaymentProvider(PaymentProvider):
    """Creates a real Razorpay order via the TEST MODE Orders API.
    https://razorpay.com/docs/api/orders/create/
    """

    name = "razorpay"

    def __init__(self) -> None:
        if not settings.razorpay_key_id or not settings.razorpay_key_secret:
            raise PaymentError(
                "DEMO_MODE is false but RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET are not configured."
            )
        self._auth = HTTPBasicAuth(settings.razorpay_key_id, settings.razorpay_key_secret)
        self._base_url = settings.razorpay_base_url.rstrip("/")

    def charge(self, *, transaction_id: str, amount_minor: int, currency: str, receipt: str) -> PaymentResult:
        payload = {
            "amount": amount_minor,  # Razorpay also expects the smallest currency unit (paise)
            "currency": currency,
            "receipt": receipt,
            "notes": {"transaction_id": transaction_id},
        }
        try:
            resp = requests.post(
                f"{self._base_url}/orders",
                json=payload,
                auth=self._auth,
                timeout=10,
            )
        except requests.exceptions.Timeout as exc:
            raise PaymentError(f"Razorpay request timed out: {exc}") from exc
        except requests.exceptions.RequestException as exc:
            raise PaymentError(f"Razorpay request failed: {exc}") from exc

        if resp.status_code >= 500:
            raise PaymentError(f"Razorpay server error: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            # Client-side error (bad request, auth failure, etc). This is a
            # definite failure, not an ambiguous one — surface it as such.
            body = _safe_json(resp)
            return PaymentResult(
                provider=self.name,
                status="failed",
                provider_order_id=None,
                provider_payment_id=None,
                raw={"http_status": resp.status_code, "body": body},
            )

        body = _safe_json(resp)
        order_id = body.get("id")
        if not order_id:
            # Ambiguous 2xx with no order id — do not claim success.
            raise PaymentError("Razorpay returned 2xx but no order id was present in the response.")

        return PaymentResult(
            provider=self.name,
            status="created",
            provider_order_id=order_id,
            provider_payment_id=None,  # payment id only exists once the buyer completes checkout client-side
            raw=body,
        )


def _safe_json(resp: requests.Response) -> dict:
    try:
        return resp.json()
    except ValueError:
        return {"raw_text": resp.text[:500]}


class PaymentService:
    """Thin façade the orchestrator depends on, so swapping providers is a
    config change, not a call-site change."""

    def __init__(self, provider: PaymentProvider | None = None) -> None:
        if provider is not None:
            self._provider = provider
        elif settings.demo_mode:
            self._provider = DemoPaymentProvider()
        else:
            self._provider = RazorpayPaymentProvider()

    @property
    def provider_name(self) -> str:
        return self._provider.name

    def charge(self, *, transaction_id: str, amount_minor: int, currency: str, receipt: str) -> PaymentResult:
        return self._provider.charge(
            transaction_id=transaction_id, amount_minor=amount_minor, currency=currency, receipt=receipt
        )
