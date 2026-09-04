"""
demo.py

Run this against the live server (`uvicorn main:app --reload`) to walk
through every decision path in one shot. This is basically your pitch
video's shot list translated into code — run it on screen, narrate what's
happening, and you've shown Problem Taste, AI Judgment, and Failure
Recovery in about 90 seconds of footage.

Usage:
    uvicorn main:app --reload &
    python demo.py
"""

import time

import requests

BASE = "http://127.0.0.1:8000"


def show(label, resp):
    print(f"\n--- {label} ---")
    print(resp.json())


def main():
    # 1. A normal, in-bounds discount request -> APPROVED
    show(
        "1. Discount within cap (should APPROVE)",
        requests.post(f"{BASE}/discount", json={
            "agent_id": "buyer-agent-1",
            "sku": "SKU-001",
            "requested_discount_pct": 8,
            "order_value": 1200,
        }),
    )

    # 2. A discount just past the cap -> ESCALATED (human review)
    show(
        "2. Discount past cap but reasonable (should ESCALATE)",
        requests.post(f"{BASE}/discount", json={
            "agent_id": "buyer-agent-1",
            "sku": "SKU-001",
            "requested_discount_pct": 15,
            "order_value": 1200,
        }),
    )

    # 3. A wildly excessive discount -> DENIED outright
    show(
        "3. Discount way past cap (should DENY outright, not escalate)",
        requests.post(f"{BASE}/discount", json={
            "agent_id": "buyer-agent-1",
            "sku": "SKU-001",
            "requested_discount_pct": 60,
            "order_value": 1200,
        }),
    )

    # 4. Checkout, first attempt
    idempotency_key = "demo-order-abc123"
    show(
        "4. Checkout, first attempt (should APPROVE)",
        requests.post(f"{BASE}/checkout", json={
            "agent_id": "buyer-agent-1",
            "sku": "SKU-001",
            "quantity": 2,
            "unit_price": 500,
            "applied_discount_pct": 8,
            "idempotency_key": idempotency_key,
        }),
    )

    # 5. Same checkout, retried with the SAME idempotency_key — this is the
    #    staged "failure": pretend the first response got lost on a flaky
    #    connection and the agent retries. Should NOT double-charge.
    time.sleep(0.5)
    show(
        "5. Checkout RETRY, same idempotency_key (should replay, not double-charge)",
        requests.post(f"{BASE}/checkout", json={
            "agent_id": "buyer-agent-1",
            "sku": "SKU-001",
            "quantity": 2,
            "unit_price": 500,
            "applied_discount_pct": 8,
            "idempotency_key": idempotency_key,
        }),
    )

    # 6. Pull the audit trail — this is what the dashboard renders
    show("6. Audit trail (last 10 decisions)", requests.get(f"{BASE}/audit?limit=10"))


if __name__ == "__main__":
    main()
