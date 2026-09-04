"""
razorpay_client.py

Thin wrapper around the Razorpay Python SDK for test-mode order creation.
Deliberately dumb: this file has ZERO policy logic in it, matching the same
separation of concerns as policy_engine.py having zero I/O in it. It is only
ever called AFTER the policy engine has already approved a checkout.

Setup:
    pip install razorpay python-dotenv
    Add to requirements.txt: razorpay, python-dotenv

    In your Razorpay dashboard: Account & Settings > Websites & API keys >
    "Generate Key" (Test Mode toggle already on). Put the two values in .env:

        RAZORPAY_KEY_ID=rzp_test_xxxxxxxxxxxx
        RAZORPAY_KEY_SECRET=xxxxxxxxxxxxxxxxxxxx

    .env is already covered by your .gitignore — never commit real keys,
    even test ones, out of habit.
"""

import os

import razorpay

_client: "razorpay.Client | None" = None


class RazorpayOrderError(Exception):
    """Raised when the Razorpay API call itself fails (network, auth, bad params).
    Kept as a distinct exception so main.py can decide how to degrade —
    an approved-but-uncharged order should never look identical to a denied one."""


def get_client() -> razorpay.Client:
    global _client
    if _client is None:
        key_id = os.environ.get("RAZORPAY_KEY_ID")
        key_secret = os.environ.get("RAZORPAY_KEY_SECRET")
        if not key_id or not key_secret:
            raise RazorpayOrderError(
                "RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET not set. Generate test-mode "
                "keys in the Razorpay dashboard (Websites & API keys > Generate Key) "
                "and put them in .env."
            )
        _client = razorpay.Client(auth=(key_id, key_secret))
    return _client


def create_test_order(amount_rupees, currency: str = "INR", receipt: str | None = None,
                       notes: dict | None = None) -> dict:
    """
    Creates a real order against Razorpay's test-mode Orders API.

    amount_rupees: Decimal/float/int, in rupees (net amount after any discount —
        pass what the customer actually owes, not the gross).
    receipt: your own reference, e.g. the idempotency_key, so a support agent
        looking at the Razorpay dashboard can trace an order back to your system.

    Returns the raw order dict from Razorpay. The 'id' field (e.g.
    'order_QXyz123...') is the concrete, checkable proof this hit Razorpay's
    actual API rather than a fake response — worth pointing the judges at
    the Razorpay dashboard's Orders tab during the demo to show it there too.

    Raises RazorpayOrderError on any failure so callers can't accidentally
    treat a failed charge as a successful one.
    """
    client = get_client()
    amount_paise = int(round(float(amount_rupees) * 100))

    try:
        order = client.order.create(
            {
                "amount": amount_paise,
                "currency": currency,
                "receipt": receipt,
                "notes": notes or {},
                "payment_capture": 1,
            }
        )
    except Exception as exc:  # razorpay's SDK raises its own error types; normalize them
        raise RazorpayOrderError(f"Razorpay order creation failed: {exc}") from exc

    return order