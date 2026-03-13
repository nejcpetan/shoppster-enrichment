"""
Settings API — read and update company configuration at runtime.
Settings stored in DB override the file-based company.json values.
"""

import json
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from auth.dependencies import get_current_user, require_admin
from config import get_config
from config.schema import CompanyConfig
from database.session import get_session

logger = logging.getLogger("settings")
router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingUpdate(BaseModel):
    key: str    # e.g., "source.strategy"
    value: str  # JSON-encoded value, e.g., '"any_source"' or '50.0' or '["sl","en"]'


class BulkSettingUpdate(BaseModel):
    settings: list[SettingUpdate]


def _get_all_db_overrides() -> dict[str, str]:
    """Load all settings from the DB."""
    session = get_session()
    rows = session.execute(text("SELECT key, value FROM settings")).fetchall()
    session.close()
    return {row._mapping["key"]: row._mapping["value"] for row in rows}


def _apply_overrides_to_config(config: CompanyConfig, overrides: dict[str, str]) -> CompanyConfig:
    """
    Apply DB-stored overrides to the config.
    Keys are dot-separated paths like "source.strategy" or "cost.max_daily_cost_usd".
    Values are JSON-encoded.
    """
    config_dict = config.model_dump()

    for key, raw_value in overrides.items():
        value = json.loads(raw_value)
        parts = key.split(".")
        target = config_dict
        for part in parts[:-1]:
            if part in target:
                target = target[part]
            else:
                break
        else:
            target[parts[-1]] = value

    return CompanyConfig.model_validate(config_dict)


@router.get("/")
def get_all_settings(user: dict = Depends(get_current_user)):
    """Return the current effective config (file defaults + DB overrides)."""
    config = get_config()
    overrides = _get_all_db_overrides()

    if overrides:
        config = _apply_overrides_to_config(config, overrides)

    return {
        "config": config.model_dump(),
        "overrides": list(overrides.keys()),  # which keys have been overridden in DB
    }


@router.put("/")
def update_setting(update: SettingUpdate, user: dict = Depends(require_admin)):
    """Update a single setting. Stored in DB, overrides file-based config."""
    # Validate that the key path exists in the schema
    config = get_config()
    config_dict = config.model_dump()
    parts = update.key.split(".")
    target = config_dict
    for part in parts[:-1]:
        if part not in target:
            raise HTTPException(status_code=400, detail=f"Invalid config path: {update.key}")
        target = target[part]
    if parts[-1] not in target:
        raise HTTPException(status_code=400, detail=f"Invalid config key: {update.key}")

    # Validate the value by trying to apply it
    try:
        json.loads(update.value)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Value must be valid JSON")

    # Test that the full config still validates with this change
    try:
        overrides = _get_all_db_overrides()
        overrides[update.key] = update.value
        _apply_overrides_to_config(config, overrides)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid value for {update.key}: {str(e)}")

    # Save to DB
    session = get_session()
    existing = session.execute(
        text("SELECT id FROM settings WHERE key = :key"),
        {"key": update.key}
    ).fetchone()

    if existing:
        session.execute(
            text("UPDATE settings SET value = :val, updated_by = :by, updated_at = :now WHERE key = :key"),
            {"val": update.value, "by": user["email"], "now": datetime.utcnow(), "key": update.key}
        )
    else:
        session.execute(
            text("""INSERT INTO settings (key, value, updated_by, updated_at)
                    VALUES (:key, :val, :by, :now)"""),
            {"key": update.key, "val": update.value, "by": user["email"], "now": datetime.utcnow()}
        )
    session.commit()
    session.close()

    logger.info(f"Setting updated: {update.key} = {update.value} (by {user['email']})")
    return {"message": f"Setting {update.key} updated", "key": update.key}


@router.delete("/{key:path}")
def reset_setting(key: str, user: dict = Depends(require_admin)):
    """Remove a DB override, reverting to file-based default."""
    session = get_session()
    result = session.execute(
        text("DELETE FROM settings WHERE key = :key"),
        {"key": key}
    )
    session.commit()
    session.close()

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"No override found for {key}")

    return {"message": f"Setting {key} reset to default"}
