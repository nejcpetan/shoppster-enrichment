# Product Data Enrichment Engine

AI-powered product data enrichment pipeline built with **LangGraph** for orchestration, **Claude Haiku 4.5** for reasoning, **Tavily** for web search, and **Firecrawl** for web scraping.

Takes incomplete product data (EAN, name) and enriches it with dimensions, weight, color, country of origin, and images from authoritative web sources — manufacturer sites first, distributors as fallback, third-party sites as a gap-fill safety net.

## Architecture

![Agent Architecture](architecture.svg)

The pipeline follows a **scrape-once, extract-multiple-times** pattern: all scraped page content is cached in SQLite by source tier. Main extraction runs on official and authorized sources only. If critical fields are still missing, the gap-fill agent reads the cached third-party pages and runs a targeted single-pass extraction — no re-scraping needed. Validation runs last, on the complete data.

```
triage → [ean_lookup?] → search → extract → gap_fill → validate → save_costs
```

### Pipeline Agents

| Agent | Model | Role | Tools |
|-------|-------|------|-------|
| **Triage** | Haiku 4.5 | Classify product type, identify brand, parse name | Claude structured output |
| **EAN Lookup** | Haiku 4.5 | Find brand from barcode database (conditional) | Firecrawl scrape |
| **Search** | Haiku 4.5 | Find product pages, classify URLs by source type | Tavily search, Claude |
| **Extract** | Haiku 4.5 | Scrape all tiers, cache markdown, extract from official + authorized | Firecrawl, Claude, SQLite cache |
| **Gemini Vision** | Gemini 2.0 Flash | Detect product color from image (sub-agent, fires if text extraction fails) | Vertex AI Vision |
| **Gap Fill** | Haiku 4.5 | Targeted extraction of critical missing fields from cached third-party pages | Claude (Mode A cached system prompt) |
| **Validate** | Haiku 4.5 | Normalize units, sanity check, quality scoring on complete data | Claude, normalization engine |

## Prerequisites

- **Node.js** 18+ and npm
- **Python** 3.11+
- **Google Cloud** project with Vertex AI API enabled
- **API Keys** for Tavily and Firecrawl

## Credentials Setup

### 1. Google Cloud (Vertex AI — for Claude)

1. Create a Google Cloud project or use an existing one
2. Enable the **Vertex AI API** in the Google Cloud Console
3. Enable **Claude models** via the Vertex AI Model Garden (search for "Claude")
4. Create a **service account** with the Vertex AI User role
5. Download the service account JSON key file
6. Place it in the `backend/` directory

```bash
# Set in backend/.env
VERTEX_PROJECT_ID=your-gcp-project-id
VERTEX_LOCATION=us-east5
GOOGLE_APPLICATION_CREDENTIALS=path/to/your-service-account.json
```

> **Note:** `us-east5` is the primary region for Claude on Vertex AI. Check [Google's docs](https://cloud.google.com/vertex-ai/generative-ai/docs/partner-models/use-claude) for current availability.

### 2. Tavily (Web Search)

1. Sign up at [tavily.com](https://tavily.com)
2. Get your API key from the dashboard
3. Free tier: 1,000 searches/month

```bash
TAVILY_API_KEY=tvly-your-key-here
```

### 3. Firecrawl (Web Scraping)

1. Sign up at [firecrawl.dev](https://www.firecrawl.dev)
2. Get your API key from the dashboard
3. Free tier: 500 credits

```bash
FIRECRAWL_API_KEY=fc-your-key-here
```

## Installation

### Backend

```bash
cd backend

# Create virtual environment
python -m venv venv

# Activate it
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy env template and fill in your credentials
cp .env.example .env
# Edit .env with your API keys
```

### Frontend

```bash
# From project root
npm install
```

## Running

### Start the backend (port 8000)

```bash
cd backend
# Activate venv first (see above)
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Start the frontend (port 3000)

```bash
# From project root
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

## Usage

1. **Upload CSV/XLSX** — Click "Upload" on the dashboard. CSV needs at minimum `EAN` and `Name` columns.
2. **Select products** — Check the products you want to enrich in the table.
3. **Run enrichment** — Click "Run Enrichment" in the floating action bar, or "Process All" to enrich all pending products.
4. **Watch agents work** — The table shows real-time progress: which agent is active, what step it's on, and pipeline progress dots.
5. **Review results** — Click any product row to see the full detail view with enrichment log, extracted data, and validation report.
6. **Export** — Download enriched data as XLSX from the product detail page or export all from the dashboard.

## Project Structure

```
├── app/                    # Next.js pages
│   ├── page.tsx            # Dashboard
│   └── products/[id]/      # Product detail page
├── components/
│   ├── ProductTable.tsx    # Main table with agent activity
│   ├── ProductDetail.tsx   # Full product view
│   └── UploadCSV.tsx       # File upload
├── backend/
│   ├── main.py             # FastAPI routes
│   ├── graph.py            # LangGraph state machine (triage→search→extract→gap_fill→validate→save_costs)
│   ├── db.py               # SQLite + helpers (products, brand_coo_cache, scraped_pages)
│   ├── schemas.py          # All Pydantic models (EnrichedProduct, GapFillExtraction, etc.)
│   ├── pipeline/
│   │   ├── triage.py       # Phase 1: Classification agent
│   │   ├── search.py       # Phase 2: Search agent
│   │   ├── extract.py      # Phase 3: Extraction agent (scrapes all tiers, caches pages)
│   │   ├── validate.py     # Phase 4: Validation agent
│   │   └── gap_fill.py     # Phase 4.5: Third-party gap-fill agent
│   └── utils/
│       ├── llm.py          # Anthropic Vertex AI setup + prompt caching (Mode A + B)
│       ├── gemini_vision.py # Gemini 2.0 Flash color detection
│       ├── ean_lookup.py   # Barcode lookup utility
│       ├── normalization.py # Unit conversion
│       └── cost_tracker.py # Per-product cost accounting + guardrails
├── .documentation/         # Internal research & optimization docs
├── architecture.svg        # Agent architecture diagram
└── README.md
```

## Cost Optimization

The pipeline implements several cost optimization techniques to minimize API spend while maintaining data quality.

### Prompt Caching (Anthropic)

All Claude API calls use Anthropic's prompt caching (`cache_control: {"type": "ephemeral"}`):

- **System prompt caching (Mode A)**: System prompts + JSON schemas are cached across products within a 5-minute TTL window. Phases: triage, search, validate, COO lookup. Subsequent products in a batch hit the cache and pay 90% less on input tokens for the cached prefix.
- **Page content caching (Mode B)**: In extraction, the scraped markdown is placed in the system message (cached) and pass-specific instructions go in the user message. Pass 2 gets a cache hit on the markdown tokens from Pass 1, saving ~90% on 5,000-8,000 tokens per URL.
- Cache writes cost 1.25x base input price, cache reads cost 0.1x. Caches auto-expire after 5 minutes (refreshed on each hit). No manual clearing needed; expired caches cost nothing.

### Scraped Pages Cache (SQLite)

All scraped page markdown is stored in the `scraped_pages` table, keyed by `product_id + url`, with a `source_type` column (`manufacturer`, `authorized_distributor`, `third_party`). This implements the **"scrape once, extract multiple times"** pattern:

- **Extract phase**: Scrapes all tiers (official, authorized, third-party). Runs LLM extraction (Pass 1 + Pass 2) only on manufacturer and authorized pages, using **Mode B caching** (page markdown in system message, cached between passes — Pass 2 gets a 90% cache read). Third-party pages are scraped and cached in SQLite but no LLM calls are made at this stage.
- **Gap Fill phase** (runs before validation): Checks the extraction result for critical missing fields. If gaps exist and cached third-party pages are available, runs a single targeted LLM call per page using **Mode A caching** (static system prompt + schema cached across pages — page 2+ gets a cache read). Page content stays in the user message to avoid the 1.25x cache-write premium on 10-30K tokens.
- **Critical gaps** that trigger gap fill: `net_weight`, `packaged_weight`, all three packaged dimensions, `warranty duration`, `short_description`.
- **Early exit**: As soon as all gaps are filled across accumulated results, remaining pages are skipped.
- **Never overwrites**: Gap-filled data only fills fields that are currently `null` — existing extraction data is never replaced.

### Brand COO Cache (SQLite)

Country-of-origin lookups are cached in `brand_coo_cache` table. Brand-to-country mappings are static (e.g., "Makita" = Japan, "Bosch" = Germany), so repeat brands skip the Tavily search + Claude call entirely. Saves 1 Tavily credit + 1 LLM call per repeat brand.

### Deterministic Image Filtering

Product images are filtered using HTTP HEAD requests + URL heuristics instead of AI vision calls. Only 1 Gemini call is made per product (for color detection), and only when text extraction fails to find a color.

### Cost Guardrails

Runtime-configurable limits prevent runaway spend:

| Limit | Default | Env Var |
|-------|---------|---------|
| Daily product limit | 200 | `DAILY_PRODUCT_LIMIT` |
| Max batch size | 50 | `MAX_BATCH_SIZE` |
| Daily cost budget | $50.00 | `MAX_DAILY_COST_USD` |

Limits can be adjusted at runtime via `PUT /api/dashboard/limits`.

### Cost Tracking

Every API call is tracked per-product with full provenance:
- Token counts (input, output, cache read, cache write) per LLM call
- Credit usage per API call (Firecrawl, Tavily)
- Cost breakdown by pipeline phase and by service
- Cache hit rate percentage
- All data persisted in `cost_data` column per product

### Cost Estimates

Per product (with prompt caching enabled):

| Service | Usage | Est. Cost |
|---------|-------|-----------|
| Tavily | 1-3 searches | $0.008-0.024 |
| Firecrawl | 4-8 scrapes (all tiers cached) | $0.003-0.006 |
| Claude Haiku 4.5 | 4-6 LLM calls, extract (cached) | $0.005-0.015 |
| Claude Haiku 4.5 | 0-3 LLM calls, gap fill (targeted) | $0.000-0.009 |
| Gemini Flash | 0-1 vision call | $0.000-0.001 |
| **Total** | | **$0.02-0.06** |

*Costs are in USD. Batch processing within the 5-minute cache window yields the best savings. Gap fill is a no-op (zero cost) when extraction is already complete — the typical overhead is ~$0.003-0.005.*

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Frontend | Next.js 16, TypeScript, Tailwind CSS, shadcn/ui |
| Backend | Python 3.11+, FastAPI, SQLite |
| Orchestration | LangGraph (state machine) |
| LLM (Text) | Claude Haiku 4.5 via Google Vertex AI |
| LLM (Vision) | Gemini 2.0 Flash via Google Vertex AI |
| Search | Tavily API |
| Scraping | Firecrawl API |
| Schemas | Pydantic v2 |
