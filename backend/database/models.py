"""
SQLAlchemy ORM models — mirrors the existing SQLite schema exactly.
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ean = Column(String, nullable=False)
    product_name = Column(String, nullable=False)
    brand = Column(String, nullable=True)
    weight = Column(String, nullable=True)
    original_data = Column(Text, nullable=True)
    status = Column(String, default="pending")
    product_type = Column(String, nullable=True)
    current_step = Column(String, nullable=True)
    classification_result = Column(Text, nullable=True)
    search_result = Column(Text, nullable=True)
    extraction_result = Column(Text, nullable=True)
    validation_result = Column(Text, nullable=True)
    enrichment_log = Column(Text, nullable=True)
    cost_data = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BrandCooCache(Base):
    __tablename__ = "brand_coo_cache"

    brand = Column(String, primary_key=True)
    country_of_origin = Column(String, nullable=False)
    confidence = Column(String, nullable=False)
    source_url = Column(String, nullable=True)
    cached_at = Column(DateTime, default=datetime.utcnow)


class ScrapedPage(Base):
    __tablename__ = "scraped_pages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, nullable=False)
    url = Column(String, nullable=False)
    source_type = Column(String, nullable=False)
    markdown = Column(Text, nullable=True)
    markdown_length = Column(Integer, default=0)
    scrape_success = Column(Boolean, default=True)
    extracted = Column(Boolean, default=False)
    gap_filled = Column(Boolean, default=False)
    scraped_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("product_id", "url", name="uq_scraped_product_url"),
        Index("idx_scraped_pages_product", "product_id", "source_type"),
    )


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=True)
    role = Column(String, default="viewer")  # "admin" or "viewer"
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)


class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String, unique=True, nullable=False, index=True)
    value = Column(Text, nullable=False)  # JSON-encoded value
    updated_by = Column(String, nullable=True)  # email of who changed it
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
