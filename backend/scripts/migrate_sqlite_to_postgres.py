"""
One-time migration script: copies all data from SQLite to PostgreSQL.
Run: DATABASE_URL=postgresql://user:password@host:5432/dbname python scripts/migrate_sqlite_to_postgres.py
"""

import sqlite3
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text

SQLITE_PATH = "products.db"
PG_URL = os.getenv("DATABASE_URL")

if not PG_URL:
    print("Set DATABASE_URL env var to your PostgreSQL connection string")
    sys.exit(1)

if not os.path.exists(SQLITE_PATH):
    print(f"SQLite database not found at: {SQLITE_PATH}")
    sys.exit(1)

# Read from SQLite
sqlite_conn = sqlite3.connect(SQLITE_PATH)
sqlite_conn.row_factory = sqlite3.Row

# Write to PostgreSQL
pg_engine = create_engine(PG_URL)

# Migrate products
rows = sqlite_conn.execute("SELECT * FROM products").fetchall()
print(f"Migrating {len(rows)} products...")
with pg_engine.begin() as conn:
    for row in rows:
        d = dict(row)
        cols = ", ".join(d.keys())
        placeholders = ", ".join(f":{k}" for k in d.keys())
        conn.execute(text(f"INSERT INTO products ({cols}) VALUES ({placeholders})"), d)

# Migrate brand_coo_cache
rows = sqlite_conn.execute("SELECT * FROM brand_coo_cache").fetchall()
print(f"Migrating {len(rows)} brand COO cache entries...")
with pg_engine.begin() as conn:
    for row in rows:
        d = dict(row)
        cols = ", ".join(d.keys())
        placeholders = ", ".join(f":{k}" for k in d.keys())
        conn.execute(text(f"INSERT INTO brand_coo_cache ({cols}) VALUES ({placeholders})"), d)

# Migrate scraped_pages
rows = sqlite_conn.execute("SELECT * FROM scraped_pages").fetchall()
print(f"Migrating {len(rows)} scraped pages...")
with pg_engine.begin() as conn:
    for row in rows:
        d = dict(row)
        cols = ", ".join(d.keys())
        placeholders = ", ".join(f":{k}" for k in d.keys())
        conn.execute(text(f"INSERT INTO scraped_pages ({cols}) VALUES ({placeholders})"), d)

sqlite_conn.close()
print("Migration complete!")
