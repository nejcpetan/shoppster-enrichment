import sqlite3
import json
import os
from datetime import datetime

DB_PATH = "products.db"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ean TEXT NOT NULL,
            product_name TEXT NOT NULL,
            brand TEXT,
            weight TEXT,
            original_data TEXT,
            status TEXT DEFAULT 'pending',
            product_type TEXT,
            current_step TEXT,
            classification_result TEXT,
            search_result TEXT,
            extraction_result TEXT,
            validation_result TEXT,
            enrichment_log TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Migration: add current_step column if it doesn't exist (for existing DBs)
    try:
        c.execute("ALTER TABLE products ADD COLUMN current_step TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    conn.commit()
    conn.close()


def update_step(product_id: int, status: str, step: str):
    """Update the current processing step for a product (real-time UI feedback)."""
    conn = get_db_connection()
    conn.execute(
        "UPDATE products SET status = ?, current_step = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, step, product_id)
    )
    conn.commit()
    conn.close()


def append_log(product_id: int, entry: dict):
    """Append a log entry to the product's enrichment_log."""
    conn = get_db_connection()
    product = conn.execute("SELECT enrichment_log FROM products WHERE id = ?", (product_id,)).fetchone()
    existing = json.loads(product['enrichment_log']) if product and product['enrichment_log'] else []
    existing.append(entry)
    conn.execute("UPDATE products SET enrichment_log = ? WHERE id = ?", (json.dumps(existing), product_id))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Database initialized.")
