"""
app/security.py

JWT issuance/verification and password hashing. Two roles only: AGENT and
ADMIN. All money-moving and approval endpoints require a valid bearer
token; role checks are centralized in `require_role` so no endpoint can
"forget" to check.

Demo auth: when DEMO_AUTH_ENABLED is true (the default for local/demo use),
POST /auth/login accepts username "agent" or "admin" with
DEMO_AUTH_PASSWORD and issues a real JWT for that role — no separate user
store needed to run the demo end-to-end. This is clearly a demo shortcut,
not a real user-management system; disable it and pre-seed real Agent rows
(see app/db_models.py) for anything beyond a local demo.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from pydantic import BaseModel

from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer = HTTPBearer(auto_error=False)


class TokenPayload(BaseModel):
    sub: str  # agent_id / username
    role: str  # AGENT | ADMIN
    exp: int


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


def create_access_token(subject: str, role: str) -> tuple[str, int]:
    expiry_minutes = settings.jwt_expiry_minutes
    expire = datetime.now(UTC) + timedelta(minutes=expiry_minutes)
    payload = {"sub": subject, "role": role, "exp": int(expire.timestamp())}
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expiry_minutes


def decode_token(token: str) -> TokenPayload:
    try:
        raw = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired") from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc
    return TokenPayload(**raw)


class CurrentPrincipal(BaseModel):
    agent_id: str
    role: str


def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> CurrentPrincipal:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_token(credentials.credentials)
    return CurrentPrincipal(agent_id=payload.sub, role=payload.role)


def require_role(*allowed_roles: str):
    """FastAPI dependency factory: require_role('ADMIN') or require_role('AGENT', 'ADMIN')."""

    def _checker(principal: CurrentPrincipal = Depends(get_current_principal)) -> CurrentPrincipal:
        if principal.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{principal.role}' is not permitted to perform this action.",
            )
        return principal

    return _checker


def demo_login(username: str, password: str) -> str | None:
    """Returns a role string if the demo credentials are valid, else None.
    Only consulted when settings.demo_auth_enabled is True."""
    if not settings.demo_auth_enabled:
        return None
    if password != settings.demo_auth_password:
        return None
    if username == "admin":
        return "ADMIN"
    if username == "agent":
        return "AGENT"
    return None
