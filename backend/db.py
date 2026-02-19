import sqlite3
import json
import os
from datetime import datetime
from events import event_bus

DB_PATH = "products.db"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    # WAL mode on every connection — allows concurrent reads during writes
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
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
            cost_data TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Brand COO cache — stores brand → country of origin lookups to avoid
    # redundant Tavily + Claude calls for repeat brands.
    c.execute("""
        CREATE TABLE IF NOT EXISTS brand_coo_cache (
            brand TEXT PRIMARY KEY COLLATE NOCASE,
            country_of_origin TEXT NOT NULL,
            confidence TEXT NOT NULL,
            source_url TEXT,
            cached_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Scraped pages cache — stores raw markdown from Firecrawl scrapes
    # so the gap_fill node can extract missing data without re-scraping.
    c.execute("""
        CREATE TABLE IF NOT EXISTS scraped_pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            source_type TEXT NOT NULL,
            markdown TEXT,
            markdown_length INTEGER DEFAULT 0,
            scrape_success INTEGER DEFAULT 1,
            extracted INTEGER DEFAULT 0,
            gap_filled INTEGER DEFAULT 0,
            scraped_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(product_id, url)
        )
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_scraped_pages_product
        ON scraped_pages(product_id, source_type)
    """)

    # Migration: add current_step column if it doesn't exist (for existing DBs)
    # Migrations for existing DBs
    for col in ['current_step TEXT', 'cost_data TEXT']:
        try:
            c.execute(f"ALTER TABLE products ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass  # Column already exists

    conn.commit()
    conn.close()


def _publish_event(product_id: int, event: dict):
    """Publish an SSE event. Thread-safe — event bus handles cross-thread delivery."""
    event_bus.publish_product_event(product_id, event)


def update_step(product_id: int, status: str, step: str):
    """Update the current processing step for a product (real-time UI feedback)."""
    conn = get_db_connection()
    conn.execute(
        "UPDATE products SET status = ?, current_step = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, step, product_id)
    )
    conn.commit()
    conn.close()

    _publish_event(product_id, {
        "type": "status",
        "status": status,
        "current_step": step,
    })


def append_log(product_id: int, entry: dict):
    """Append a log entry to the product's enrichment_log."""
    conn = get_db_connection()
    product = conn.execute("SELECT enrichment_log FROM products WHERE id = ?", (product_id,)).fetchone()
    existing = json.loads(product['enrichment_log']) if product and product['enrichment_log'] else []
    existing.append(entry)
    conn.execute("UPDATE products SET enrichment_log = ? WHERE id = ?", (json.dumps(existing), product_id))
    conn.commit()
    conn.close()

    _publish_event(product_id, {
        "type": "log",
        "entry": entry,
    })


def save_cost_data(product_id: int, cost_summary: dict):
    """Persist the cost tracking summary for a product."""
    conn = get_db_connection()
    conn.execute(
        "UPDATE products SET cost_data = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (json.dumps(cost_summary), product_id)
    )
    conn.commit()
    conn.close()


def save_scraped_page(product_id: int, url: str, source_type: str, markdown: str | None, success: bool = True):
    """Cache a scraped page's markdown for potential gap-fill use."""
    conn = get_db_connection()
    conn.execute("""
        INSERT OR REPLACE INTO scraped_pages
        (product_id, url, source_type, markdown, markdown_length, scrape_success, scraped_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, (product_id, url, source_type, markdown, len(markdown) if markdown else 0, 1 if success else 0))
    conn.commit()
    conn.close()


def get_scraped_pages(product_id: int, source_type: str | None = None, only_unextracted: bool = False) -> list[dict]:
    """Retrieve cached scraped pages for a product."""
    conn = get_db_connection()
    query = "SELECT * FROM scraped_pages WHERE product_id = ? AND scrape_success = 1"
    params: list = [product_id]
    if source_type:
        query += " AND source_type = ?"
        params.append(source_type)
    if only_unextracted:
        query += " AND extracted = 0 AND gap_filled = 0"
    query += " ORDER BY id ASC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_page_extracted(product_id: int, url: str):
    """Mark a scraped page as having gone through main extraction."""
    conn = get_db_connection()
    conn.execute(
        "UPDATE scraped_pages SET extracted = 1 WHERE product_id = ? AND url = ?",
        (product_id, url)
    )
    conn.commit()
    conn.close()


def mark_page_gap_filled(product_id: int, url: str):
    """Mark a scraped page as used for gap filling."""
    conn = get_db_connection()
    conn.execute(
        "UPDATE scraped_pages SET gap_filled = 1 WHERE product_id = ? AND url = ?",
        (product_id, url)
    )
    conn.commit()
    conn.close()


def delete_scraped_pages(product_id: int):
    """Delete all cached scraped pages for a product (used on reset)."""
    conn = get_db_connection()
    conn.execute("DELETE FROM scraped_pages WHERE product_id = ?", (product_id,))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Database initialized.")
