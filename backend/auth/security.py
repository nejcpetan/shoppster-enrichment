"""
Password hashing and JWT token management.
"""

import os
import logging
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext

logger = logging.getLogger("auth")

# Config
_DEFAULT_SECRET = "CHANGE-ME-IN-PRODUCTION-use-openssl-rand-hex-32"
SECRET_KEY = os.getenv("JWT_SECRET", _DEFAULT_SECRET)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

if SECRET_KEY == _DEFAULT_SECRET:
    logger.warning(
        "WARNING: JWT_SECRET is set to the default insecure value. "
        "Set JWT_SECRET in your .env file before using in production."
    )

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """Decode JWT token. Returns payload dict or None if invalid/expired."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None
