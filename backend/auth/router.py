"""
Auth API endpoints: login, register (admin-only), me, change password.
"""

from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from auth.security import hash_password, verify_password, create_access_token
from auth.dependencies import get_current_user, require_admin
from database.session import get_session

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str
    full_name: str = ""
    role: str = "viewer"  # "admin" or "viewer"


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest):
    session = get_session()
    row = session.execute(
        text("SELECT id, email, full_name, role, is_active, hashed_password FROM users WHERE email = :email"),
        {"email": req.email.lower().strip()}
    ).fetchone()
    session.close()

    if row is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    user = dict(row._mapping)

    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="Account disabled")

    if not verify_password(req.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Update last_login
    session = get_session()
    session.execute(
        text("UPDATE users SET last_login = :now WHERE id = :id"),
        {"now": datetime.utcnow(), "id": user["id"]}
    )
    session.commit()
    session.close()

    token = create_access_token(data={"sub": str(user["id"]), "role": user["role"]})

    return TokenResponse(
        access_token=token,
        user={
            "id": user["id"],
            "email": user["email"],
            "full_name": user["full_name"],
            "role": user["role"],
        }
    )


@router.post("/register", status_code=201)
def register_user(req: RegisterRequest, admin: dict = Depends(require_admin)):
    """Create a new user. Admin-only."""
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    if req.role not in ("admin", "viewer"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'viewer'")

    session = get_session()

    # Check if email already exists
    existing = session.execute(
        text("SELECT id FROM users WHERE email = :email"),
        {"email": req.email.lower().strip()}
    ).fetchone()
    if existing:
        session.close()
        raise HTTPException(status_code=409, detail="Email already registered")

    hashed = hash_password(req.password)
    session.execute(
        text("""INSERT INTO users (email, hashed_password, full_name, role, is_active, created_at)
                VALUES (:email, :hashed, :name, :role, true, :now)"""),
        {
            "email": req.email.lower().strip(),
            "hashed": hashed,
            "name": req.full_name,
            "role": req.role,
            "now": datetime.utcnow(),
        }
    )
    session.commit()
    session.close()

    return {"message": f"User {req.email} created with role {req.role}"}


@router.get("/me")
def get_me(user: dict = Depends(get_current_user)):
    return user


@router.post("/change-password")
def change_password(req: ChangePasswordRequest, user: dict = Depends(get_current_user)):
    if len(req.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")

    session = get_session()
    row = session.execute(
        text("SELECT hashed_password FROM users WHERE id = :id"),
        {"id": user["id"]}
    ).fetchone()

    if not verify_password(req.current_password, row._mapping["hashed_password"]):
        session.close()
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    new_hash = hash_password(req.new_password)
    session.execute(
        text("UPDATE users SET hashed_password = :hash WHERE id = :id"),
        {"hash": new_hash, "id": user["id"]}
    )
    session.commit()
    session.close()

    return {"message": "Password changed successfully"}
