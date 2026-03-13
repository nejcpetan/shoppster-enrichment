"""
Config loader — reads company.json and provides a validated singleton.
"""

import os
import json
import logging
from pathlib import Path
from config.schema import CompanyConfig

logger = logging.getLogger("config")

_config: CompanyConfig | None = None


def load_config(config_path: str | None = None) -> CompanyConfig:
    """
    Load and validate the company config.
    Priority: config_path arg > COMPANY_CONFIG_PATH env var > config/company.json
    """
    global _config

    if config_path is None:
        config_path = os.getenv("COMPANY_CONFIG_PATH", "config/company.json")

    path = Path(config_path)

    if path.exists():
        logger.info(f"Loading company config from {path}")
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        _config = CompanyConfig.model_validate(raw)
    else:
        logger.warning(f"Config file not found at {path}, using defaults")
        _config = CompanyConfig()

    logger.info(f"Config loaded: company={_config.company_name}, source_strategy={_config.source.strategy}")
    return _config


def get_config() -> CompanyConfig:
    """
    Get the effective config: file-based defaults with DB overrides applied.
    Raises if config has not been loaded via load_config() yet.
    """
    if _config is None:
        raise RuntimeError("Config not loaded. Call load_config() on startup.")
    try:
        from settings.router import _get_all_db_overrides, _apply_overrides_to_config
        overrides = _get_all_db_overrides()
        if overrides:
            return _apply_overrides_to_config(_config, overrides)
    except Exception:
        pass
    return _config
