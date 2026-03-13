"""Tests for Phase 4: Settings API."""

import json
import pytest
from tests.conftest import auth_header


# ---------------------------------------------------------------------------
# Settings override logic (unit tests — no HTTP)
# ---------------------------------------------------------------------------

class TestApplyOverrides:
    def test_apply_single_override(self):
        from config.schema import CompanyConfig
        from settings.router import _apply_overrides_to_config

        cfg = CompanyConfig(company_name="Test")
        result = _apply_overrides_to_config(cfg, {"cost.max_daily_cost_usd": "100.0"})
        assert result.cost.max_daily_cost_usd == 100.0
        assert result.company_name == "Test"  # unchanged

    def test_apply_nested_override(self):
        from config.schema import CompanyConfig
        from settings.router import _apply_overrides_to_config

        cfg = CompanyConfig()
        result = _apply_overrides_to_config(cfg, {"source.strategy": '"any_source"'})
        assert result.source.strategy == "any_source"

    def test_apply_list_override(self):
        from config.schema import CompanyConfig
        from settings.router import _apply_overrides_to_config

        cfg = CompanyConfig()
        result = _apply_overrides_to_config(cfg, {
            "language.primary_languages": '["de", "en"]'
        })
        assert result.language.primary_languages == ["de", "en"]

    def test_apply_bool_override(self):
        from config.schema import CompanyConfig
        from settings.router import _apply_overrides_to_config

        cfg = CompanyConfig()
        assert cfg.source.trust_third_party_as_primary is False
        result = _apply_overrides_to_config(cfg, {"source.trust_third_party_as_primary": "true"})
        assert result.source.trust_third_party_as_primary is True

    def test_multiple_overrides(self):
        from config.schema import CompanyConfig
        from settings.router import _apply_overrides_to_config

        cfg = CompanyConfig()
        result = _apply_overrides_to_config(cfg, {
            "cost.max_daily_cost_usd": "75.0",
            "cost.daily_product_limit": "500",
            "source.strategy": '"any_source"',
        })
        assert result.cost.max_daily_cost_usd == 75.0
        assert result.cost.daily_product_limit == 500
        assert result.source.strategy == "any_source"

    def test_invalid_path_ignored(self):
        from config.schema import CompanyConfig
        from settings.router import _apply_overrides_to_config

        cfg = CompanyConfig()
        # Bad path — should not crash, just be ignored
        result = _apply_overrides_to_config(cfg, {"nonexistent.key": '"val"'})
        assert result.company_name == "Default"


# ---------------------------------------------------------------------------
# Settings API endpoints
# ---------------------------------------------------------------------------

class TestSettingsAPI:
    def test_get_settings_unauthenticated(self, client):
        resp = client.get("/api/settings/")
        assert resp.status_code in (401, 403)

    def test_get_settings_as_viewer(self, client, viewer_token):
        resp = client.get("/api/settings/", headers=auth_header(viewer_token))
        assert resp.status_code == 200
        data = resp.json()
        assert "config" in data
        assert "overrides" in data
        assert data["overrides"] == []

    def test_get_settings_as_admin(self, client, admin_token):
        resp = client.get("/api/settings/", headers=auth_header(admin_token))
        assert resp.status_code == 200
        assert resp.json()["config"]["company_name"] == "Shoppster"

    def test_update_setting(self, client, admin_token):
        resp = client.put(
            "/api/settings/",
            json={"key": "cost.max_daily_cost_usd", "value": "99.9"},
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 200

        # Verify override is reflected
        resp2 = client.get("/api/settings/", headers=auth_header(admin_token))
        data = resp2.json()
        assert data["config"]["cost"]["max_daily_cost_usd"] == 99.9
        assert "cost.max_daily_cost_usd" in data["overrides"]

    def test_update_setting_viewer_forbidden(self, client, viewer_token):
        resp = client.put(
            "/api/settings/",
            json={"key": "cost.max_daily_cost_usd", "value": "99.9"},
            headers=auth_header(viewer_token),
        )
        assert resp.status_code == 403

    def test_update_invalid_key(self, client, admin_token):
        resp = client.put(
            "/api/settings/",
            json={"key": "nonexistent.path.here", "value": '"foo"'},
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 400

    def test_update_invalid_json_value(self, client, admin_token):
        resp = client.put(
            "/api/settings/",
            json={"key": "cost.max_daily_cost_usd", "value": "not json"},
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 400

    def test_reset_setting(self, client, admin_token):
        # Set an override
        client.put(
            "/api/settings/",
            json={"key": "cost.max_daily_cost_usd", "value": "123.0"},
            headers=auth_header(admin_token),
        )

        # Reset it
        resp = client.delete("/api/settings/cost.max_daily_cost_usd", headers=auth_header(admin_token))
        assert resp.status_code == 200

        # Verify it reverted to default
        resp2 = client.get("/api/settings/", headers=auth_header(admin_token))
        data = resp2.json()
        assert data["config"]["cost"]["max_daily_cost_usd"] == 50.0
        assert "cost.max_daily_cost_usd" not in data["overrides"]

    def test_reset_nonexistent_override(self, client, admin_token):
        resp = client.delete("/api/settings/cost.max_daily_cost_usd", headers=auth_header(admin_token))
        assert resp.status_code == 404

    def test_reset_viewer_forbidden(self, client, viewer_token):
        resp = client.delete("/api/settings/cost.max_daily_cost_usd", headers=auth_header(viewer_token))
        assert resp.status_code == 403
