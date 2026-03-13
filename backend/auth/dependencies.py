"""
FastAPI dependencies for authentication.
Supports Bearer token in Authorization header (standard) and
?token= query parameter (fallback for SSE/EventSource).
"""

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import text
from auth.security import decode_access_token
from database.session import get_session

# auto_error=False so that SSE endpoints (which can't set headers) don't get
# rejected by FastAPI's security scheme before our function runs.
security = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    token: str = Query(None, alias="token"),  # fallback for SSE
) -> dict:
    """
    Decode JWT token and return user dict.
    Accepts token from Authorization: Bearer header OR ?token= query param.
    Raises 401 if token is missing, invalid, or user not found.
    """
    actual_token = credentials.credentials if credentials else token
    if not actual_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(actual_token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    session = get_session()
    row = session.execute(
        text("SELECT id, email, full_name, role, is_active FROM users WHERE id = :id"),
        {"id": int(user_id)}
    ).fetchone()
    session.close()

    if row is None:
        raise HTTPException(status_code=401, detail="User not found")

    user = dict(row._mapping)
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="Account disabled")

    return user


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Require the current user to have admin role."""
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
