"""Tests for Phase 2: Database compatibility layer and ORM models."""

import pytest


# ---------------------------------------------------------------------------
# CompatRow
# ---------------------------------------------------------------------------

class TestCompatRow:
    def test_getitem(self):
        from db import CompatRow
        row = CompatRow({"ean": "123", "name": "Widget"})
        assert row["ean"] == "123"
        assert row["name"] == "Widget"

    def test_dict_conversion(self):
        from db import CompatRow
        row = CompatRow({"ean": "123", "name": "Widget"})
        d = dict(row)
        assert d == {"ean": "123", "name": "Widget"}

    def test_keys(self):
        from db import CompatRow
        row = CompatRow({"a": 1, "b": 2})
        assert set(row.keys()) == {"a", "b"}

    def test_contains(self):
        from db import CompatRow
        row = CompatRow({"ean": "123"})
        assert "ean" in row
        assert "missing" not in row

    def test_len(self):
        from db import CompatRow
        row = CompatRow({"a": 1, "b": 2, "c": 3})
        assert len(row) == 3

    def test_get_with_default(self):
        from db import CompatRow
        row = CompatRow({"a": 1})
        assert row.get("a") == 1
        assert row.get("missing", "default") == "default"


# ---------------------------------------------------------------------------
# Placeholder conversion
# ---------------------------------------------------------------------------

class TestPlaceholderConversion:
    def test_single_placeholder(self):
        from db import _convert_placeholders
        sql, params = _convert_placeholders("SELECT * FROM t WHERE id = ?", (42,))
        assert ":p0" in sql
        assert "?" not in sql
        assert params["p0"] == 42

    def test_multiple_placeholders(self):
        from db import _convert_placeholders
        sql, params = _convert_placeholders(
            "INSERT INTO t (a, b, c) VALUES (?, ?, ?)", ("x", "y", "z")
        )
        assert sql.count(":p") == 3
        assert params["p0"] == "x"
        assert params["p1"] == "y"
        assert params["p2"] == "z"

    def test_no_placeholders(self):
        from db import _convert_placeholders
        sql, params = _convert_placeholders("SELECT 1", ())
        assert sql == "SELECT 1"
        assert params == {}

    def test_placeholder_in_string_literal_skipped(self):
        from db import _convert_placeholders
        sql, params = _convert_placeholders("SELECT * FROM t WHERE name = '?' AND id = ?", (1,))
        # The ? inside quotes should NOT be converted
        assert params.get("p0") == 1
        assert "?" in sql  # The quoted one remains


# ---------------------------------------------------------------------------
# CompatConnection end-to-end with real DB
# ---------------------------------------------------------------------------

class TestCompatConnection:
    def test_insert_and_select(self):
        from db import get_db_connection
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO products (ean, product_name, status) VALUES (?, ?, ?)",
            ("9999", "Test Prod", "pending"),
        )
        conn.commit()

        row = conn.execute("SELECT * FROM products WHERE ean = ?", ("9999",)).fetchone()
        assert row is not None
        assert row["ean"] == "9999"
        assert row["product_name"] == "Test Prod"
        assert dict(row)["status"] == "pending"
        conn.close()

    def test_cursor_pattern(self):
        from db import get_db_connection
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "INSERT INTO products (ean, product_name, status) VALUES (?, ?, ?)",
            ("8888", "Cursor Prod", "pending"),
        )
        conn.commit()

        result = c.execute("SELECT product_name FROM products WHERE ean = ?", ("8888",))
        row = result.fetchone()
        assert row["product_name"] == "Cursor Prod"
        conn.close()

    def test_fetchall(self):
        from db import get_db_connection
        conn = get_db_connection()
        conn.execute("INSERT INTO products (ean, product_name, status) VALUES (?, ?, ?)", ("1", "A", "pending"))
        conn.execute("INSERT INTO products (ean, product_name, status) VALUES (?, ?, ?)", ("2", "B", "pending"))
        conn.commit()

        rows = conn.execute("SELECT * FROM products ORDER BY ean").fetchall()
        assert len(rows) == 2
        assert rows[0]["ean"] == "1"
        assert rows[1]["ean"] == "2"
        conn.close()

    def test_fetchone_returns_none_for_no_match(self):
        from db import get_db_connection
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM products WHERE ean = ?", ("nonexistent",)).fetchone()
        assert row is None
        conn.close()


# ---------------------------------------------------------------------------
# Helper functions (update_step, append_log, scraped pages)
# ---------------------------------------------------------------------------

class TestDbHelpers:
    def _insert_product(self):
        from db import get_db_connection
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO products (ean, product_name, status) VALUES (?, ?, ?)",
            ("7777", "Helper Prod", "pending"),
        )
        conn.commit()
        row = conn.execute("SELECT id FROM products WHERE ean = ?", ("7777",)).fetchone()
        pid = row["id"]
        conn.close()
        return pid

    def test_update_step(self):
        from db import update_step, get_db_connection
        pid = self._insert_product()
        update_step(pid, "processing", "triage")

        conn = get_db_connection()
        row = conn.execute("SELECT status, current_step FROM products WHERE id = ?", (pid,)).fetchone()
        assert row["status"] == "processing"
        assert row["current_step"] == "triage"
        conn.close()

    def test_append_log(self):
        import json
        from db import append_log, get_db_connection
        pid = self._insert_product()

        append_log(pid, {"step": "triage", "msg": "classified"})
        append_log(pid, {"step": "search", "msg": "found 3 results"})

        conn = get_db_connection()
        row = conn.execute("SELECT enrichment_log FROM products WHERE id = ?", (pid,)).fetchone()
        log = json.loads(row["enrichment_log"])
        assert len(log) == 2
        assert log[0]["step"] == "triage"
        assert log[1]["step"] == "search"
        conn.close()

    def test_save_and_get_scraped_pages(self):
        from db import save_scraped_page, get_scraped_pages
        pid = self._insert_product()

        save_scraped_page(pid, "https://example.com/a", "manufacturer", "# Page A", True)
        save_scraped_page(pid, "https://example.com/b", "third_party", "# Page B", True)

        pages = get_scraped_pages(pid)
        assert len(pages) == 2

        mfr_pages = get_scraped_pages(pid, source_type="manufacturer")
        assert len(mfr_pages) == 1
        assert mfr_pages[0]["url"] == "https://example.com/a"

    def test_mark_page_extracted(self):
        from db import save_scraped_page, mark_page_extracted, get_scraped_pages
        pid = self._insert_product()
        save_scraped_page(pid, "https://example.com/x", "manufacturer", "# X", True)

        mark_page_extracted(pid, "https://example.com/x")

        pages = get_scraped_pages(pid, only_unextracted=True)
        assert len(pages) == 0  # extracted page is filtered out

    def test_delete_scraped_pages(self):
        from db import save_scraped_page, delete_scraped_pages, get_scraped_pages
        pid = self._insert_product()
        save_scraped_page(pid, "https://example.com/y", "manufacturer", "# Y", True)

        delete_scraped_pages(pid)
        assert get_scraped_pages(pid) == []

    def test_save_scraped_page_upsert(self):
        from db import save_scraped_page, get_scraped_pages
        pid = self._insert_product()
        save_scraped_page(pid, "https://example.com/z", "manufacturer", "# V1", True)
        save_scraped_page(pid, "https://example.com/z", "manufacturer", "# V2 updated", True)

        pages = get_scraped_pages(pid)
        assert len(pages) == 1
        assert pages[0]["markdown"] == "# V2 updated"


# ---------------------------------------------------------------------------
# Database session management
# ---------------------------------------------------------------------------

class TestDatabaseSession:
    def test_get_database_url_default(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        from database.session import get_database_url
        url = get_database_url()
        assert url.startswith("sqlite")

    def test_get_database_url_postgres(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@host:5432/db")
        from database.session import get_database_url
        url = get_database_url()
        assert url.startswith("postgresql://")

    def test_get_database_url_postgresql(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host:5432/db")
        from database.session import get_database_url
        url = get_database_url()
        assert url == "postgresql://user:pass@host:5432/db"
