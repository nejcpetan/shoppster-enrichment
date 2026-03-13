"""
Database access layer — wraps SQLAlchemy.

All functions maintain the same signatures as the original SQLite version.
Pipeline modules and main.py import from here unchanged.
"""

import json
import logging
from datetime import datetime
from sqlalchemy import text
from database.session import init_engine, get_session
from events import event_bus

logger = logging.getLogger("database")


# ---------------------------------------------------------------------------
# Compatibility layer — makes existing sqlite3-style code work unchanged
# ---------------------------------------------------------------------------

class CompatRow:
    """
    Mimics sqlite3.Row — supports both dict(row) and row['column'] access.

    dict(row) works via the mapping protocol: Python calls keys() then __getitem__.
    """

    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def __contains__(self, key):
        return key in self._data

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()

    def __iter__(self):
        """Iterate over keys (mapping protocol — enables dict(row))."""
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def get(self, key, default=None):
        return self._data.get(key, default)


class CompatResult:
    """Wraps SQLAlchemy CursorResult to provide fetchone()/fetchall() with CompatRow."""

    def __init__(self, result):
        self._result = result

    def fetchone(self):
        row = self._result.fetchone()
        if row is None:
            return None
        return CompatRow(dict(row._mapping))

    def fetchall(self):
        rows = self._result.fetchall()
        return [CompatRow(dict(r._mapping)) for r in rows]

    @property
    def lastrowid(self):
        try:
            return self._result.lastrowid
        except Exception:
            return None


def _convert_placeholders(sql: str, params: tuple) -> tuple[str, dict]:
    """
    Convert SQLite-style ? placeholders to SQLAlchemy :p0/:p1/... named params.
    Skips ? characters inside single-quoted string literals.
    """
    if "?" not in sql:
        return sql, {}

    param_dict = {}
    counter = 0
    new_sql = []
    in_string = False

    for char in sql:
        if in_string:
            new_sql.append(char)
            if char == "'":
                in_string = False
        elif char == "'":
            in_string = True
            new_sql.append(char)
        elif char == "?":
            key = f"p{counter}"
            new_sql.append(f":{key}")
            param_dict[key] = params[counter] if params and counter < len(params) else None
            counter += 1
        else:
            new_sql.append(char)

    return "".join(new_sql), param_dict


class CompatCursor:
    """
    Returned by CompatConnection.cursor() — provides .execute() for code that
    uses the cursor pattern: c = conn.cursor(); c.execute(sql, params)
    """

    def __init__(self, session):
        self._session = session
        self._last_result = None

    def execute(self, sql: str, params=None) -> CompatResult:
        if params is None:
            params = ()
        new_sql, param_dict = _convert_placeholders(sql, params)
        result = self._session.execute(text(new_sql), param_dict)
        self._last_result = CompatResult(result)
        return self._last_result

    @property
    def lastrowid(self):
        if self._last_result:
            return self._last_result.lastrowid
        return None


class CompatConnection:
    """
    Mimics sqlite3.Connection using SQLAlchemy session underneath.
    Supports the patterns used throughout main.py:
      conn = get_db_connection()
      c = conn.cursor()
      c.execute(sql, params)
      conn.execute(sql, params).fetchone()
      conn.commit()
      conn.close()
    """

    def __init__(self):
        self._session = get_session()
        self._cursor = None

    def cursor(self) -> CompatCursor:
        self._cursor = CompatCursor(self._session)
        return self._cursor

    def execute(self, sql: str, params=None) -> CompatResult:
        if params is None:
            params = ()
        new_sql, param_dict = _convert_placeholders(sql, params)
        result = self._session.execute(text(new_sql), param_dict)
        return CompatResult(result)

    def commit(self):
        self._session.commit()

    def close(self):
        self._session.close()


# ---------------------------------------------------------------------------
# Public API — same signatures as original db.py
# ---------------------------------------------------------------------------

def get_db_connection() -> CompatConnection:
    """Return a CompatConnection that mimics sqlite3.Connection."""
    return CompatConnection()


def init_db():
    """Initialize database engine and create all tables."""
    init_engine()


# ---------------------------------------------------------------------------
# Helper: SSE event publishing
# ---------------------------------------------------------------------------

def _publish_event(product_id: int, event: dict):
    """Publish an SSE event. Thread-safe — event bus handles cross-thread delivery."""
    event_bus.publish_product_event(product_id, event)


# ---------------------------------------------------------------------------
# Helper functions (used by pipeline nodes)
# ---------------------------------------------------------------------------

def update_step(product_id: int, status: str, step: str):
    """Update the current processing step for a product (real-time UI feedback)."""
    session = get_session()
    session.execute(
        text("UPDATE products SET status = :status, current_step = :step, updated_at = :now WHERE id = :id"),
        {"status": status, "step": step, "now": datetime.utcnow(), "id": product_id}
    )
    session.commit()
    session.close()

    _publish_event(product_id, {
        "type": "status",
        "status": status,
        "current_step": step,
    })


def append_log(product_id: int, entry: dict):
    """Append a log entry to the product's enrichment_log."""
    session = get_session()
    row = session.execute(
        text("SELECT enrichment_log FROM products WHERE id = :id"),
        {"id": product_id}
    ).fetchone()
    existing = json.loads(row._mapping["enrichment_log"]) if row and row._mapping["enrichment_log"] else []
    existing.append(entry)
    session.execute(
        text("UPDATE products SET enrichment_log = :log WHERE id = :id"),
        {"log": json.dumps(existing), "id": product_id}
    )
    session.commit()
    session.close()

    _publish_event(product_id, {
        "type": "log",
        "entry": entry,
    })


def save_cost_data(product_id: int, cost_summary: dict):
    """Persist the cost tracking summary for a product."""
    session = get_session()
    session.execute(
        text("UPDATE products SET cost_data = :data, updated_at = :now WHERE id = :id"),
        {"data": json.dumps(cost_summary), "now": datetime.utcnow(), "id": product_id}
    )
    session.commit()
    session.close()


def save_scraped_page(product_id: int, url: str, source_type: str, markdown: str | None, success: bool = True):
    """Cache a scraped page's markdown for potential gap-fill use."""
    session = get_session()
    ml = len(markdown) if markdown else 0
    now = datetime.utcnow()

    existing = session.execute(
        text("SELECT id FROM scraped_pages WHERE product_id = :pid AND url = :url"),
        {"pid": product_id, "url": url}
    ).fetchone()

    if existing:
        session.execute(
            text("""UPDATE scraped_pages
                     SET source_type = :st, markdown = :md, markdown_length = :ml,
                         scrape_success = :ss, scraped_at = :now
                     WHERE product_id = :pid AND url = :url"""),
            {"st": source_type, "md": markdown, "ml": ml, "ss": success,
             "now": now, "pid": product_id, "url": url}
        )
    else:
        session.execute(
            text("""INSERT INTO scraped_pages
                     (product_id, url, source_type, markdown, markdown_length, scrape_success, scraped_at)
                     VALUES (:pid, :url, :st, :md, :ml, :ss, :now)"""),
            {"pid": product_id, "url": url, "st": source_type, "md": markdown,
             "ml": ml, "ss": success, "now": now}
        )
    session.commit()
    session.close()


def get_scraped_pages(product_id: int, source_type: str | None = None, only_unextracted: bool = False) -> list[dict]:
    """Retrieve cached scraped pages for a product."""
    session = get_session()
    # Use 'IS TRUE' to work on both SQLite and PostgreSQL
    query = "SELECT * FROM scraped_pages WHERE product_id = :pid AND scrape_success IS TRUE"
    params: dict = {"pid": product_id}
    if source_type:
        query += " AND source_type = :st"
        params["st"] = source_type
    if only_unextracted:
        query += " AND extracted IS NOT TRUE AND gap_filled IS NOT TRUE"
    query += " ORDER BY id ASC"
    rows = session.execute(text(query), params).fetchall()
    session.close()
    return [dict(r._mapping) for r in rows]


def mark_page_extracted(product_id: int, url: str):
    """Mark a scraped page as having gone through main extraction."""
    session = get_session()
    session.execute(
        text("UPDATE scraped_pages SET extracted = :val WHERE product_id = :pid AND url = :url"),
        {"val": True, "pid": product_id, "url": url}
    )
    session.commit()
    session.close()


def mark_page_gap_filled(product_id: int, url: str):
    """Mark a scraped page as used for gap filling."""
    session = get_session()
    session.execute(
        text("UPDATE scraped_pages SET gap_filled = :val WHERE product_id = :pid AND url = :url"),
        {"val": True, "pid": product_id, "url": url}
    )
    session.commit()
    session.close()


def delete_scraped_pages(product_id: int):
    """Delete all cached scraped pages for a product (used on reset)."""
    session = get_session()
    session.execute(
        text("DELETE FROM scraped_pages WHERE product_id = :pid"),
        {"pid": product_id}
    )
    session.commit()
    session.close()


if __name__ == "__main__":
    init_db()
    print("Database initialized.")
