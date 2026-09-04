"""
buyer_agent.py

Stands in for the "AI shopping agent" the buildathon brief asks for. This is
what you actually screen-record for the 5-minute pitch — demo.py already
proves every code path works, but this file frames it as an agent making
decisions, which is what judges want to *see* rather than infer.

It reads your live policy.json so the requested amounts are always exercised
relative to your ACTUAL current bounds, not hardcoded numbers that might
drift out of sync with policy.json.

Run:
    uvicorn main:app --reload      # in one terminal
    python buyer_agent.py          # in another

Each step prints what the "agent" is asking for, then the engine's decision,
so you can narrate over it live or just let the terminal output carry the demo.
"""

import json
import time
import uuid

import requests

BASE_URL = "http://localhost:8000"
AGENT_ID = "buyer-agent-demo"


def load_policy():
    with open("policy.json") as f:
        return json.load(f)


def banner(text):
    print(f"\n{'=' * 70}\n{text}\n{'=' * 70}")


def show(label, payload, response):
    print(f"\n[{label}]")
    print(f"  agent requests: {json.dumps(payload, default=str)}")
    print(f"  engine returns: decision={response.get('decision')}  "
          f"reason={response.get('reason')}")
    if response.get("escalation_id"):
        print(f"  -> escalation_id: {response['escalation_id']} (needs a human via /approve/{{id}})")
    if response.get("razorpay_order_id"):
        print(f"  -> real Razorpay test order created: {response['razorpay_order_id']}")
    if response.get("razorpay_error"):
        print(f"  -> Razorpay call failed: {response['razorpay_error']}")
    if response.get("replayed"):
        print(f"  -> REPLAYED: {response.get('note')}")


def main():
    policy = load_policy()
    sku = policy["allowed_skus"][0]
    cap = policy["max_discount_pct"]
    multiplier = policy["hard_deny_multiplier"]
    unit_price = 500  # adjust to something sensible for your catalog/demo

    banner("STEP 1 — Agent requests a normal discount (within policy)")
    payload = {"agent_id": AGENT_ID, "sku": sku, "requested_discount_pct": max(cap - 2, 1)}
    r = requests.post(f"{BASE_URL}/discount", json=payload).json()
    show("normal discount", payload, r)

    banner("STEP 2 — Agent requests a borderline discount (should escalate)")
    borderline_pct = round(cap * (1 + (multiplier - 1) / 2), 1)
    payload = {"agent_id": AGENT_ID, "sku": sku, "requested_discount_pct": borderline_pct}
    r = requests.post(f"{BASE_URL}/discount", json=payload).json()
    show("borderline discount", payload, r)

    banner("STEP 3 — Agent tries something implausible (should deny outright)")
    absurd_pct = round(cap * multiplier * 2, 1)
    payload = {"agent_id": AGENT_ID, "sku": sku, "requested_discount_pct": absurd_pct}
    r = requests.post(f"{BASE_URL}/discount", json=payload).json()
    show("implausible discount", payload, r)

    banner("STEP 4 — Agent completes checkout at an approved discount")
    idempotency_key = str(uuid.uuid4())
    payload = {
        "agent_id": AGENT_ID,
        "idempotency_key": idempotency_key,
        "sku": sku,
        "unit_price": unit_price,
        "quantity": 1,
        "applied_discount_pct": max(cap - 2, 1),
    }
    r = requests.post(f"{BASE_URL}/checkout", json=payload).json()
    show("checkout (first attempt)", payload, r)

    banner("STEP 5 — Connection 'drops', agent retries the SAME checkout call")
    time.sleep(1)  # just for pacing in the recording
    r = requests.post(f"{BASE_URL}/checkout", json=payload).json()
    show("checkout (retry, same idempotency_key)", payload, r)
    assert r["replayed"] is True, "expected the retry to be served from the idempotency cache"

    banner("Done — every decision above is also in GET /audit for the dashboard")


if __name__ == "__main__":
    main()