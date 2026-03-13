"""Tests for Phase 1: Company Config System."""

import os
import json
import pytest


# ---------------------------------------------------------------------------
# Schema defaults
# ---------------------------------------------------------------------------

class TestCompanyConfigDefaults:
    def test_default_company_name(self):
        from config.schema import CompanyConfig
        cfg = CompanyConfig()
        assert cfg.company_name == "Default"
        assert cfg.company_slug == "default"

    def test_default_source_strategy(self):
        from config.schema import CompanyConfig
        cfg = CompanyConfig()
        assert cfg.source.strategy == "official_first"
        assert cfg.source.trust_third_party_as_primary is False

    def test_default_brands_list(self):
        from config.schema import CompanyConfig
        cfg = CompanyConfig()
        assert "Makita" in cfg.brands.known_brands
        assert "Bosch" in cfg.brands.known_brands
        assert len(cfg.brands.known_brands) == 15

    def test_default_cost_limits(self):
        from config.schema import CompanyConfig
        cfg = CompanyConfig()
        assert cfg.cost.max_daily_cost_usd == 50.0
        assert cfg.cost.daily_product_limit == 200
        assert cfg.cost.max_batch_size == 50

    def test_default_critical_fields(self):
        from config.schema import CompanyConfig
        cfg = CompanyConfig()
        assert cfg.critical_fields.net_weight is True
        assert cfg.critical_fields.color is False
        assert cfg.critical_fields.country_of_origin is False

    def test_default_llm_models_are_haiku(self):
        from config.schema import CompanyConfig
        cfg = CompanyConfig()
        assert cfg.llm.triage_model == "haiku"
        assert cfg.llm.extract_model == "haiku"
        assert cfg.llm.validate_model == "haiku"


# ---------------------------------------------------------------------------
# Loading from JSON files
# ---------------------------------------------------------------------------

class TestConfigLoader:
    def test_load_shoppster_config(self):
        from config.loader import load_config
        path = os.path.join(os.path.dirname(__file__), "..", "config", "shoppster.json")
        cfg = load_config(path)
        assert cfg.company_name == "Shoppster"
        assert cfg.source.strategy == "official_first"
        assert cfg.source.trust_third_party_as_primary is False

    def test_load_merkur_config(self):
        from config.loader import load_config
        path = os.path.join(os.path.dirname(__file__), "..", "config", "merkur.json")
        cfg = load_config(path)
        assert cfg.company_name == "Merkur"
        assert cfg.source.strategy == "any_source"
        assert cfg.source.trust_third_party_as_primary is True
        assert cfg.pipeline.gap_fill_is_primary is True
        assert cfg.cost.market_region == "Slovenia"

    def test_merkur_extra_color_mappings(self):
        from config.loader import load_config
        path = os.path.join(os.path.dirname(__file__), "..", "config", "merkur.json")
        cfg = load_config(path)
        assert "türkis" in cfg.language.extra_color_mappings
        assert cfg.language.extra_color_mappings["türkis"] == "turquoise"

    def test_missing_config_uses_defaults(self, tmp_path):
        from config.loader import load_config
        cfg = load_config(str(tmp_path / "nonexistent.json"))
        assert cfg.company_name == "Default"

    def test_partial_config_fills_defaults(self, tmp_path):
        partial = tmp_path / "partial.json"
        partial.write_text(json.dumps({"company_name": "PartialCo"}))
        from config.loader import load_config
        cfg = load_config(str(partial))
        assert cfg.company_name == "PartialCo"
        assert cfg.source.strategy == "official_first"  # default filled in

    def test_get_config_raises_before_load(self):
        from config.loader import get_config
        with pytest.raises(RuntimeError, match="Config not loaded"):
            get_config()

    def test_get_config_returns_loaded(self):
        from config.loader import load_config, get_config
        path = os.path.join(os.path.dirname(__file__), "..", "config", "shoppster.json")
        load_config(path)
        cfg = get_config()
        assert cfg.company_name == "Shoppster"


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

class TestConfigValidation:
    def test_invalid_field_type_rejected(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"cost": {"max_daily_cost_usd": "not_a_number"}}))
        from config.loader import load_config
        with pytest.raises(Exception):
            load_config(str(bad))

    def test_extra_fields_ignored(self, tmp_path):
        extended = tmp_path / "extended.json"
        extended.write_text(json.dumps({"company_name": "Test", "unknown_field": 123}))
        from config.loader import load_config
        cfg = load_config(str(extended))
        assert cfg.company_name == "Test"
