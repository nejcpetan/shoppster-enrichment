"""
Database session management.
Supports both PostgreSQL (production) and SQLite (local dev fallback).
"""

import os
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from database.models import Base

logger = logging.getLogger("database")

_engine = None
_SessionLocal = None


def get_database_url() -> str:
    """
    Determine database URL.
    Priority: DATABASE_URL env var > default SQLite file.
    """
    url = os.getenv("DATABASE_URL")
    if url:
        # Handle Heroku/Railway-style postgres:// -> postgresql://
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url
    # Fallback: SQLite for local development
    return "sqlite:///products.db"


def init_engine():
    """Create the database engine and tables."""
    global _engine, _SessionLocal

    url = get_database_url()
    is_sqlite = url.startswith("sqlite")

    connect_args = {}
    if is_sqlite:
        connect_args["check_same_thread"] = False

    _engine = create_engine(
        url,
        connect_args=connect_args,
        pool_pre_ping=True,
        echo=False,
    )

    # Create all tables (idempotent)
    Base.metadata.create_all(_engine)

    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)

    db_type = "SQLite" if is_sqlite else "PostgreSQL"
    logger.info(f"Database initialized: {db_type}")


def get_session() -> Session:
    """Get a new database session."""
    if _SessionLocal is None:
        init_engine()
    return _SessionLocal()
