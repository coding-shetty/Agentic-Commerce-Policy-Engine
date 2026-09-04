"""
app/database.py

SQLAlchemy engine/session wiring. SQLite works with zero setup for local
dev and CI; the connection string is the only thing that changes to move
to Postgres in production (see DATABASE_URL in .env.example). Models use
portable types (String for UUIDs, DateTime, Numeric/Integer for money) so
that migration is a config change, not a rewrite.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(settings.database_url, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency: one session per request, always closed."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
