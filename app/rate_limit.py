"""
app/rate_limit.py

Simple DB-backed fixed-window rate limiter. Deliberately not built on
Redis/memcached — for a single-instance demo/internship project a table
with a unique constraint is reliable and needs no extra infrastructure.

Production note: a fixed-window counter has the usual edge-of-window burst
property, and a single shared DB row is a contention point under real
load. For multi-instance production deployments, use a sliding-window or
token-bucket limiter backed by Redis (e.g. `redis-py` + a Lua script for
atomicity) instead of this table.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.db_models import RateLimitBucket


def _window_start(now: datetime) -> datetime:
    return now.replace(second=0, microsecond=0)


def enforce(db: Session, agent_id: str) -> None:
    limit = settings.rate_limit_per_minute
    if limit <= 0:
        return  # rate limiting disabled

    now = datetime.now(UTC)
    window = _window_start(now)

    bucket = (
        db.query(RateLimitBucket)
        .filter(RateLimitBucket.agent_id == agent_id, RateLimitBucket.window_start == window)
        .first()
    )
    if bucket is None:
        bucket = RateLimitBucket(agent_id=agent_id, window_start=window, count=0)
        db.add(bucket)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            bucket = (
                db.query(RateLimitBucket)
                .filter(RateLimitBucket.agent_id == agent_id, RateLimitBucket.window_start == window)
                .first()
            )

    if bucket.count >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded: {limit} requests/minute per agent.",
        )

    bucket.count += 1
    db.flush()
