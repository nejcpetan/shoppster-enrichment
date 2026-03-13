# SaaS Migration Plan — Product Enrichment Engine

## Why We're Doing This

This product enrichment engine currently works great — for one customer (Shoppster). But every company has different needs:

- **Shoppster** sells branded products (Makita, Bosch, etc.) that have rich official data on manufacturer websites. Their strategy is "search official sources first, only use third-party as a fallback."
- **Merkur** (a potential new customer) sells products that often have NO official online presence. No manufacturer website, no authorized distributor page. Their data lives on random retailer sites, comparison sites, forums. They need "search everywhere, clean the messy data afterward."
- **Future customers** will have their own unique needs — different languages, different product types, different quality thresholds, different brands.

Right now, all of this behavior is hardcoded: the brand list is in a Python string, the search strategy is an `if` statement, the critical fields are lambda functions in a dict. Signing a new customer means forking the codebase or doing surgery on every pipeline file.

**The fix:** Extract all company-specific behavior into a config file. Each customer gets their own config. The pipeline code becomes generic — it reads config and acts accordingly. New customer = new JSON file, zero code changes.

**The business model:** We're turning this into a SaaS product. Each customer gets their own isolated instance (Docker container + database). We host it on a single VPS for now. Later, customers can self-host if they want (some enterprises won't let product data flow through external infrastructure). The system has built-in auth (login/password), so each customer's team can log in and manage their enrichment pipeline.

**Why separate instances instead of shared database?** With max 10 customers, separate instances are simpler, safer, and easier to debug. Each customer has their own database — no risk of data leaking between tenants. If Merkur's instance crashes, Shoppster is unaffected. And when a customer wants to self-host, you literally hand them their Docker Compose file.

---

## Architecture Overview

**Goal:** Transform the single-tenant product enrichment engine into a multi-instance SaaS product that can be deployed per-customer via Docker.

**Deployment model:** One Docker Compose stack per customer (separate instance + separate Postgres DB per customer). All instances use the same Docker image. Updates are pushed via a container registry and pulled on the VPS.

**Stack:**
- Backend: FastAPI (Python) — same as current
- Database: PostgreSQL (replaces SQLite) via SQLAlchemy + Alembic
- Auth: Built-in (bcrypt + JWT) — no external dependencies
- Config: JSON file per instance + settings registry in code
- Delivery: Docker Compose (FastAPI + Postgres per customer)
- Hosting: Single VPS (e.g., Hetzner €8/mo) running all customer stacks

**Directory structure on VPS:**
```
/opt/enrichment/
├── shoppster/
│   ├── docker-compose.yml
│   ├── .env
│   └── config/company.json
├── merkur/
│   ├── docker-compose.yml
│   ├── .env
│   └── config/company.json
└── update-all.sh
```

---

## Phase 1: Company Config System

**Goal:** Extract all hardcoded company-specific values into a JSON config file that each instance loads on startup. The pipeline reads from this config instead of hardcoded values.

**Files to CREATE:**
- `backend/config/schema.py` — Config registry (Pydantic models for validation)
- `backend/config/loader.py` — Loads and validates `company.json`, provides a singleton
- `backend/config/defaults.py` — Platform defaults (what Shoppster uses today)
- `backend/config/shoppster.json` — Shoppster's config (mirrors current hardcoded behavior)
- `backend/config/merkur.json` — Merkur's config (any-source strategy)

**Files to MODIFY:**
- `backend/pipeline/triage.py` — Read known brands + system prompt from config
- `backend/pipeline/search.py` — Read source strategy, allowed/blocked domains from config
- `backend/pipeline/extract.py` — Read source tier behavior from config
- `backend/pipeline/gap_fill.py` — Read critical gap definitions from config
- `backend/pipeline/validate.py` — Read color map, country map, junk values from config
- `backend/utils/cost_tracker.py` — Read cost limits from config
- `backend/main.py` — Load config on startup, pass to pipeline

### Step 1.1: Create `backend/config/schema.py`

Create a Pydantic model that defines every configurable setting. This is the single source of truth for what can be configured.

```python
"""
Company configuration schema — defines all per-instance settings.
Every field has a default value matching current Shoppster behavior.
"""

from pydantic import BaseModel, Field
from typing import Optional


class SourceConfig(BaseModel):
    """Controls how the pipeline finds and trusts data sources."""
    strategy: str = "official_first"
    # "official_first" = search manufacturer domain first, fall back to general (current Shoppster behavior)
    # "any_source" = search broadly, no manufacturer domain preference (Merkur)
    # "official_only" = only use manufacturer + authorized distributor, never third-party

    search_provider: str = "tavily"  # "tavily" or "firecrawl"
    max_manufacturer_results: int = 5
    max_general_results: int = 7
    max_general_queries: int = 3
    max_total_results: int = 6  # stop searching after this many total results

    allowed_domains: list[str] = Field(default_factory=list)
    # If non-empty, ONLY search these domains (whitelist mode)

    blocked_domains: list[str] = Field(default_factory=list)
    # Never include results from these domains

    trust_third_party_as_primary: bool = False
    # If True, third-party sources are treated with same weight as authorized (Merkur mode)


class PipelineConfig(BaseModel):
    """Controls pipeline flow and behavior."""
    enable_ean_lookup: bool = True
    enable_gap_fill: bool = True
    enable_gemini_vision: bool = True  # color detection via Gemini

    max_pages_to_scrape: int = 5  # max pages to scrape per product in extract phase
    max_gap_fill_pages: int = 5  # max third-party pages to check for gap fill

    gap_fill_is_primary: bool = False
    # If True, gap fill runs with higher priority (Merkur: data often only on third-party sites)


class CriticalFieldsConfig(BaseModel):
    """Defines which fields are considered critical (trigger gap-fill if missing)."""
    net_weight: bool = True
    packaged_weight: bool = True
    packaged_dims: bool = True
    warranty: bool = True
    short_description: bool = True
    # Add more as needed per company
    color: bool = False
    country_of_origin: bool = False


class LanguageConfig(BaseModel):
    """Language and localization settings."""
    primary_languages: list[str] = Field(default_factory=lambda: ["sl", "en"])
    # Languages to look for in product data. First is primary.

    output_language: str = "en"
    # Language for generated descriptions and features

    # Additional color name mappings (merged with platform defaults)
    extra_color_mappings: dict[str, str] = Field(default_factory=dict)
    # e.g., {"türkis": "turquoise"} for German-speaking markets

    # Additional country name mappings (merged with platform defaults)
    extra_country_mappings: dict[str, str] = Field(default_factory=dict)


class CostConfig(BaseModel):
    """Cost guardrails and limits."""
    max_daily_cost_usd: float = 50.0
    daily_product_limit: int = 200
    max_batch_size: int = 50
    market_region: str = ""
    # Appended to search queries for regional relevance (e.g., "Slovenia")


class LLMConfig(BaseModel):
    """LLM model selection per pipeline phase."""
    triage_model: str = "haiku"       # "haiku" or "sonnet"
    search_model: str = "haiku"
    extract_model: str = "haiku"
    gap_fill_model: str = "haiku"
    validate_model: str = "haiku"


class BrandConfig(BaseModel):
    """Brand-related settings."""
    known_brands: list[str] = Field(default_factory=lambda: [
        "Texas", "Makita", "Bosch", "DeWalt", "Valvoline", "Husqvarna",
        "Stihl", "Kärcher", "Metabo", "Milwaukee", "Ryobi", "Black+Decker",
        "Einhell", "Gardena", "Fiskars"
    ])
    # Used in triage system prompt to help Claude identify brands

    # Pre-seeded brand → country of origin mappings
    brand_coo_seeds: dict[str, str] = Field(default_factory=dict)
    # e.g., {"Makita": "Japan", "Bosch": "Germany"}


class ExportConfig(BaseModel):
    """Export format and field selection."""
    include_cost_data: bool = False
    include_enrichment_log: bool = False
    # Custom column names (maps internal name → export column header)
    column_overrides: dict[str, str] = Field(default_factory=dict)
    # e.g., {"short_description": "Kratek opis"} for Slovenian headers


class CompanyConfig(BaseModel):
    """Root config — one per instance. Loaded from config/company.json."""
    company_name: str = "Default"
    company_slug: str = "default"  # used for logging, file paths

    source: SourceConfig = SourceConfig()
    pipeline: PipelineConfig = PipelineConfig()
    critical_fields: CriticalFieldsConfig = CriticalFieldsConfig()
    language: LanguageConfig = LanguageConfig()
    cost: CostConfig = CostConfig()
    llm: LLMConfig = LLMConfig()
    brands: BrandConfig = BrandConfig()
    export: ExportConfig = ExportConfig()
```

### Step 1.2: Create `backend/config/loader.py`

```python
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
    """Get the loaded config singleton. Raises if not loaded yet."""
    if _config is None:
        raise RuntimeError("Config not loaded. Call load_config() on startup.")
    return _config
```

### Step 1.3: Create `backend/config/shoppster.json`

This mirrors EXACTLY what the current codebase does (all current hardcoded behavior):

```json
{
    "company_name": "Shoppster",
    "company_slug": "shoppster",
    "source": {
        "strategy": "official_first",
        "search_provider": "tavily",
        "max_manufacturer_results": 5,
        "max_general_results": 7,
        "max_general_queries": 3,
        "max_total_results": 6,
        "allowed_domains": [],
        "blocked_domains": [],
        "trust_third_party_as_primary": false
    },
    "pipeline": {
        "enable_ean_lookup": true,
        "enable_gap_fill": true,
        "enable_gemini_vision": true,
        "max_pages_to_scrape": 5,
        "max_gap_fill_pages": 5,
        "gap_fill_is_primary": false
    },
    "critical_fields": {
        "net_weight": true,
        "packaged_weight": true,
        "packaged_dims": true,
        "warranty": true,
        "short_description": true,
        "color": false,
        "country_of_origin": false
    },
    "language": {
        "primary_languages": ["sl", "en"],
        "output_language": "en",
        "extra_color_mappings": {},
        "extra_country_mappings": {}
    },
    "cost": {
        "max_daily_cost_usd": 50.0,
        "daily_product_limit": 200,
        "max_batch_size": 50,
        "market_region": ""
    },
    "llm": {
        "triage_model": "haiku",
        "search_model": "haiku",
        "extract_model": "haiku",
        "gap_fill_model": "haiku",
        "validate_model": "haiku"
    },
    "brands": {
        "known_brands": [
            "Texas", "Makita", "Bosch", "DeWalt", "Valvoline", "Husqvarna",
            "Stihl", "Kärcher", "Metabo", "Milwaukee", "Ryobi", "Black+Decker",
            "Einhell", "Gardena", "Fiskars"
        ],
        "brand_coo_seeds": {}
    },
    "export": {
        "include_cost_data": false,
        "include_enrichment_log": false,
        "column_overrides": {}
    }
}
```

### Step 1.4: Create `backend/config/merkur.json`

```json
{
    "company_name": "Merkur",
    "company_slug": "merkur",
    "source": {
        "strategy": "any_source",
        "search_provider": "tavily",
        "max_manufacturer_results": 5,
        "max_general_results": 10,
        "max_general_queries": 4,
        "max_total_results": 10,
        "allowed_domains": [],
        "blocked_domains": [],
        "trust_third_party_as_primary": true
    },
    "pipeline": {
        "enable_ean_lookup": true,
        "enable_gap_fill": true,
        "enable_gemini_vision": true,
        "max_pages_to_scrape": 7,
        "max_gap_fill_pages": 7,
        "gap_fill_is_primary": true
    },
    "critical_fields": {
        "net_weight": true,
        "packaged_weight": true,
        "packaged_dims": true,
        "warranty": true,
        "short_description": true,
        "color": true,
        "country_of_origin": true
    },
    "language": {
        "primary_languages": ["sl", "de", "en"],
        "output_language": "en",
        "extra_color_mappings": {
            "türkis": "turquoise",
            "beige": "beige",
            "rosa": "pink",
            "dunkelblau": "dark blue",
            "hellgrau": "light gray",
            "anthrazit": "anthracite"
        },
        "extra_country_mappings": {}
    },
    "cost": {
        "max_daily_cost_usd": 50.0,
        "daily_product_limit": 200,
        "max_batch_size": 50,
        "market_region": "Slovenia"
    },
    "llm": {
        "triage_model": "haiku",
        "search_model": "haiku",
        "extract_model": "haiku",
        "gap_fill_model": "haiku",
        "validate_model": "haiku"
    },
    "brands": {
        "known_brands": [
            "Bosch", "Makita", "DeWalt", "Metabo", "Kärcher", "Stihl",
            "Husqvarna", "Gardena", "Fiskars", "Weber", "Vileda",
            "Leifheit", "Tefal", "Philips", "Braun"
        ],
        "brand_coo_seeds": {}
    },
    "export": {
        "include_cost_data": false,
        "include_enrichment_log": false,
        "column_overrides": {}
    }
}
```

### Step 1.5: Create `backend/config/__init__.py`

```python
from config.loader import load_config, get_config
from config.schema import CompanyConfig
```

### Step 1.6: Modify `backend/main.py` — Load config on startup

**Change 1:** Add import at top of file (after existing imports around line 14):
```python
from config import load_config, get_config
```

**Change 2:** In the `startup_event()` function (line 61-64), add config loading BEFORE `init_db()`:
```python
@app.on_event("startup")
async def startup_event():
    load_config()  # Load company.json — must be first
    init_db()
    event_bus.set_loop(asyncio.get_running_loop())
```

### Step 1.7: Modify `backend/pipeline/triage.py` — Use config for known brands

**What to change:** The system prompt on line 40-60 has hardcoded brand names. Replace the hardcoded list with config values.

**Current code (line 59):**
```python
- Look for known brands in the product name (Texas, Makita, Bosch, DeWalt, Valvoline, etc.)
```

**New code:**
```python
# At the top of the function (after product_id = state["product_id"]):
from config import get_config
cfg = get_config()

# In the system prompt, replace the hardcoded brand line with:
brands_str = ", ".join(cfg.brands.known_brands)
# Then in the system prompt string:
f"- Look for known brands in the product name ({brands_str})"
```

The full replacement: In `triage_node()`, after line 38 (`product_id = state["product_id"]`), add:
```python
from config import get_config
cfg = get_config()
```

Then replace the system_prompt string (lines 40-60) with a version that uses `cfg.brands.known_brands`:
```python
    brands_str = ", ".join(cfg.brands.known_brands[:15])  # Show top 15 in prompt

    system_prompt = f"""You are a product classification expert for a data enrichment pipeline.

Given a product name (often in Slovenian), EAN code, and any existing data:
1. PARSE the product name to extract: brand, model number, color hints, size hints
2. CLASSIFY the product type into one of: standard_product, accessory, liquid, soft_good, electronics, other
3. Provide reasoning for your classification
4. MANUFACTURER DOMAIN: Identify the brand's official website domain (e.g., "makita.com", "bosch.com", "texas-garden.com").
   Return just the domain — no "http://", no "www." prefix, no paths.
   Set to null if the brand has no obvious website or you cannot determine it confidently.

PRODUCT TYPE RULES:
- standard_product: Physical products with standard dimensions (H/L/W). Tools, machines, appliances.
- accessory: Small parts/attachments defined by diameter, arbor size, etc. Wire brushes, drill bits, saw blades.
- liquid: Liquids, oils, chemicals. Defined by volume, not physical dimensions.
- soft_good: Textiles, clothing, bags.
- electronics: Pure electronic devices.
- other: If nothing else fits.

BRAND DETECTION:
- Look for known brands in the product name ({brands_str}, etc.)
- brand_confidence: "certain" if brand is explicitly stated, "likely" if inferred, "unknown" if can't determine"""
```

The LLM model selection should also use config. Change line 71-77:
```python
        classification, usage = classify_with_schema(
            prompt=user_prompt,
            system=system_prompt,
            schema=ProductClassification,
            model=cfg.llm.triage_model,  # was hardcoded "haiku"
            return_usage=True
        )
```

### Step 1.8: Modify `backend/pipeline/search.py` — Use config for source strategy

**What to change:** Replace hardcoded search behavior with config-driven behavior.

Add at the top of `search_node()` (after line 38, `product_id = state["product_id"]`):
```python
from config import get_config
cfg = get_config()
```

**Change 1 (line 63):** Replace `market_region = get_limits().get("market_region", "")` with:
```python
market_region = cfg.cost.market_region
```

**Change 2 (line 86):** Replace `search_provider = os.getenv("SEARCH_PROVIDER", "tavily").lower()` with:
```python
search_provider = cfg.source.search_provider
```

**Change 3 (line 98-100):** Make manufacturer search conditional on source strategy:
```python
# Replace: if manufacturer_domain:
# With:
if manufacturer_domain and cfg.source.strategy != "any_source":
```

When `strategy == "any_source"`, skip the manufacturer-targeted Phase 1 search entirely. The product still gets manufacturer_domain set by triage (for URL classification), but we don't restrict search to that domain.

**Change 4 (line 132):** Replace hardcoded max_general_queries:
```python
# Replace: max_general_queries = 2 if (manufacturer_domain and all_results) else 3
# With:
if cfg.source.strategy == "any_source":
    max_general_queries = cfg.source.max_general_queries
else:
    max_general_queries = 2 if (manufacturer_domain and all_results) else cfg.source.max_general_queries
```

**Change 5 (line 137):** Replace hardcoded max_results:
```python
# Replace: response = client.search(query=q, max_results=7)
# With:
response = client.search(query=q, max_results=cfg.source.max_general_results)
```

**Change 6 (line 154):** Replace hardcoded result cap:
```python
# Replace: if len(all_results) >= 6:
# With:
if len(all_results) >= cfg.source.max_total_results:
```

**Change 7:** LLM model for URL classification (line 323):
```python
# Replace: model="haiku",
# With:
model=cfg.llm.search_model,
```

### Step 1.9: Modify `backend/pipeline/extract.py` — Use config for source tiers and scraping limits

Add at the top of `extract_node()` function:
```python
from config import get_config
cfg = get_config()
```

**Key changes in extract.py:**

1. **Max pages to scrape**: Find where the code limits how many pages to scrape (the loop over classified URLs). Replace any hardcoded limit with `cfg.pipeline.max_pages_to_scrape`.

2. **Source tier behavior**: When `cfg.source.trust_third_party_as_primary` is True, treat third_party URLs the same as authorized_distributor (don't skip them, extract from them with the same prompts).

   Find the section where the code decides which URLs to extract from based on `source_type`. Currently it extracts from "manufacturer" and "authorized_distributor" but skips/caches "third_party" for gap-fill. When `trust_third_party_as_primary` is True, include "third_party" in the main extraction loop too.

3. **LLM model**: Replace all `model="haiku"` calls with `model=cfg.llm.extract_model`.

4. **Gemini vision**: Wrap the Gemini color detection call in `if cfg.pipeline.enable_gemini_vision:`.

### Step 1.10: Modify `backend/pipeline/gap_fill.py` — Use config for critical fields

Add at the top of `gap_fill_node()`:
```python
from config import get_config
cfg = get_config()
```

**Change 1:** Replace the hardcoded `CRITICAL_GAP_CHECKS` dict (lines 37-58) with a version that respects config:

```python
def _get_active_gap_checks(cfg_fields: 'CriticalFieldsConfig') -> dict:
    """Return only the gap checks that are enabled in config."""
    ALL_CHECKS = {
        "net_weight": lambda m: (
            m.dimensions.net.weight.value is None
            or m.dimensions.net.weight.confidence == 'not_found'
        ),
        "packaged_weight": lambda m: (
            m.dimensions.packaged.weight.value is None
            or m.dimensions.packaged.weight.confidence == 'not_found'
        ),
        "packaged_dims": lambda m: all([
            m.dimensions.packaged.height.value is None or m.dimensions.packaged.height.confidence == 'not_found',
            m.dimensions.packaged.length.value is None or m.dimensions.packaged.length.confidence == 'not_found',
            m.dimensions.packaged.width.value is None or m.dimensions.packaged.width.confidence == 'not_found',
        ]),
        "warranty": lambda m: (
            m.warranty.duration.value is None
            or m.warranty.duration.confidence == 'not_found'
        ),
        "short_description": lambda m: (
            m.descriptions.short_description.value is None
        ),
    }
    active = {}
    for name, check_fn in ALL_CHECKS.items():
        if getattr(cfg_fields, name, False):
            active[name] = check_fn
    return active
```

Then in `gap_fill_node()`, replace `gaps = _identify_gaps(model)` with:
```python
active_checks = _get_active_gap_checks(cfg.critical_fields)
gaps = [name for name, check_fn in active_checks.items() if check_fn(model)]
```

**Change 2:** Replace the hardcoded max pages loop. In the `for page in third_party_pages:` loop, add a counter and break after `cfg.pipeline.max_gap_fill_pages`.

**Change 3:** LLM model: Replace `model="haiku"` with `model=cfg.llm.gap_fill_model`.

### Step 1.11: Modify `backend/pipeline/validate.py` — Use config for color/country maps

Add at the top of `validate_node()`:
```python
from config import get_config
cfg = get_config()
```

**Change 1:** Merge extra color mappings from config into MULTILANG_COLORS. At the top of `validate_node()`:
```python
# Merge config-provided extra color mappings
merged_colors = {**MULTILANG_COLORS, **cfg.language.extra_color_mappings}
```

Then in `_normalize_color()`, use the merged dict. Since `_normalize_color` is a module-level function, the simplest approach is to make it accept the dict as a parameter:
```python
def _normalize_color(raw: str, color_map: dict[str, str] = None) -> str | None:
    lookup = color_map or MULTILANG_COLORS
    return lookup.get(raw.strip().lower())
```

And call it as `_normalize_color(raw, merged_colors)` from `_apply_corrections`.

**Change 2:** Same pattern for country mappings:
```python
merged_countries = {**COUNTRY_NAME_MAP, **cfg.language.extra_country_mappings}
```

**Change 3:** LLM model: Replace `model="haiku"` with `model=cfg.llm.validate_model`.

### Step 1.12: Modify `backend/utils/cost_tracker.py` — Use config for limits

**Change:** Replace the module-level `_limits` dict initialization (lines 54-59).

Currently:
```python
_limits = {
    "daily_product_limit": int(os.getenv("DAILY_PRODUCT_LIMIT", "200")),
    "max_batch_size": int(os.getenv("MAX_BATCH_SIZE", "50")),
    "max_daily_cost_usd": float(os.getenv("MAX_DAILY_COST_USD", "50.0")),
    "market_region": os.getenv("MARKET_REGION", ""),
}
```

Replace with:
```python
_limits = None  # Initialized from config on first access

def _init_limits():
    """Initialize limits from config. Falls back to env vars if config not loaded yet."""
    global _limits
    try:
        from config import get_config
        cfg = get_config()
        _limits = {
            "daily_product_limit": cfg.cost.daily_product_limit,
            "max_batch_size": cfg.cost.max_batch_size,
            "max_daily_cost_usd": cfg.cost.max_daily_cost_usd,
            "market_region": cfg.cost.market_region,
        }
    except RuntimeError:
        # Config not loaded yet — use env vars as fallback
        _limits = {
            "daily_product_limit": int(os.getenv("DAILY_PRODUCT_LIMIT", "200")),
            "max_batch_size": int(os.getenv("MAX_BATCH_SIZE", "50")),
            "max_daily_cost_usd": float(os.getenv("MAX_DAILY_COST_USD", "50.0")),
            "market_region": os.getenv("MARKET_REGION", ""),
        }
```

And update `get_limits()`:
```python
def get_limits() -> dict:
    if _limits is None:
        _init_limits()
    return dict(_limits)
```

### Step 1.13: Add `COMPANY_CONFIG_PATH` to `.env`

Add this line to `backend/.env`:
```
COMPANY_CONFIG_PATH=config/shoppster.json
```

### Step 1.14: Create `backend/config/__init__.py`

(Already specified in Step 1.5 above)

### Verification

After completing Phase 1:
- Run `python -c "from config import load_config; c = load_config('config/shoppster.json'); print(c.company_name)"` — should print "Shoppster"
- Run `python -c "from config import load_config; c = load_config('config/merkur.json'); print(c.source.strategy)"` — should print "any_source"
- Run the existing pipeline on a single product — behavior should be IDENTICAL to before (all defaults match current hardcoded values)
- Run the test: start the server with `COMPANY_CONFIG_PATH=config/shoppster.json` and verify the pipeline works exactly as before

---

## Phase 2: Database Migration (SQLite → PostgreSQL)

**Goal:** Replace SQLite with PostgreSQL using SQLAlchemy ORM + Alembic migrations. Keep SQLite as a fallback for local development.

**Files to CREATE:**
- `backend/database/models.py` — SQLAlchemy ORM models
- `backend/database/session.py` — Database session management
- `backend/database/__init__.py` — Exports
- `backend/alembic.ini` — Alembic config
- `backend/alembic/env.py` — Alembic environment
- `backend/alembic/versions/001_initial.py` — Initial migration

**Files to MODIFY:**
- `backend/db.py` — Replace SQLite functions with SQLAlchemy equivalents (keep same function signatures!)
- `backend/.env` — Add DATABASE_URL
- `backend/requirements.txt` or equivalent — Add sqlalchemy, alembic, psycopg2-binary

### Step 2.1: Add dependencies

Add to requirements (create `backend/requirements.txt` if it doesn't exist, or update existing):
```
sqlalchemy>=2.0
alembic>=1.13
psycopg2-binary>=2.9
```

### Step 2.2: Create `backend/database/models.py`

```python
"""
SQLAlchemy ORM models — mirrors the existing SQLite schema exactly.
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, Float, DateTime,
    UniqueConstraint, Index, create_engine
)
from sqlalchemy.orm import DeclarativeBase, Session


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ean = Column(String, nullable=False)
    product_name = Column(String, nullable=False)
    brand = Column(String, nullable=True)
    weight = Column(String, nullable=True)
    original_data = Column(Text, nullable=True)
    status = Column(String, default="pending")
    product_type = Column(String, nullable=True)
    current_step = Column(String, nullable=True)
    classification_result = Column(Text, nullable=True)
    search_result = Column(Text, nullable=True)
    extraction_result = Column(Text, nullable=True)
    validation_result = Column(Text, nullable=True)
    enrichment_log = Column(Text, nullable=True)
    cost_data = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BrandCooCache(Base):
    __tablename__ = "brand_coo_cache"

    brand = Column(String, primary_key=True)
    country_of_origin = Column(String, nullable=False)
    confidence = Column(String, nullable=False)
    source_url = Column(String, nullable=True)
    cached_at = Column(DateTime, default=datetime.utcnow)


class ScrapedPage(Base):
    __tablename__ = "scraped_pages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, nullable=False)
    url = Column(String, nullable=False)
    source_type = Column(String, nullable=False)
    markdown = Column(Text, nullable=True)
    markdown_length = Column(Integer, default=0)
    scrape_success = Column(Boolean, default=True)
    extracted = Column(Boolean, default=False)
    gap_filled = Column(Boolean, default=False)
    scraped_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("product_id", "url", name="uq_scraped_product_url"),
        Index("idx_scraped_pages_product", "product_id", "source_type"),
    )
```

### Step 2.3: Create `backend/database/session.py`

```python
"""
Database session management.
Supports both PostgreSQL (production) and SQLite (local dev fallback).
"""

import os
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from database.models import Base

logger = logging.getLogger("database")

_engine = None
_SessionLocal = None


def get_database_url() -> str:
    """
    Determine database URL.
    Priority: DATABASE_URL env var > default SQLite file.
    """
    url = os.getenv("DATABASE_URL")
    if url:
        # Handle Heroku/Railway-style postgres:// → postgresql://
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url
    # Fallback: SQLite for local development
    return "sqlite:///products.db"


def init_engine():
    """Create the database engine and tables."""
    global _engine, _SessionLocal

    url = get_database_url()
    is_sqlite = url.startswith("sqlite")

    connect_args = {}
    if is_sqlite:
        connect_args["check_same_thread"] = False

    _engine = create_engine(
        url,
        connect_args=connect_args,
        pool_pre_ping=True,
        echo=False,
    )

    # Create all tables (idempotent)
    Base.metadata.create_all(_engine)

    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)

    db_type = "SQLite" if is_sqlite else "PostgreSQL"
    logger.info(f"Database initialized: {db_type}")


def get_session() -> Session:
    """Get a new database session."""
    if _SessionLocal is None:
        init_engine()
    return _SessionLocal()
```

### Step 2.4: Create `backend/database/__init__.py`

```python
from database.session import init_engine, get_session, get_database_url
from database.models import Base, Product, BrandCooCache, ScrapedPage
```

### Step 2.5: Rewrite `backend/db.py` — Keep same function signatures, use SQLAlchemy internally

This is the critical file. Every pipeline module imports functions from `db.py`. We keep the EXACT same function signatures so no other files need to change (except the imports already added in Phase 1).

**IMPORTANT:** The existing `db.py` functions use `sqlite3.Row` (dict-like access). The pipeline code accesses results as `product['ean']`, `product['product_name']`, etc. We must maintain this interface.

Replace the ENTIRE contents of `backend/db.py` with:

```python
"""
Database access layer — wraps SQLAlchemy.

All functions maintain the same signatures as the original SQLite version.
Pipeline modules import from here and should not need any changes.
"""

import json
import logging
from datetime import datetime
from database import init_engine, get_session, Product, BrandCooCache, ScrapedPage
from events import event_bus

logger = logging.getLogger("database")


def init_db():
    """Initialize database engine and create tables."""
    init_engine()


def get_db_connection():
    """
    Returns a CompatConnection that mimics sqlite3.Connection interface.
    This is a compatibility wrapper so existing code using conn.execute()
    and dict(row) continues to work unchanged.
    """
    return CompatConnection()


class CompatRow:
    """Mimics sqlite3.Row — allows both dict(row) and row['column'] access."""

    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def __contains__(self, key):
        return key in self._data

    def keys(self):
        return self._data.keys()

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)


class CompatConnection:
    """
    Mimics sqlite3.Connection interface using SQLAlchemy session underneath.
    Supports conn.execute(sql, params), conn.commit(), conn.close().
    Results can be used as dict(row) or row['column'].
    """

    def __init__(self):
        self.session = get_session()
        self.row_factory = None  # compat — ignored, we always return dict-like rows

    def execute(self, sql: str, params=None):
        """Execute raw SQL and return a CompatResult."""
        from sqlalchemy import text
        if params is None:
            params = ()

        # Convert ? placeholders to :param style for SQLAlchemy
        # Count ? occurrences and replace with :p0, :p1, etc.
        if "?" in sql:
            param_dict = {}
            counter = 0
            new_sql = ""
            for char in sql:
                if char == "?":
                    key = f"p{counter}"
                    new_sql += f":{key}"
                    param_dict[key] = params[counter]
                    counter += 1
                else:
                    new_sql += char
            result = self.session.execute(text(new_sql), param_dict)
        else:
            result = self.session.execute(text(sql))

        return CompatResult(result, self.session)

    def commit(self):
        self.session.commit()

    def close(self):
        self.session.close()

    def cursor(self):
        """Return self — we handle execute directly."""
        return self


class CompatResult:
    """Wraps SQLAlchemy result to provide fetchone()/fetchall() with dict-like rows."""

    def __init__(self, result, session):
        self._result = result
        self._session = session

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
        return self._result.lastrowid


def _publish_event(product_id: int, event: dict):
    """Publish an SSE event."""
    event_bus.publish_product_event(product_id, event)


def update_step(product_id: int, status: str, step: str):
    """Update the current processing step for a product."""
    session = get_session()
    session.execute(
        __import__('sqlalchemy').text(
            "UPDATE products SET status = :status, current_step = :step, updated_at = :now WHERE id = :id"
        ),
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
    from sqlalchemy import text
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
    from sqlalchemy import text
    session.execute(
        text("UPDATE products SET cost_data = :data, updated_at = :now WHERE id = :id"),
        {"data": json.dumps(cost_summary), "now": datetime.utcnow(), "id": product_id}
    )
    session.commit()
    session.close()


def save_scraped_page(product_id: int, url: str, source_type: str, markdown: str | None, success: bool = True):
    """Cache a scraped page's markdown."""
    session = get_session()
    from sqlalchemy import text
    # Use upsert pattern
    existing = session.execute(
        text("SELECT id FROM scraped_pages WHERE product_id = :pid AND url = :url"),
        {"pid": product_id, "url": url}
    ).fetchone()

    ml = len(markdown) if markdown else 0
    now = datetime.utcnow()

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
    from sqlalchemy import text
    query = "SELECT * FROM scraped_pages WHERE product_id = :pid AND scrape_success = true"
    params = {"pid": product_id}
    if source_type:
        query += " AND source_type = :st"
        params["st"] = source_type
    if only_unextracted:
        query += " AND extracted = false AND gap_filled = false"
    query += " ORDER BY id ASC"
    rows = session.execute(text(query), params).fetchall()
    session.close()
    return [dict(r._mapping) for r in rows]


def mark_page_extracted(product_id: int, url: str):
    session = get_session()
    from sqlalchemy import text
    session.execute(
        text("UPDATE scraped_pages SET extracted = true WHERE product_id = :pid AND url = :url"),
        {"pid": product_id, "url": url}
    )
    session.commit()
    session.close()


def mark_page_gap_filled(product_id: int, url: str):
    session = get_session()
    from sqlalchemy import text
    session.execute(
        text("UPDATE scraped_pages SET gap_filled = true WHERE product_id = :pid AND url = :url"),
        {"pid": product_id, "url": url}
    )
    session.commit()
    session.close()


def delete_scraped_pages(product_id: int):
    session = get_session()
    from sqlalchemy import text
    session.execute(
        text("DELETE FROM scraped_pages WHERE product_id = :pid"),
        {"pid": product_id}
    )
    session.commit()
    session.close()
```

**IMPORTANT NOTE:** The `CompatConnection` / `CompatRow` / `CompatResult` classes exist purely so that ALL existing pipeline code (`main.py`, `triage.py`, `search.py`, `extract.py`, `gap_fill.py`, `validate.py`, `cost_tracker.py`) continues to work without changes. The pattern `conn = get_db_connection(); product = conn.execute("SELECT ...").fetchone(); value = product['column']; conn.close()` must keep working.

### Step 2.6: Update `backend/.env`

Add:
```
# Database — uncomment ONE:
# PostgreSQL (production):
# DATABASE_URL=postgresql://user:password@localhost:5432/enrichment
# SQLite (local dev, default if not set):
# DATABASE_URL=sqlite:///products.db
```

### Step 2.7: Migrate existing data (optional helper script)

Create `backend/scripts/migrate_sqlite_to_postgres.py`:
```python
"""
One-time migration script: copies all data from SQLite to PostgreSQL.
Run: DATABASE_URL=postgresql://... python scripts/migrate_sqlite_to_postgres.py
"""

import sqlite3
import os
from sqlalchemy import create_engine, text

SQLITE_PATH = "products.db"
PG_URL = os.getenv("DATABASE_URL")

if not PG_URL:
    print("Set DATABASE_URL env var to your PostgreSQL connection string")
    exit(1)

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
```

### Verification

- Set `DATABASE_URL` to a test PostgreSQL database
- Run the server: `python main.py`
- Upload a CSV, process one product — full pipeline should work identically
- Unset `DATABASE_URL` — should fall back to SQLite and still work

---

## Phase 3: Authentication System

**Goal:** Add user accounts with email/password login, JWT tokens, and role-based access. Simple: just `admin` and `viewer` roles. Max 30 users across all instances.

**Files to CREATE:**
- `backend/auth/models.py` — User SQLAlchemy model
- `backend/auth/security.py` — Password hashing, JWT creation/verification
- `backend/auth/dependencies.py` — FastAPI dependencies (get_current_user)
- `backend/auth/router.py` — Login/register/me endpoints
- `backend/auth/__init__.py`

**Files to MODIFY:**
- `backend/database/models.py` — Add User model
- `backend/main.py` — Include auth router, protect existing endpoints
- `backend/.env` — Add JWT_SECRET

### Step 3.1: Add dependencies

Add to requirements:
```
python-jose[cryptography]>=3.3
passlib[bcrypt]>=1.7
```

### Step 3.2: Add User model to `backend/database/models.py`

Add this class after the existing models:

```python
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=True)
    role = Column(String, default="viewer")  # "admin" or "viewer"
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)
```

### Step 3.3: Create `backend/auth/security.py`

```python
"""
Password hashing and JWT token management.
"""

import os
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext

# Config
SECRET_KEY = os.getenv("JWT_SECRET", "CHANGE-ME-IN-PRODUCTION-use-openssl-rand-hex-32")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """Decode JWT token. Returns payload dict or None if invalid/expired."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None
```

### Step 3.4: Create `backend/auth/dependencies.py`

```python
"""
FastAPI dependencies for authentication.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import text
from auth.security import decode_access_token
from database.session import get_session

security = HTTPBearer()


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """
    Decode JWT token and return user dict.
    Raises 401 if token is invalid or user not found.
    """
    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    session = get_session()
    row = session.execute(
        text("SELECT id, email, full_name, role, is_active FROM users WHERE id = :id"),
        {"id": int(user_id)}
    ).fetchone()
    session.close()

    if row is None:
        raise HTTPException(status_code=401, detail="User not found")

    user = dict(row._mapping)
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="Account disabled")

    return user


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Require the current user to have admin role."""
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
```

### Step 3.5: Create `backend/auth/router.py`

```python
"""
Auth API endpoints: login, register (admin-only), me, change password.
"""

from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from auth.security import hash_password, verify_password, create_access_token
from auth.dependencies import get_current_user, require_admin
from database.session import get_session

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str
    full_name: str = ""
    role: str = "viewer"  # "admin" or "viewer"


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest):
    session = get_session()
    row = session.execute(
        text("SELECT id, email, full_name, role, is_active, hashed_password FROM users WHERE email = :email"),
        {"email": req.email.lower().strip()}
    ).fetchone()
    session.close()

    if row is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    user = dict(row._mapping)

    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="Account disabled")

    if not verify_password(req.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Update last_login
    session = get_session()
    session.execute(
        text("UPDATE users SET last_login = :now WHERE id = :id"),
        {"now": datetime.utcnow(), "id": user["id"]}
    )
    session.commit()
    session.close()

    token = create_access_token(data={"sub": str(user["id"]), "role": user["role"]})

    return TokenResponse(
        access_token=token,
        user={
            "id": user["id"],
            "email": user["email"],
            "full_name": user["full_name"],
            "role": user["role"],
        }
    )


@router.post("/register", status_code=201)
def register_user(req: RegisterRequest, admin: dict = Depends(require_admin)):
    """Create a new user. Admin-only."""
    if req.role not in ("admin", "viewer"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'viewer'")

    session = get_session()

    # Check if email already exists
    existing = session.execute(
        text("SELECT id FROM users WHERE email = :email"),
        {"email": req.email.lower().strip()}
    ).fetchone()
    if existing:
        session.close()
        raise HTTPException(status_code=409, detail="Email already registered")

    hashed = hash_password(req.password)
    session.execute(
        text("""INSERT INTO users (email, hashed_password, full_name, role, is_active, created_at)
                VALUES (:email, :hashed, :name, :role, true, :now)"""),
        {
            "email": req.email.lower().strip(),
            "hashed": hashed,
            "name": req.full_name,
            "role": req.role,
            "now": datetime.utcnow(),
        }
    )
    session.commit()
    session.close()

    return {"message": f"User {req.email} created with role {req.role}"}


@router.get("/me")
def get_me(user: dict = Depends(get_current_user)):
    return user


@router.post("/change-password")
def change_password(req: ChangePasswordRequest, user: dict = Depends(get_current_user)):
    session = get_session()
    row = session.execute(
        text("SELECT hashed_password FROM users WHERE id = :id"),
        {"id": user["id"]}
    ).fetchone()

    if not verify_password(req.current_password, row._mapping["hashed_password"]):
        session.close()
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    new_hash = hash_password(req.new_password)
    session.execute(
        text("UPDATE users SET hashed_password = :hash WHERE id = :id"),
        {"hash": new_hash, "id": user["id"]}
    )
    session.commit()
    session.close()

    return {"message": "Password changed successfully"}
```

### Step 3.6: Create `backend/auth/__init__.py`

```python
from auth.router import router as auth_router
from auth.dependencies import get_current_user, require_admin
```

### Step 3.7: Create seed script `backend/scripts/create_admin.py`

```python
"""
Create the initial admin user for a new instance.
Run: python scripts/create_admin.py admin@example.com mypassword "Admin Name"
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.session import init_engine, get_session
from auth.security import hash_password
from sqlalchemy import text
from datetime import datetime


def create_admin(email: str, password: str, name: str = "Admin"):
    init_engine()
    session = get_session()

    # Check if user exists
    existing = session.execute(
        text("SELECT id FROM users WHERE email = :email"),
        {"email": email}
    ).fetchone()

    if existing:
        print(f"User {email} already exists")
        session.close()
        return

    hashed = hash_password(password)
    session.execute(
        text("""INSERT INTO users (email, hashed_password, full_name, role, is_active, created_at)
                VALUES (:email, :hash, :name, 'admin', true, :now)"""),
        {"email": email, "hash": hashed, "name": name, "now": datetime.utcnow()}
    )
    session.commit()
    session.close()
    print(f"Admin user created: {email}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python scripts/create_admin.py <email> <password> [full_name]")
        sys.exit(1)

    email = sys.argv[1]
    password = sys.argv[2]
    name = sys.argv[3] if len(sys.argv) > 3 else "Admin"
    create_admin(email, password, name)
```

### Step 3.8: Modify `backend/main.py` — Include auth router and protect endpoints

**Change 1:** Add imports at the top:
```python
from auth import auth_router, get_current_user, require_admin
```

**Change 2:** Include the auth router after creating the `app` (after line 37):
```python
app.include_router(auth_router)
```

**Change 3:** Add authentication to ALL existing endpoints. Add `user: dict = Depends(get_current_user)` as a parameter to every route function.

For write operations (upload, process, enrich, reset, update limits), use `Depends(require_admin)` instead.

Example — change the products list endpoint (line 156-161):
```python
# Before:
@app.get("/api/products", response_model=List[ProductResponse])
def get_products():

# After:
@app.get("/api/products", response_model=List[ProductResponse])
def get_products(user: dict = Depends(get_current_user)):
```

Example — change the upload endpoint (line 94-131):
```python
# Before:
@app.post("/api/upload")
async def upload_products(file: UploadFile = File(...)):

# After:
@app.post("/api/upload")
async def upload_products(file: UploadFile = File(...), user: dict = Depends(require_admin)):
```

Apply this pattern to ALL endpoints:
- **Read-only endpoints** (GET): `user: dict = Depends(get_current_user)`
  - `get_products`, `get_product`, `sse_products`, `sse_product`, `get_dashboard_stats`, `get_cost_stats`, `get_guardrail_limits`, `export_products`, `export_single_product`
- **Write endpoints** (POST/PUT/DELETE): `user: dict = Depends(require_admin)`
  - `upload_products`, `add_product_manually`, `process_all_products`, `process_batch_products`, `enrich_product`, `trigger_classify`, `trigger_search`, `trigger_extract`, `trigger_validate`, `reset_product`, `update_guardrail_limits`

**Change 4:** Update CORS to allow Authorization header (it's already included via `allow_headers=["*"]`, so no change needed).

### Step 3.9: Update `backend/.env`

Add:
```
# Auth
JWT_SECRET=generate-a-random-32-char-hex-string-here
```

### Verification

- Start the server
- Try `GET /api/products` without a token — should get 401
- Run `python scripts/create_admin.py admin@test.com password123 "Test Admin"`
- `POST /api/auth/login` with `{"email": "admin@test.com", "password": "password123"}` — should get a JWT token
- Use the token in `Authorization: Bearer <token>` header — all endpoints should work
- Create a viewer user, verify they can read but not write

---

## Phase 4: Settings API (Dashboard Configuration)

**Goal:** Add API endpoints that allow the admin to view and modify company config settings from the dashboard at runtime. Changes are saved to the database (not the JSON file) and override the file-based defaults.

**Files to CREATE:**
- `backend/settings/router.py` — Settings API endpoints
- `backend/settings/__init__.py`

**Files to MODIFY:**
- `backend/config/loader.py` — Add ability to overlay DB-stored settings on file-based config
- `backend/database/models.py` — Add Settings table
- `backend/main.py` — Include settings router

### Step 4.1: Add Settings model to `backend/database/models.py`

```python
class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String, unique=True, nullable=False, index=True)
    # key format: "source.strategy", "cost.max_daily_cost_usd", etc.
    value = Column(Text, nullable=False)  # JSON-encoded value
    updated_by = Column(String, nullable=True)  # email of who changed it
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```

### Step 4.2: Create `backend/settings/router.py`

```python
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
```

### Step 4.3: Update `backend/config/loader.py` — Integrate DB overrides

Add a function to get the effective config (file + DB overrides):

```python
def get_effective_config() -> CompanyConfig:
    """Get config with DB overrides applied. Use this in pipeline code."""
    base = get_config()  # File-based
    try:
        from settings.router import _get_all_db_overrides, _apply_overrides_to_config
        overrides = _get_all_db_overrides()
        if overrides:
            return _apply_overrides_to_config(base, overrides)
    except Exception:
        pass  # DB not available yet, use file-based config
    return base
```

Then update `get_config()` to call `get_effective_config()`, OR update the pipeline imports to use `get_effective_config()`. The simplest approach: have `get_config()` automatically apply DB overrides:

```python
def get_config() -> CompanyConfig:
    """Get the loaded config with DB overrides applied."""
    if _config is None:
        raise RuntimeError("Config not loaded. Call load_config() on startup.")
    # Apply DB overrides if available
    try:
        from settings.router import _get_all_db_overrides, _apply_overrides_to_config
        overrides = _get_all_db_overrides()
        if overrides:
            return _apply_overrides_to_config(_config, overrides)
    except Exception:
        pass
    return _config
```

### Step 4.4: Modify `backend/main.py` — Include settings router

Add import:
```python
from settings.router import router as settings_router
```

Include router (after auth router):
```python
app.include_router(settings_router)
```

### Step 4.5: Create `backend/settings/__init__.py`

```python
from settings.router import router as settings_router
```

### Verification

- `GET /api/settings/` — returns full config with no overrides
- `PUT /api/settings/` with `{"key": "cost.max_daily_cost_usd", "value": "100.0"}` — updates the setting
- `GET /api/settings/` — now shows `cost.max_daily_cost_usd = 100.0` and `overrides: ["cost.max_daily_cost_usd"]`
- `DELETE /api/settings/cost.max_daily_cost_usd` — reverts to file default
- Pipeline behavior should change when settings are updated

---

## Phase 5: Docker Packaging

**Goal:** Create Dockerfile and docker-compose.yml that packages the entire application (FastAPI + Postgres) into a deployable stack.

**Files to CREATE:**
- `backend/Dockerfile`
- `docker-compose.yml` (template for customer instances)
- `.dockerignore`
- `scripts/new-customer.sh` — Script to set up a new customer instance
- `scripts/update-all.sh` — Script to update all customer instances

### Step 5.1: Create `backend/Dockerfile`

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create config directory
RUN mkdir -p config

EXPOSE 8000

# Run with uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Step 5.2: Create `backend/.dockerignore`

```
__pycache__
*.pyc
*.pyo
.env
products.db
products.db-shm
products.db-wal
*.db
node_modules
.git
.vscode
*.csv
service-account.json
```

### Step 5.3: Create `docker-compose.yml` (template)

This file lives in each customer's directory on the VPS (e.g., `/opt/enrichment/shoppster/docker-compose.yml`):

```yaml
version: "3.8"

services:
  api:
    image: ghcr.io/YOUR_GITHUB_USER/enrichment-engine:latest
    # Or if not using a registry: build: ./backend
    ports:
      - "${API_PORT:-8000}:8000"
    env_file:
      - .env
    volumes:
      - ./config:/app/config:ro
      - ./service-account.json:/app/service-account.json:ro
    depends_on:
      db:
        condition: service_healthy
    restart: unless-stopped

  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ${DB_USER:-enrichment}
      POSTGRES_PASSWORD: ${DB_PASSWORD:-changeme}
      POSTGRES_DB: ${DB_NAME:-enrichment}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DB_USER:-enrichment}"]
      interval: 5s
      timeout: 5s
      retries: 5
    restart: unless-stopped

volumes:
  pgdata:
```

### Step 5.4: Create `.env` template for customer instances

Create `backend/env.template`:
```
# === Customer Instance Configuration ===
# Copy this to .env and fill in the values

# Company config file (relative to /app inside container)
COMPANY_CONFIG_PATH=config/company.json

# Database (connects to the db service in docker-compose)
DATABASE_URL=postgresql://enrichment:changeme@db:5432/enrichment

# Google Cloud Vertex AI (for Claude access)
VERTEX_PROJECT_ID=
VERTEX_LOCATION=europe-west1
GOOGLE_APPLICATION_CREDENTIALS=service-account.json

# API Keys
TAVILY_API_KEY=
FIRECRAWL_API_KEY=

# Auth
JWT_SECRET=generate-with-openssl-rand-hex-32

# Port (each customer instance on a different port)
API_PORT=8000
```

### Step 5.5: Create `scripts/new-customer.sh`

```bash
#!/bin/bash
# Usage: ./new-customer.sh <customer_slug> <port>
# Example: ./new-customer.sh merkur 8001

set -e

CUSTOMER=$1
PORT=$2
BASE_DIR="/opt/enrichment"

if [ -z "$CUSTOMER" ] || [ -z "$PORT" ]; then
    echo "Usage: ./new-customer.sh <customer_slug> <port>"
    echo "Example: ./new-customer.sh merkur 8001"
    exit 1
fi

CUSTOMER_DIR="$BASE_DIR/$CUSTOMER"

if [ -d "$CUSTOMER_DIR" ]; then
    echo "Error: $CUSTOMER_DIR already exists"
    exit 1
fi

echo "Creating customer instance: $CUSTOMER (port $PORT)"

# Create directory structure
mkdir -p "$CUSTOMER_DIR/config"

# Copy docker-compose template
cp "$BASE_DIR/docker-compose.template.yml" "$CUSTOMER_DIR/docker-compose.yml"

# Create .env from template
cp "$BASE_DIR/env.template" "$CUSTOMER_DIR/.env"

# Set the port in .env
sed -i "s/API_PORT=8000/API_PORT=$PORT/" "$CUSTOMER_DIR/.env"

# Generate JWT secret
JWT_SECRET=$(openssl rand -hex 32)
sed -i "s/JWT_SECRET=generate-with-openssl-rand-hex-32/JWT_SECRET=$JWT_SECRET/" "$CUSTOMER_DIR/.env"

# Generate DB password
DB_PASS=$(openssl rand -hex 16)
sed -i "s/DB_PASSWORD:-changeme/DB_PASSWORD:-$DB_PASS/" "$CUSTOMER_DIR/docker-compose.yml"
sed -i "s/changeme@db/$DB_PASS@db/" "$CUSTOMER_DIR/.env"

echo ""
echo "Customer instance created at: $CUSTOMER_DIR"
echo "Next steps:"
echo "  1. Copy config/$CUSTOMER.json to $CUSTOMER_DIR/config/company.json"
echo "  2. Copy service-account.json to $CUSTOMER_DIR/"
echo "  3. Edit $CUSTOMER_DIR/.env with API keys"
echo "  4. cd $CUSTOMER_DIR && docker compose up -d"
echo "  5. docker compose exec api python scripts/create_admin.py admin@$CUSTOMER.com <password>"
```

### Step 5.6: Create `scripts/update-all.sh`

```bash
#!/bin/bash
# Updates all customer instances to the latest Docker image.
# Run from VPS: ./update-all.sh

BASE_DIR="/opt/enrichment"

echo "Pulling latest image..."
docker pull ghcr.io/YOUR_GITHUB_USER/enrichment-engine:latest

echo ""
for CUSTOMER_DIR in "$BASE_DIR"/*/; do
    if [ -f "$CUSTOMER_DIR/docker-compose.yml" ]; then
        CUSTOMER=$(basename "$CUSTOMER_DIR")
        echo "Updating: $CUSTOMER"
        cd "$CUSTOMER_DIR"
        docker compose up -d --pull always
        echo "  Done."
    fi
done

echo ""
echo "All instances updated."
```

### Step 5.7: GitHub Actions CI/CD (optional but recommended)

Create `.github/workflows/docker.yml`:
```yaml
name: Build and Push Docker Image

on:
  push:
    branches: [master]
    paths:
      - 'backend/**'

jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write

    steps:
      - uses: actions/checkout@v4

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: ./backend
          push: true
          tags: |
            ghcr.io/${{ github.repository_owner }}/enrichment-engine:latest
            ghcr.io/${{ github.repository_owner }}/enrichment-engine:${{ github.sha }}
```

### Verification

- Build image locally: `cd backend && docker build -t enrichment-engine .`
- Run: `docker compose up -d`
- Create admin: `docker compose exec api python scripts/create_admin.py admin@test.com password123`
- Login via API, process a product — full pipeline should work
- Stop: `docker compose down`

---

## Phase 6: Shoppster Migration (First Customer)

**Goal:** Set up Shoppster as the first production customer instance.

### Step 6.1: On VPS, run the new-customer script

```bash
./new-customer.sh shoppster 8000
```

### Step 6.2: Copy configs

```bash
# Copy Shoppster config
cp config/shoppster.json /opt/enrichment/shoppster/config/company.json

# Copy service account
cp service-account.json /opt/enrichment/shoppster/

# Edit .env with real API keys
nano /opt/enrichment/shoppster/.env
```

### Step 6.3: Start the instance

```bash
cd /opt/enrichment/shoppster
docker compose up -d
```

### Step 6.4: Create admin user

```bash
docker compose exec api python scripts/create_admin.py admin@shoppster.com <password>
```

### Step 6.5: Migrate existing data

```bash
# Copy the existing SQLite DB into the container temporarily
docker cp products.db shoppster-api-1:/app/products.db

# Run migration script
docker compose exec api python scripts/migrate_sqlite_to_postgres.py
```

### Step 6.6: Verify

- Login at `http://vps-ip:8000/api/auth/login`
- Check `GET /api/products` returns all migrated products
- Process a new product — full pipeline should work
- Check `GET /api/settings/` returns Shoppster config

---

## Summary of All New Files

```
backend/
├── config/
│   ├── __init__.py
│   ├── schema.py          (Phase 1)
│   ├── loader.py           (Phase 1)
│   ├── shoppster.json      (Phase 1)
│   └── merkur.json         (Phase 1)
├── database/
│   ├── __init__.py         (Phase 2)
│   ├── models.py           (Phase 2, updated in Phase 3 & 4)
│   └── session.py          (Phase 2)
├── auth/
│   ├── __init__.py         (Phase 3)
│   ├── security.py         (Phase 3)
│   ├── dependencies.py     (Phase 3)
│   └── router.py           (Phase 3)
├── settings/
│   ├── __init__.py         (Phase 4)
│   └── router.py           (Phase 4)
├── scripts/
│   ├── create_admin.py     (Phase 3)
│   └── migrate_sqlite_to_postgres.py  (Phase 2)
├── Dockerfile              (Phase 5)
├── .dockerignore           (Phase 5)
├── env.template            (Phase 5)
└── requirements.txt        (updated in Phase 2 & 3)

(project root)
├── docker-compose.yml      (Phase 5, template)
├── scripts/
│   ├── new-customer.sh     (Phase 5)
│   └── update-all.sh       (Phase 5)
└── .github/
    └── workflows/
        └── docker.yml      (Phase 5)
```

## Files Modified (All Phases)

```
backend/db.py                    (Phase 2 — full rewrite)
backend/main.py                  (Phase 1, 3, 4 — config loading, auth, settings router)
backend/.env                     (Phase 1, 2, 3 — new env vars)
backend/pipeline/triage.py       (Phase 1 — config-driven brands, model)
backend/pipeline/search.py       (Phase 1 — config-driven source strategy)
backend/pipeline/extract.py      (Phase 1 — config-driven scraping behavior)
backend/pipeline/gap_fill.py     (Phase 1 — config-driven critical fields)
backend/pipeline/validate.py     (Phase 1 — config-driven color/country maps)
backend/utils/cost_tracker.py    (Phase 1 — config-driven limits)
```

---

## Per-Phase Execution Prompts for Sonnet

Below are copy-paste prompts to give Sonnet for each phase. Each prompt is self-contained — it tells Sonnet what to do, how to verify, and when to stop and ask the human for help.

---

### Prompt for Phase 1: Company Config System

```
You are implementing Phase 1 of a SaaS migration for a product enrichment engine.

READ the full plan first: SAAS_MIGRATION_PLAN.md — specifically the "Phase 1: Company Config System" section. Read it completely before writing any code.

CONTEXT: This is a FastAPI + Python backend that enriches product data using AI (Claude via Vertex AI), web search (Tavily), and web scraping (Firecrawl). Currently all company-specific values are hardcoded. You are extracting them into a JSON config system.

YOUR TASK:
1. Read ALL files listed in Phase 1 "Files to MODIFY" section to understand current hardcoded values
2. Create the config schema, loader, and JSON files exactly as specified in the plan
3. Modify each pipeline file to read from config instead of hardcoded values
4. Ensure ZERO behavior change when using the shoppster.json config (all defaults must match current behavior exactly)

RULES:
- Follow the plan exactly. Do not add features, abstractions, or improvements not in the plan.
- Read each file BEFORE modifying it. Understand the existing code.
- The config system must be a clean Pydantic model. No metaprogramming, no dynamic attribute access tricks.
- When modifying pipeline files, import `from config import get_config` and call `cfg = get_config()` at the start of each node function. Do NOT make config a global — load it fresh each time (it may change via settings API later).
- Keep the `from config import get_config` import INSIDE the function body (not at module level) to avoid circular imports during startup.
- Do NOT touch db.py, schemas.py, events.py, or graph.py in this phase.

VERIFICATION (do all of these):
1. Run: cd backend && python -c "from config.schema import CompanyConfig; c = CompanyConfig(); print(f'Default: {c.company_name}')"
   Expected: "Default: Default"
2. Run: python -c "from config.loader import load_config; c = load_config('config/shoppster.json'); print(f'{c.company_name}: strategy={c.source.strategy}')"
   Expected: "Shoppster: strategy=official_first"
3. Run: python -c "from config.loader import load_config; c = load_config('config/merkur.json'); print(f'{c.company_name}: strategy={c.source.strategy}, trust_3p={c.source.trust_third_party_as_primary}')"
   Expected: "Merkur: strategy=any_source, trust_3p=True"
4. Start the server with COMPANY_CONFIG_PATH=config/shoppster.json and verify it starts without errors
5. Verify that all modified pipeline files have no syntax errors by importing them:
   python -c "from pipeline.triage import triage_node; from pipeline.search import search_node; from pipeline.validate import validate_node; from pipeline.gap_fill import gap_fill_node; print('All imports OK')"

IF YOU NEED HELP:
- If you're unsure about what a hardcoded value currently does, read the surrounding code and comments — they explain the behavior.
- If you find a hardcoded value in extract.py that's not covered by the plan's config schema, add it to the schema following the same patterns (but tell me what you added and why).
- If any verification step fails, debug it. Do not skip verification.
- If you need to install a package, tell me and stop.

DO NOT proceed to Phase 2. Stop after Phase 1 is verified.
```

---

### Prompt for Phase 2: Database Migration (SQLite → PostgreSQL)

```
You are implementing Phase 2 of a SaaS migration for a product enrichment engine.

READ the full plan: SAAS_MIGRATION_PLAN.md — specifically "Phase 2: Database Migration". Read it completely before writing any code. Also read Phase 1 to understand what was already done (config system is in place).

CONTEXT: The backend currently uses raw SQLite via the sqlite3 module. Every pipeline file imports functions from db.py (get_db_connection, update_step, append_log, save_scraped_page, etc.). You are replacing the internals with SQLAlchemy while keeping the EXACT same function signatures so no pipeline code needs to change.

YOUR TASK:
1. Read the current backend/db.py thoroughly — understand every function signature and return type
2. Read how db.py functions are CALLED in: main.py, pipeline/triage.py, pipeline/search.py, pipeline/extract.py, pipeline/gap_fill.py, pipeline/validate.py, utils/cost_tracker.py
3. Create the SQLAlchemy models, session management, and compatibility wrapper as specified
4. Rewrite db.py with the CompatConnection/CompatRow/CompatResult pattern from the plan
5. Create the migration script
6. Add required packages to requirements

CRITICAL CONSTRAINT: The CompatConnection wrapper must support EXACTLY the same usage patterns as the current sqlite3 code. Specifically:
- conn = get_db_connection() must work
- conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone() must work
- dict(row) must work on returned rows (convert CompatRow to dict)
- row['column_name'] must work on returned rows
- conn.cursor() must return something with .execute() (used in main.py upload)
- conn.commit() and conn.close() must work
- The ? placeholder style must be converted to :param style for SQLAlchemy

IMPORTANT: The CompatRow class must support dict() conversion. Add a __iter__ that yields (key, value) pairs, or add items()/values() methods. Test this explicitly because dict(row) is used throughout main.py's _build_export_row function.

RULES:
- Follow the plan exactly.
- Read each file BEFORE modifying it.
- Do NOT modify any pipeline files in this phase — only db.py and new database/ files.
- The system must work with BOTH SQLite (no DATABASE_URL set) and PostgreSQL (DATABASE_URL set).
- If you need to look up SQLAlchemy 2.0 patterns, use web search.
- Boolean values: SQLite uses 0/1, PostgreSQL uses true/false. Make sure the CompatConnection handles this in queries (the existing code uses integers like `scrape_success = 1`). SQLAlchemy with text() handles this, but verify.

VERIFICATION:
1. Without DATABASE_URL set (SQLite fallback):
   - Start the server: cd backend && python main.py
   - Verify it creates products.db and starts without errors
   - Hit GET /api/products — should return empty list (or existing data if DB exists)
   - Hit GET /api/dashboard/stats — should return valid stats JSON

2. With PostgreSQL (if available):
   - Set DATABASE_URL=postgresql://user:pass@localhost:5432/test_enrichment
   - Start the server — should create tables in Postgres
   - Same endpoint tests as above

3. Run the import check:
   python -c "from database import init_engine, get_session, Product, ScrapedPage; print('Database models OK')"

4. Test the compat layer:
   python -c "
from db import get_db_connection, init_db
init_db()
conn = get_db_connection()
conn.execute(\"INSERT INTO products (ean, product_name) VALUES (?, ?)\", ('1234', 'Test Product'))
conn.commit()
row = conn.execute(\"SELECT * FROM products WHERE ean = ?\", ('1234',)).fetchone()
print(f'Row access: {row[\"ean\"]} - {row[\"product_name\"]}')
print(f'Dict conversion: {dict(row)}')
conn.execute(\"DELETE FROM products WHERE ean = ?\", ('1234',))
conn.commit()
conn.close()
print('Compat layer OK')
"

IF YOU NEED HELP:
- If dict(row) doesn't work, you need to add __iter__ to CompatRow that yields (key, value) tuples.
- If PostgreSQL is not installed locally, test with SQLite only and tell me. The Postgres testing can happen later in Docker.
- If you encounter "no such table" errors, make sure init_engine() is called before any queries.
- If you need to install packages: pip install sqlalchemy psycopg2-binary

DO NOT proceed to Phase 3. Stop after Phase 2 is verified.
```

---

### Prompt for Phase 3: Authentication System

```
You are implementing Phase 3 of a SaaS migration for a product enrichment engine.

READ the full plan: SAAS_MIGRATION_PLAN.md — specifically "Phase 3: Authentication System". Read it completely before writing any code. Also skim Phase 1-2 to understand what's already in place.

CONTEXT: The FastAPI backend now has a config system (Phase 1) and SQLAlchemy database layer (Phase 2). All endpoints are currently unprotected — anyone can access them. You are adding email/password authentication with JWT tokens and role-based access (admin/viewer).

YOUR TASK:
1. Add the User model to database/models.py
2. Create the auth module (security.py, dependencies.py, router.py) as specified
3. Create the admin seed script
4. Modify main.py to include the auth router and protect ALL existing endpoints
5. Update .env with JWT_SECRET

RULES:
- Follow the plan exactly.
- Read main.py BEFORE modifying it — understand every endpoint.
- Every GET endpoint gets `user: dict = Depends(get_current_user)` — read access requires login.
- Every POST/PUT/DELETE endpoint gets `user: dict = Depends(require_admin)` — write access requires admin role.
- SSE endpoints (sse_products, sse_product) also need auth — add the dependency.
- The auth router itself (/api/auth/login) must NOT require auth (obviously).
- Use a strong default JWT_SECRET but log a warning if the default is still in use.
- Password minimum length: 8 characters. Validate in the register endpoint.
- The create_admin.py script must work standalone (import and init the database itself).

IMPORTANT — CORS: The frontend sends Authorization headers. The current CORS config already has `allow_headers=["*"]` which covers this. Do NOT change CORS unless something breaks.

IMPORTANT — SSE AUTH: For SSE endpoints, the Bearer token may come as a query parameter instead of a header (browsers can't set headers on EventSource). Add support for both:
```python
# In dependencies.py, modify get_current_user to also check query param:
from fastapi import Query
def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    token: str = Query(None, alias="token"),  # fallback for SSE
) -> dict:
    actual_token = credentials.credentials if credentials else token
    if not actual_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    # ... rest of the function uses actual_token
```

However, FastAPI's HTTPBearer will reject requests without Authorization header before your function runs. To fix this, set `auto_error=False`:
```python
security = HTTPBearer(auto_error=False)
```

VERIFICATION:
1. Start the server, verify it starts without errors
2. Test unprotected access is blocked:
   curl http://localhost:8000/api/products
   Expected: 401 Unauthorized

3. Create admin user:
   cd backend && python scripts/create_admin.py admin@test.com testpass123 "Test Admin"
   Expected: "Admin user created: admin@test.com"

4. Login:
   curl -X POST http://localhost:8000/api/auth/login \
     -H "Content-Type: application/json" \
     -d '{"email": "admin@test.com", "password": "testpass123"}'
   Expected: JSON with access_token

5. Access protected endpoint with token:
   curl http://localhost:8000/api/products \
     -H "Authorization: Bearer <token_from_step_4>"
   Expected: 200 with products list

6. Test /api/auth/me:
   curl http://localhost:8000/api/auth/me \
     -H "Authorization: Bearer <token>"
   Expected: user info JSON

7. Create viewer user and verify they can GET but not POST:
   Use the admin token to POST /api/auth/register with role=viewer
   Login as viewer, try POST /api/products/process-all — should get 403

IF YOU NEED HELP:
- If you need to install packages: pip install python-jose[cryptography] passlib[bcrypt]
- If bcrypt gives installation errors, try: pip install bcrypt passlib python-jose
- If SSE endpoints break with auth, it's likely the HTTPBearer auto_error issue — see the note above.
- If the create_admin.py script can't find modules, check the sys.path insertion at the top.

DO NOT proceed to Phase 4. Stop after Phase 3 is verified.
```

---

### Prompt for Phase 4: Settings API

```
You are implementing Phase 4 of a SaaS migration for a product enrichment engine.

READ the full plan: SAAS_MIGRATION_PLAN.md — specifically "Phase 4: Settings API". Read it completely before writing any code.

CONTEXT: The backend now has a config system (Phase 1, reads from company.json), a PostgreSQL database (Phase 2), and authentication (Phase 3). You are adding API endpoints that let admins view and modify config settings at runtime. Changes are stored in the database and override the file-based defaults.

YOUR TASK:
1. Add the Setting model to database/models.py
2. Create the settings module (router.py) as specified
3. Update config/loader.py so get_config() applies DB overrides automatically
4. Include the settings router in main.py

KEY DESIGN DECISIONS:
- Settings in the DB are stored as key-value pairs where key is a dot-path (e.g., "source.strategy") and value is JSON-encoded.
- Only OVERRIDES are stored — if a setting hasn't been changed, it inherits from company.json.
- The settings API returns the full effective config (file defaults + DB overrides) plus a list of which keys have been overridden.
- Resetting a setting (DELETE) removes the DB override, reverting to the file default.
- All setting updates are validated against the Pydantic schema before saving.

RULES:
- Follow the plan exactly.
- The GET /api/settings/ endpoint must be accessible to any logged-in user (viewer or admin).
- The PUT /api/settings/ and DELETE /api/settings/{key} endpoints require admin role.
- When applying overrides in get_config(), catch exceptions gracefully — if the DB is not yet initialized (e.g., during startup), fall back to file-based config.
- Do NOT cache the effective config aggressively — it should reflect DB changes within the same request cycle. But don't query the DB on every attribute access either. Query once per get_config() call.

IMPORTANT — Circular import prevention: The settings router imports from config, and config.loader may import from settings. Break the cycle by doing the settings import INSIDE the get_config() function body, not at module level:
```python
def get_config() -> CompanyConfig:
    if _config is None:
        raise RuntimeError("Config not loaded.")
    try:
        from settings.router import _get_all_db_overrides, _apply_overrides_to_config
        overrides = _get_all_db_overrides()
        if overrides:
            return _apply_overrides_to_config(_config, overrides)
    except Exception:
        pass
    return _config
```

VERIFICATION:
1. Start server, login as admin, get token

2. Get current settings:
   curl http://localhost:8000/api/settings/ -H "Authorization: Bearer <token>"
   Expected: full config JSON with "overrides": []

3. Update a setting:
   curl -X PUT http://localhost:8000/api/settings/ \
     -H "Authorization: Bearer <token>" \
     -H "Content-Type: application/json" \
     -d '{"key": "cost.max_daily_cost_usd", "value": "100.0"}'
   Expected: 200 with success message

4. Verify override is applied:
   curl http://localhost:8000/api/settings/ -H "Authorization: Bearer <token>"
   Expected: cost.max_daily_cost_usd = 100.0, overrides includes "cost.max_daily_cost_usd"

5. Verify pipeline uses new value:
   curl http://localhost:8000/api/dashboard/limits -H "Authorization: Bearer <token>"
   Expected: max_daily_cost_usd = 100.0

6. Reset the setting:
   curl -X DELETE http://localhost:8000/api/settings/cost.max_daily_cost_usd \
     -H "Authorization: Bearer <token>"
   Expected: reverts to file default

7. Test validation — try an invalid key:
   curl -X PUT http://localhost:8000/api/settings/ \
     -H "Authorization: Bearer <token>" \
     -H "Content-Type: application/json" \
     -d '{"key": "nonexistent.key", "value": "\"foo\""}'
   Expected: 400 error

8. Test validation — try an invalid value:
   curl -X PUT http://localhost:8000/api/settings/ \
     -H "Authorization: Bearer <token>" \
     -H "Content-Type: application/json" \
     -d '{"key": "source.strategy", "value": "\"invalid_strategy\""}'
   Expected: 400 error (Pydantic validation fails because it's not in the enum-like options)
   NOTE: Actually, source.strategy is a plain str, not an enum. So this might not fail. That's OK — the schema doesn't constrain it to specific values. Skip this test if the schema allows any string.

IF YOU NEED HELP:
- If circular imports occur, move the import inside the function body as shown above.
- If the settings table doesn't exist, make sure init_engine() creates it (Base.metadata.create_all should handle it).
- If dot-path traversal fails for nested keys, debug _apply_overrides_to_config — the for/else loop needs to handle the case where a path segment doesn't exist.

DO NOT proceed to Phase 5. Stop after Phase 4 is verified.
```

---

### Prompt for Phase 5: Docker Packaging

```
You are implementing Phase 5 of a SaaS migration for a product enrichment engine.

READ the full plan: SAAS_MIGRATION_PLAN.md — specifically "Phase 5: Docker Packaging". Read it completely before writing any code.

CONTEXT: The backend now has config, PostgreSQL, auth, and settings API (Phases 1-4). You are packaging everything into Docker containers for production deployment.

YOUR TASK:
1. Create the Dockerfile for the FastAPI backend
2. Create .dockerignore
3. Create the docker-compose.yml template
4. Create the .env template for customer instances
5. Create the new-customer.sh and update-all.sh scripts
6. Create the GitHub Actions CI/CD workflow (optional but do it)

IMPORTANT — Requirements file: Before creating the Dockerfile, check if backend/requirements.txt exists. If not, create it by examining all imports across the codebase. The key packages are:
- fastapi
- uvicorn[standard]
- python-multipart (for file uploads)
- pandas
- openpyxl (for Excel export)
- anthropic[vertex] (for Claude via Vertex AI)
- google-auth (for service account)
- tavily-python
- firecrawl-py
- langgraph
- pydantic
- python-dotenv
- sqlalchemy>=2.0
- psycopg2-binary>=2.9
- python-jose[cryptography]>=3.3
- passlib[bcrypt]>=1.7
- httpx (used in extract.py for image HEAD requests)

If you're unsure about exact package names, use web search to verify the correct PyPI package name.

RULES:
- Follow the plan exactly.
- The Dockerfile should use python:3.12-slim as base.
- The docker-compose.yml must include a healthcheck on the Postgres container so the API waits for it.
- The .env template must have comments explaining each variable.
- The shell scripts must be executable (chmod +x) and use bash.
- The GitHub Actions workflow should only trigger on changes to backend/ files.
- Use ghcr.io (GitHub Container Registry) for the Docker image — it's free for public repos.
- In docker-compose.yml, the API container should mount config/ as read-only.

VERIFICATION:
1. Build the Docker image locally:
   cd backend && docker build -t enrichment-engine .
   Expected: builds successfully without errors

2. Test with docker compose (create a temp test directory):
   mkdir -p /tmp/test-enrichment/config
   cp config/shoppster.json /tmp/test-enrichment/config/company.json
   cp docker-compose.yml /tmp/test-enrichment/
   # Create a minimal .env in /tmp/test-enrichment/
   cd /tmp/test-enrichment && docker compose up -d
   Expected: both api and db containers start

3. Verify the API is reachable:
   curl http://localhost:8000/docs
   Expected: FastAPI Swagger UI page

4. Stop and clean up:
   docker compose down -v

IF YOU NEED HELP:
- If the Docker build fails on psycopg2, make sure the Dockerfile installs gcc and libpq-dev.
- If you can't figure out the exact pip package name for a dependency, search PyPI: https://pypi.org
- If Docker is not installed locally, create all the files but tell me you couldn't test the build. I'll test it.
- If the GitHub Actions workflow needs the repository name, use a placeholder (YOUR_GITHUB_USER) and tell me to replace it.
- The service-account.json file is sensitive — it should be mounted as a volume, never baked into the image.

DO NOT proceed to Phase 6. Stop after Phase 5 is verified.
```

---

### Prompt for Phase 6: Shoppster Migration (First Customer)

```
You are implementing Phase 6 (final phase) of a SaaS migration for a product enrichment engine.

READ the full plan: SAAS_MIGRATION_PLAN.md — specifically "Phase 6: Shoppster Migration". Read it completely.

CONTEXT: All infrastructure is built (Phases 1-5). You are setting up Shoppster as the first production customer. This phase is mostly operational — creating config files, running scripts, verifying the deployment.

YOUR TASK:
1. Verify that all Phase 1-5 deliverables exist and are correct
2. Create a final pre-deployment checklist
3. Document the exact commands needed to deploy on a VPS
4. Create a simple README for operating the system

IMPORTANT: This phase requires access to a VPS and real credentials. You CANNOT do the actual deployment. Instead, create:

1. A deployment guide: backend/docs/DEPLOYMENT.md with step-by-step instructions
2. A troubleshooting guide: common issues and fixes
3. A customer onboarding checklist: what to do when adding a new customer

The deployment guide should include:
- VPS setup (install Docker, create directories)
- First customer deployment (Shoppster)
- Adding a new customer (Merkur)
- Updating all instances
- Monitoring and logs
- Backup strategy (pg_dump commands)
- How to access logs: docker compose logs -f api

ALSO create a summary document listing:
- All API endpoints (existing + new auth/settings ones)
- All config keys with descriptions
- All environment variables with descriptions

RULES:
- Follow the plan exactly.
- Write docs in clear, step-by-step format. Assume the reader is technical but has never seen this codebase.
- Include exact commands, not just descriptions.
- For passwords and secrets, always use placeholder values and note "CHANGE THIS".

VERIFICATION:
1. Read through DEPLOYMENT.md yourself — does every step make sense? Would a developer be able to follow it without asking questions?
2. Verify all file paths referenced in the docs actually exist in the codebase.
3. Run a final check: start the server locally and verify all new endpoints work:
   - POST /api/auth/login
   - GET /api/auth/me
   - GET /api/settings/
   - PUT /api/settings/
   - DELETE /api/settings/{key}
   - All existing endpoints still work with auth

STOP AND ASK ME:
- Before writing the deployment guide, ask me for the VPS IP/hostname so you can include it in the docs.
- Ask me for the GitHub repository URL for the CI/CD workflow.
- Ask me if there are any customer-specific details I want included.

This is the final phase. After verification, summarize everything that was built across all 6 phases.
```

---

## How to Use These Prompts

1. **Start a NEW conversation with Sonnet for each phase.** Do not run multiple phases in one conversation — context window will overflow.

2. **Copy-paste the phase prompt as your first message.** Sonnet will read the plan file and execute.

3. **After Sonnet finishes a phase, verify its work manually** before starting the next phase. Run the verification commands yourself.

4. **If Sonnet gets stuck or makes mistakes,** paste the error output and say "fix this." If it goes in circles, start a fresh conversation with the same prompt plus "Previous attempt failed because: [error description]."

5. **Phase order matters.** Each phase builds on the previous:
   - Phase 1 (Config) — standalone, no dependencies
   - Phase 2 (Database) — needs Phase 1 config loader
   - Phase 3 (Auth) — needs Phase 2 database models
   - Phase 4 (Settings) — needs Phase 1 config + Phase 2 database + Phase 3 auth
   - Phase 5 (Docker) — needs all of the above
   - Phase 6 (Deployment) — needs all of the above

6. **Between phases, commit your code:** `git add -A && git commit -m "Phase N complete"` — this gives you a clean rollback point if the next phase goes wrong.
