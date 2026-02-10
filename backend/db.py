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
            classification_result TEXT,
            search_result TEXT,
            extraction_result TEXT,
            validation_result TEXT,
            enrichment_log TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized.")
