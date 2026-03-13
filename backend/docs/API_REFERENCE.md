# API Reference — Product Enrichment Engine

Base URL (production): `https://enrichment.webfast.si`
Base URL (local dev): `http://localhost:8000`

All endpoints except `/api/auth/login` require a `Bearer` token in the `Authorization` header.

---

## Authentication

### POST /api/auth/login
Login and get a JWT token. **No auth required.**

```json
// Request
{ "email": "admin@shoppster.com", "password": "yourpassword" }

// Response
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "user": { "id": 1, "email": "...", "full_name": "...", "role": "admin" }
}
```

### POST /api/auth/register
Create a new user. **Admin only.**

```json
// Request
{ "email": "user@example.com", "password": "min8chars", "full_name": "Jane Doe", "role": "viewer" }
// role: "admin" or "viewer"
```

### GET /api/auth/me
Returns the current user's info.

### POST /api/auth/change-password
Change your own password.

```json
{ "current_password": "old", "new_password": "newmin8chars" }
```

---

## Products

### GET /api/products
List all products, newest first.

### GET /api/products/{id}
Get a single product by ID.

### POST /api/upload
Upload a CSV or Excel file of products. **Admin only.**

Accepts `multipart/form-data` with a `file` field. Expected columns: `EAN`, `Name` (or `naziv`), `Brand`, `Weight`.

### POST /api/products/add
Add a single product manually. **Admin only.**

```json
{ "ean": "1234567890123", "product_name": "Makita DHP484Z Drill" }
```

### POST /api/products/process-all
Enrich all `pending` and `needs_review` products. Respects batch size limit. **Admin only.**

### POST /api/products/process-batch
Enrich a specific list of products. **Admin only.**

```json
{ "product_ids": [1, 2, 3] }
```

### POST /api/products/{id}/enrich
Run the full pipeline on a single product. **Admin only.**

### POST /api/products/{id}/reset
Reset a product back to `pending`, clearing all enrichment data. **Admin only.**

### POST /api/products/{id}/classify
Run Phase 1 (triage/classification) only. **Admin only.**

### POST /api/products/{id}/search
Run Phase 2 (search) only. **Admin only.**

### POST /api/products/{id}/extract
Run Phase 3 (extraction) only. **Admin only.**

### POST /api/products/{id}/validate
Run Phase 4 (validation) only. **Admin only.**

### GET /api/products/{id}/export
Download a single product as an Excel file.

---

## Export

### GET /api/export
Download all products as an Excel file.

---

## Real-Time Events (SSE)

Connect via `EventSource` in the browser. Send the token as a query parameter: `?token=eyJ...`

### GET /api/events/products
Global stream — receives status and log events for all products.

### GET /api/events/products/{id}
Per-product stream — receives events for a single product. Sends an initial `snapshot` event with current status.

Event types: `snapshot`, `status`, `log`, `connected`

---

## Dashboard

### GET /api/dashboard/stats
Product counts by status.

```json
{ "total": 100, "pending": 20, "done": 70, "errors": 5, "needs_review": 3, "processing": 2 }
```

### GET /api/dashboard/costs
Daily cost stats and all-time aggregates.

### GET /api/dashboard/limits
Current guardrail limits (cost cap, batch size, daily product limit).

### PUT /api/dashboard/limits
Update guardrail limits at runtime. **Admin only.**

```json
{
  "daily_product_limit": 200,
  "max_batch_size": 50,
  "max_daily_cost_usd": 50.0,
  "market_region": "Slovenia"
}
```

---

## Settings (Company Config)

### GET /api/settings/
Returns the full effective config (company.json defaults + any DB overrides).

```json
{
  "config": { "source": { "strategy": "official_first", ... }, ... },
  "overrides": ["cost.max_daily_cost_usd"]
}
```

### PUT /api/settings/
Update a single config value at runtime. Stored in DB, overrides company.json. **Admin only.**

```json
{ "key": "cost.max_daily_cost_usd", "value": "100.0" }
```

Keys use dot-notation matching the config schema: `source.strategy`, `pipeline.enable_gap_fill`, `cost.max_daily_cost_usd`, etc. Values must be JSON-encoded strings.

### DELETE /api/settings/{key}
Remove a DB override, reverting to the company.json default. **Admin only.**

```
DELETE /api/settings/cost.max_daily_cost_usd
```

---

## Product Status Flow

```
pending → enriching → classifying → searching → extracting → gap_filling → validating → done
                                                                                      ↘ needs_review
                                                                                      ↘ error
```

---

## Config Keys Reference

All keys use dot-notation for the Settings API (e.g. `source.strategy`).

### source.*
| Key | Type | Default (Shoppster) | Description |
|-----|------|---------------------|-------------|
| `source.strategy` | string | `"official_first"` | `"official_first"` = manufacturer domain first, then general. `"any_source"` = search everywhere equally. `"official_only"` = manufacturer + authorized only. |
| `source.search_provider` | string | `"tavily"` | `"tavily"` or `"firecrawl"` |
| `source.max_manufacturer_results` | int | `5` | Max results from manufacturer-targeted search |
| `source.max_general_results` | int | `7` | Max results per general query |
| `source.max_general_queries` | int | `3` | Max number of general search queries |
| `source.max_total_results` | int | `6` | Stop collecting results after this many total |
| `source.allowed_domains` | list | `[]` | If non-empty, only search these domains |
| `source.blocked_domains` | list | `[]` | Never use results from these domains |
| `source.trust_third_party_as_primary` | bool | `false` | If true, treat third-party sources as equally trustworthy as authorized distributors |

### pipeline.*
| Key | Type | Default (Shoppster) | Description |
|-----|------|---------------------|-------------|
| `pipeline.enable_ean_lookup` | bool | `true` | Enable EAN-based lookups |
| `pipeline.enable_gap_fill` | bool | `true` | Enable gap-fill phase (searches third-party for missing fields) |
| `pipeline.enable_gemini_vision` | bool | `true` | Use Gemini Vision for color detection from images |
| `pipeline.max_pages_to_scrape` | int | `5` | Max pages to extract from in the extraction phase |
| `pipeline.max_gap_fill_pages` | int | `3` | Max third-party pages to check during gap-fill |
| `pipeline.gap_fill_is_primary` | bool | `false` | If true, gap-fill runs with higher priority |

### critical_fields.*
| Key | Type | Default (Shoppster) | Description |
|-----|------|---------------------|-------------|
| `critical_fields.net_weight` | bool | `true` | Trigger gap-fill if net weight is missing |
| `critical_fields.packaged_weight` | bool | `true` | Trigger gap-fill if packaged weight is missing |
| `critical_fields.packaged_dims` | bool | `true` | Trigger gap-fill if packaged dimensions are missing |
| `critical_fields.warranty` | bool | `true` | Trigger gap-fill if warranty info is missing |
| `critical_fields.short_description` | bool | `true` | Trigger gap-fill if short description is missing |
| `critical_fields.color` | bool | `false` | Trigger gap-fill if color is missing |
| `critical_fields.country_of_origin` | bool | `false` | Trigger gap-fill if country of origin is missing |

### language.*
| Key | Type | Default (Shoppster) | Description |
|-----|------|---------------------|-------------|
| `language.primary_languages` | list | `["sl", "en"]` | Languages to look for in product data. First = primary. |
| `language.output_language` | string | `"en"` | Language for generated descriptions and features |
| `language.extra_color_mappings` | dict | `{}` | Additional color name translations (e.g. `{"türkis": "turquoise"}`) |
| `language.extra_country_mappings` | dict | `{}` | Additional country name translations |

### cost.*
| Key | Type | Default (Shoppster) | Description |
|-----|------|---------------------|-------------|
| `cost.max_daily_cost_usd` | float | `50.0` | Daily API cost cap in USD |
| `cost.daily_product_limit` | int | `200` | Max products to enrich per day |
| `cost.max_batch_size` | int | `50` | Max products per batch job |
| `cost.market_region` | string | `""` | Appended to search queries for regional relevance (e.g. `"Slovenia"`) |

### llm.*
| Key | Type | Default (Shoppster) | Description |
|-----|------|---------------------|-------------|
| `llm.triage_model` | string | `"haiku"` | Model for classification phase. `"haiku"` or `"sonnet"` |
| `llm.search_model` | string | `"haiku"` | Model for search URL classification |
| `llm.extract_model` | string | `"haiku"` | Model for data extraction |
| `llm.gap_fill_model` | string | `"haiku"` | Model for gap-fill |
| `llm.validate_model` | string | `"haiku"` | Model for validation |

### brands.*
| Key | Type | Default (Shoppster) | Description |
|-----|------|---------------------|-------------|
| `brands.known_brands` | list | `["Makita", "Bosch", ...]` | Brand names shown to the LLM in the triage system prompt to help identify brands |
| `brands.brand_coo_seeds` | dict | `{}` | Pre-seeded brand → country of origin mappings (e.g. `{"Makita": "Japan"}`) |

### export.*
| Key | Type | Default (Shoppster) | Description |
|-----|------|---------------------|-------------|
| `export.include_cost_data` | bool | `false` | Include per-product cost data in exports |
| `export.include_enrichment_log` | bool | `false` | Include the enrichment log in exports |
| `export.column_overrides` | dict | `{}` | Rename export columns (e.g. `{"short_description": "Kratek opis"}`) |

---

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `COMPANY_CONFIG_PATH` | Yes | Path to company.json (default: `config/company.json`) |
| `DATABASE_URL` | Yes | PostgreSQL connection string. Falls back to `sqlite:///products.db` if not set. |
| `DB_USER` | Yes | Postgres username (used by docker-compose) |
| `DB_PASSWORD` | Yes | Postgres password (used by docker-compose) |
| `DB_NAME` | Yes | Postgres database name (used by docker-compose) |
| `VERTEX_PROJECT_ID` | Yes | GCP project ID with Vertex AI enabled |
| `VERTEX_LOCATION` | Yes | GCP region (use `europe-west1` for EU) |
| `GOOGLE_APPLICATION_CREDENTIALS` | Yes | Path to GCP service account JSON file |
| `TAVILY_API_KEY` | Yes | Tavily search API key |
| `FIRECRAWL_API_KEY` | No | Firecrawl API key (only needed if `source.search_provider = "firecrawl"`) |
| `JWT_SECRET` | Yes | 32-byte hex string for signing JWT tokens. Generate: `openssl rand -hex 32` |
| `API_PORT` | No | Port the API listens on inside docker-compose (default: `8000`) |

---

## Shoppster Config (Current Defaults)

This is what Shoppster runs with. The pipeline behavior is identical to what was hardcoded before the multi-customer migration.

```json
{
  "company_name": "Shoppster",
  "source": {
    "strategy": "official_first",
    "search_provider": "tavily",
    "max_manufacturer_results": 5,
    "max_general_results": 7,
    "max_general_queries": 3,
    "max_total_results": 6,
    "trust_third_party_as_primary": false
  },
  "pipeline": {
    "enable_ean_lookup": true,
    "enable_gap_fill": true,
    "enable_gemini_vision": true,
    "max_pages_to_scrape": 5,
    "max_gap_fill_pages": 3,
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
    "output_language": "en"
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
    ]
  }
}
```

**What this means in practice:**
- Searches manufacturer websites first (e.g. `makita.com`), only falls back to general search if manufacturer data is incomplete
- Enriches descriptions and dimensions in English
- Gap-fill triggers when net weight, packaged weight, packaged dimensions, warranty, or short description are missing
- Uses Claude Haiku for all pipeline phases (fast and cheap)
- Daily cost cap: $50, up to 200 products/day, max 50 per batch
- No regional search bias (market_region is empty)
