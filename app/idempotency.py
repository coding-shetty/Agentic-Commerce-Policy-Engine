"""
app/idempotency.py

Persistent (DB-backed, never in-memory) idempotency handling for checkout.

Behavior:
    same key + same payload      -> return the original cached result
    same key + different payload -> IdempotencyConflictError (409)
    concurrent identical request -> the unique constraint on `key` means
                                     only one row can ever be inserted; the
                                     loser of the race gets an IntegrityError,
                                     which the caller treats as "go read the
                                     row that just won."
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db_models import IdempotencyKey

DEFAULT_TTL_HOURS = 24


class IdempotencyConflictError(Exception):
    """Same key reused with a materially different payload."""


def hash_payload(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_existing(db: Session, key: str) -> IdempotencyKey | None:
    return db.get(IdempotencyKey, key)


def check_and_reserve(db: Session, key: str, agent_id: str, payload: dict) -> tuple[IdempotencyKey, bool]:
    """Returns (record, is_new). If a record already exists with a matching
    payload hash, returns it (is_new=False) for the caller to replay.
    If it exists with a different hash, raises IdempotencyConflictError.
    If it doesn't exist, inserts a 'pending' placeholder row and returns
    it as new — the caller fills in transaction_id/response afterwards."""
    payload_hash = hash_payload(payload)
    existing = get_existing(db, key)

    if existing is not None:
        if existing.request_hash != payload_hash:
            raise IdempotencyConflictError(
                f"idempotency_key '{key}' was already used with a different request payload"
            )
        return existing, False

    record = IdempotencyKey(
        key=key,
        agent_id=agent_id,
        request_hash=payload_hash,
        transaction_id="",  # filled in once the transaction is created
        status="pending",
        expires_at=datetime.now(UTC) + timedelta(hours=DEFAULT_TTL_HOURS),
    )
    db.add(record)
    try:
        db.flush()  # surfaces the unique-constraint race here, not later
    except IntegrityError as exc:
        db.rollback()
        # Someone else won the race and inserted the same key concurrently.
        winner = get_existing(db, key)
        if winner is not None and winner.request_hash == payload_hash:
            return winner, False
        raise IdempotencyConflictError(
            f"idempotency_key '{key}' is already in use"
        ) from exc
    return record, True


def complete(db: Session, record: IdempotencyKey, transaction_id: str, response: dict) -> None:
    record.transaction_id = transaction_id
    record.status = "completed"
    record.response_json = json.dumps(response)
    db.flush()
