# PRODUCT ENRICHMENT ENGINE — BUILD GUIDE
## For AI Coding Agents

**What this is:** A phase-by-phase implementation guide. Each phase is a self-contained prompt you give to your AI coding agent. Complete Phase 0, then Phase 1 in a new chat, and so on. Each phase builds on the last.

**Stack:** Next.js (latest - 16.1.6) frontend + Python FastAPI backend. Monolithic repo. SQLite for persistence. No Docker, no message queues, no Redis. Keep it simple.

**What we're building:** A UI that lets you upload a CSV of products, runs them through an AI enrichment pipeline (classify → search → scrape → extract → validate), and shows the results with confidence scores and source attribution.

its the year 2026, so do up to date search and research when needed.

---

# PHASE 0: PROJECT SCAFFOLDING

## Give this to the AI:

```
Build me a monolithic project with this structure:

enrichment-engine/
├── frontend/          # Next.js app
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx              # Dashboard: upload CSV, see product list
│   │   └── products/
│   │       └── [id]/
│   │           └── page.tsx      # Single product detail view
│   ├── components/
│   │   ├── ProductTable.tsx      # Table showing all products + status
│   │   ├── ProductDetail.tsx     # Full enrichment results for one product
│   │   ├── UploadCSV.tsx         # File upload component
│   │   ├── ConfidenceBadge.tsx   # Visual badge: green/yellow/orange/red
│   │   └── EnrichmentLog.tsx     # Shows pipeline steps + what happened
│   ├── lib/
│   │   └── api.ts               # Fetch wrapper for backend calls
│   ├── package.json
│   └── tailwind.config.ts
│
├── backend/
│   ├── main.py                  # FastAPI app, all routes
│   ├── db.py                    # SQLite setup with sqlite3 (no ORM)
│   ├── schemas.py               # Pydantic models (shared truth)
│   ├── pipeline/
│   │   ├── triage.py            # Phase 1: classify + brand ID
│   │   ├── search.py            # Phase 2: Tavily search
│   │   ├── extract.py           # Phase 3: Firecrawl + LLM extraction
│   │   └── validate.py          # Phase 4: normalize + confidence
│   ├── utils/
│   │   ├── normalization.py     # Unit conversion (cm, kg, L)
│   │   └── llm.py               # LLM client setup (Claude)
│   ├── requirements.txt
│   └── .env
│
└── README.md

Technical decisions:
- Frontend: Next.js with App Router, Tailwind CSS, TypeScript. ShadCN UI components and luicide icons.
- Backend: FastAPI with uvicorn. SQLite via raw sqlite3 module (no SQLAlchemy, no ORM). One db.py file handles all SQL.
- Communication: REST API. Frontend calls backend on localhost:8000.
- No authentication. No deployment config. This is a local validation tool.

For Phase 0, build ONLY:
1. The Next.js app with a working layout, the dashboard page with a placeholder ProductTable and UploadCSV component.
2. The FastAPI app with these endpoints (return dummy data for now):
   - POST /api/upload — accepts CSV file, returns list of parsed products
   - GET /api/products — returns all products from SQLite
   - GET /api/products/{id} — returns single product with enrichment data
   - POST /api/products/{id}/enrich — triggers enrichment (placeholder, returns "not implemented")
3. SQLite database with this schema:

CREATE TABLE products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ean TEXT NOT NULL,
    product_name TEXT NOT NULL,
    brand TEXT,
    weight TEXT,
    -- original data from CSV, stored as-is
    original_data TEXT,  -- full JSON of the CSV row
    -- enrichment state
    status TEXT DEFAULT 'pending',  -- pending | enriching | done | error
    product_type TEXT,  -- standard_product | accessory | liquid | etc.
    -- enrichment results stored as JSON blobs
    classification_result TEXT,  -- JSON from Phase 1
    search_result TEXT,          -- JSON from Phase 2
    extraction_result TEXT,      -- JSON from Phase 3
    validation_result TEXT,      -- JSON from Phase 4 (final enriched data)
    -- log of what happened
    enrichment_log TEXT,         -- JSON array of log entries
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

4. The UploadCSV component should parse the uploaded CSV and POST it to /api/upload. The backend parses it with pandas and inserts each row into the products table.
5. The ProductTable shows: EAN, product name, brand, status (with a colored dot: gray=pending, blue=enriching, green=done, red=error). Clicking a row goes to /products/{id}.
6. The product detail page shows the product info and placeholder sections for each enrichment phase (empty for now).
7. Frontend uses a simple fetch wrapper in lib/api.ts that prefixes all calls with http://localhost:8000.
8. Add CORS middleware to FastAPI allowing localhost:3000.
9. requirements.txt should include: fastapi, uvicorn, pandas, openpyxl, python-dotenv, pydantic

Make sure both apps start cleanly:
- cd frontend && npm run dev (port 3000)
- cd backend && uvicorn main:app --reload (port 8000)

Keep the UI clean and minimal. White background, clear typography. No gradients, no hero sections. This is an internal tool.
```

### What you should have after Phase 0:
- Working Next.js app on :3000 with a dashboard showing an upload button and empty product table
- Working FastAPI on :8000 with endpoints that accept CSV uploads and store in SQLite
- You can upload `webfast_sample_izdelki.xlsx` (export as CSV first, or add xlsx parsing to the upload endpoint — tell the AI which format you want)
- Products appear in the table after upload
- Clicking a product shows a detail page (mostly empty)

---

# PHASE 1: PYDANTIC SCHEMAS + CLASSIFICATION PIPELINE

## Give this to the AI:

```
I'm building a product data enrichment pipeline. Phase 0 (project scaffolding) is done. Now implement Phase 1: the Pydantic schemas and the product classification step.

CONTEXT: We have products in a SQLite database with EAN codes and Slovenian product names. The first step of enrichment is classifying what TYPE of product it is, because different product types need different data schemas (a power tool has height/length/width, but a wire brush has diameter, and motor oil has volume).

STEP 1: Add the Pydantic schemas to backend/schemas.py

These are the core data models for the entire system:

class EnrichedField(BaseModel):
    """Every enriched data point carries this metadata."""
    value: str | float | None = None
    unit: str | None = None  # "cm", "kg", "L", "mm", etc.
    source_url: str | None = None
    confidence: Literal["official", "third_party", "inferred", "not_found"] = "not_found"
    dimension_type: Literal["product", "packaging", "na"] = "na"
    notes: str | None = None

class ProductClassification(BaseModel):
    product_type: Literal["standard_product", "accessory", "liquid", "soft_good", "electronics", "other"]
    brand: str | None = None
    brand_confidence: Literal["certain", "likely", "unknown"] = "unknown"
    model_number: str | None = None
    parsed_color: str | None = None
    parsed_size: str | None = None
    reasoning: str

class StandardProduct(BaseModel):
    height: EnrichedField = EnrichedField()
    length: EnrichedField = EnrichedField()
    width: EnrichedField = EnrichedField()
    weight: EnrichedField = EnrichedField()
    color: EnrichedField = EnrichedField()
    country_of_origin: EnrichedField = EnrichedField()
    image_url: EnrichedField = EnrichedField()

class AccessoryProduct(BaseModel):
    diameter: EnrichedField = EnrichedField()
    arbor_size: EnrichedField | None = None
    thickness: EnrichedField | None = None
    weight: EnrichedField = EnrichedField()
    color: EnrichedField = EnrichedField()
    country_of_origin: EnrichedField = EnrichedField()
    image_url: EnrichedField = EnrichedField()

class LiquidProduct(BaseModel):
    volume: EnrichedField = EnrichedField()
    container_height: EnrichedField | None = None
    container_width: EnrichedField | None = None
    container_depth: EnrichedField | None = None
    weight: EnrichedField = EnrichedField()
    color: EnrichedField = EnrichedField()
    country_of_origin: EnrichedField = EnrichedField()
    image_url: EnrichedField = EnrichedField()

STEP 2: Create backend/utils/llm.py — a simple module that initializes the Claude client.

Use the Anthropic Python SDK directly (pip install anthropic). Not LangChain — we'll keep it simple for validation.

import anthropic
client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

Add a helper function that calls Claude with a Pydantic schema and returns a validated instance:

def classify_with_schema(prompt: str, system: str, schema: type[BaseModel]) -> BaseModel:
    """
    Calls Claude, asks it to respond in JSON matching the schema,
    parses the response, and returns a validated Pydantic instance.
    """
    # Use claude-sonnet-4-5-20250929
    # In the system prompt, include the JSON schema from schema.model_json_schema()
    # Tell Claude to respond ONLY with valid JSON, no markdown, no explanation
    # Parse the response with schema.model_validate_json()
    # If parsing fails, retry once with a corrective prompt
    
This is simpler than LangChain's with_structured_output and gives us full control.

STEP 3: Create backend/pipeline/triage.py with a function:

async def classify_product(product_id: int) -> ProductClassification:
    """
    Takes a product ID, loads it from SQLite, sends the product name + EAN + any existing data
    to Claude for classification, and returns a ProductClassification.
    """

The system prompt for the classification LLM call should be:

SYSTEM PROMPT:
```
You are a product classification system for an ecommerce data enrichment pipeline.

Your job: Given a product name (often in Slovenian) and an EAN code, determine:
1. What TYPE of product this is (this controls which data schema we use)
2. The BRAND (if identifiable from the name)
3. The MODEL NUMBER (if identifiable from the name)
4. Any COLOR or SIZE already embedded in the product name

Product types and what they mean:
- standard_product: Has physical dimensions (height, length, width). Examples: power tools, appliances, furniture, boxes, hardware.
- accessory: Small parts defined by diameter, arbor size, or other non-H/L/W measurements. Examples: drill bits, saw blades, wire brushes, grinding discs, sanding pads.
- liquid: Defined by volume. Physical dimensions are of the container, not the product. Examples: motor oil, paint, chemicals, lubricants.
- soft_good: Defined by clothing/textile sizing (S/M/L or numeric). Examples: gloves, safety clothing, protective gear.
- electronics: Has standard dimensions BUT also voltage, wattage, connectivity specs. Examples: chargers, batteries, power banks.
- other: Doesn't fit above categories. Use sparingly.

CRITICAL RULES:
- If the product name contains "fi" or "Ø" followed by a number, it's likely an accessory with a diameter.
- If the product name contains a volume (e.g., "20L", "500ml", "5L"), it's likely a liquid.
- If the brand is not obvious from the name, set brand_confidence to "unknown". Do NOT guess.
- Model numbers often look like alphanumeric codes: "HTZ5800", "D-39914", "DHP481".
- Slovenian product names: "škarje" = scissors/shears, "krtača" = brush, "olje" = oil, "vrtalnik" = drill, "žaga" = saw.

Respond with ONLY valid JSON matching the provided schema. No other text.
```

USER PROMPT:
```
Classify this product:

Product name: {product_name}
EAN: {ean}
Existing brand field: {brand or "empty"}
Existing weight: {weight or "empty"}

Respond with JSON matching this schema:
{ProductClassification.model_json_schema()}
```

STEP 4: Wire up the API

- Update POST /api/products/{id}/enrich to call classify_product() as the first step
- Store the result in products.classification_result as JSON
- Update the product status to "enriching"
- Add a log entry to enrichment_log: {"phase": "triage", "status": "complete", "timestamp": "...", "result_summary": "classified as {product_type}, brand: {brand}"}
- Return the classification result

STEP 5: Update the frontend product detail page

Show the classification result in a card:
- Product Type (with an appropriate icon or label)
- Brand + confidence level
- Model Number
- Parsed Color / Size (if found)
- Reasoning (collapsible, shows the LLM's explanation)
- A "Run Classification" button that triggers the API call

STEP 6: Add a "Classify All" button on the dashboard that loops through all pending products and classifies them one by one, updating the table status in real-time.

Add ANTHROPIC_API_KEY to backend/.env

Requirements to add: anthropic
```

### What you should have after Phase 1:
- Clicking "Run Classification" on a product calls Claude and classifies it
- The Texas HTZ5800 should classify as `standard_product` with brand "Texas"
- The Makita wire brush should classify as `accessory` (and hopefully catch "fi 50" as diameter)
- The Valvoline oil should classify as `liquid` with brand "Valvoline"
- Classification results visible in the UI with confidence badges

---

# PHASE 2: BRAND IDENTIFICATION FALLBACK + SEARCH

## Give this to the AI:

```
I'm building a product data enrichment pipeline. Phase 1 (classification) is done. Now implement Phase 2: brand identification fallback for products where the classifier couldn't determine the brand, then web search to find product pages.

CONTEXT: Some products don't have a brand in their name. The Makita D-39914 wire brush is listed as "Okrogla žičnata krtaca D-39914 fi 50 naštek za drill" — no brand. We need to identify the brand before we can search effectively.

STEP 1: Create backend/utils/ean_lookup.py

Add a function that looks up an EAN code via barcodelookup.com by scraping it with Firecrawl:

async def lookup_ean(ean: str) -> dict | None:
    """
    Scrapes barcodelookup.com/{ean} via Firecrawl.
    Returns {brand: str, product_name: str, category: str} or None.
    """
    # pip install firecrawl-py
    # from firecrawl import FirecrawlApp
    # firecrawl = FirecrawlApp(api_key=FIRECRAWL_API_KEY)
    # result = firecrawl.scrape_url(f"https://www.barcodelookup.com/{ean}", params={"formats": ["markdown"]})
    # Then send the markdown to Claude to extract brand + product info
    # This uses 1 Firecrawl credit + 1 small Claude call

The Claude prompt for extracting brand from barcodelookup page:

SYSTEM PROMPT:
```
Extract product information from this barcodelookup.com page content.
Return ONLY valid JSON with these fields:
- brand: the manufacturer/brand name
- product_name: the full product name
- category: the product category
If the information is not found, set the field to null.
```

STEP 2: Update backend/pipeline/triage.py

After classification, if brand_confidence is "unknown":
1. Call lookup_ean(ean)
2. If brand is found, update the classification_result with the brand and set brand_confidence to "likely"
3. Add a log entry: {"phase": "triage", "step": "ean_lookup", "status": "found", "brand": "Makita", "source": "barcodelookup.com"}

STEP 3: Create backend/pipeline/search.py

async def search_product(product_id: int) -> list[dict]:
    """
    Takes a product ID, loads classification result, constructs search queries,
    runs Tavily search, classifies the results by source type, and returns
    a ranked list of URLs to scrape.
    """

Install tavily-python (pip install tavily-python).

Search strategy — run UP TO 3 searches, stop as soon as we have good results:

Search 1 (primary): "{brand} {model_number} specifications"
Search 2 (if Search 1 has <3 results): "{brand} {model_number} {ean}"
Search 3 (fallback, EAN only): "{ean}"

For each search, use:
    from tavily import TavilyClient
    tavily = TavilyClient(api_key=TAVILY_API_KEY)
    results = tavily.search(query=query, max_results=7)

After collecting all results, deduplicate by URL, then classify each URL:

Send ALL the search result URLs + titles to Claude in ONE call and ask it to classify them.

SYSTEM PROMPT for URL classification:
```
You are classifying web search results for a product data enrichment pipeline.

For each URL, determine the source_type:
- "manufacturer": This is the brand's own website (e.g., texas-garden.com for Texas brand, makita.com for Makita, valvoline.com for Valvoline). Official product pages, spec sheets, or catalogs from the manufacturer.
- "authorized_distributor": Large, reputable tool/product distributors. Examples: agrieuro.com, toolnation.com, contorion.com, amazon.com (if sold by brand), grainger.com.
- "third_party": Smaller retailers, comparison sites, forums, review sites. Still potentially useful but lower trust.
- "irrelevant": Not related to the product, wrong product, spam, or aggregator sites with no real data.

CRITICAL: If you're not sure whether a site is the manufacturer, check if the domain name relates to the brand name. texas-garden.com = manufacturer for Texas brand. But amazon.com listing a Texas product = authorized_distributor.

Return a JSON array of objects: [{url, title, source_type, reasoning}]
Sort by priority: manufacturer first, then authorized_distributor, then third_party. Exclude irrelevant.
Limit to top 5 URLs.
```

USER PROMPT:
```
Product: {brand} {model_number} (EAN: {ean})
Product type: {product_type}

Search results to classify:
{for each result: "- {url} | {title}"}

Return JSON array sorted by source priority. Top 5 only.
```

STEP 4: Store search results

Save the classified URL list to products.search_result as JSON.
Add log entries for each search query run and the final URL list.

STEP 5: Wire up the API

Add POST /api/products/{id}/search endpoint that:
1. Checks classification_result exists (if not, run triage first)
2. If brand_confidence is "unknown", runs EAN lookup first
3. Runs the search
4. Returns the classified URL list

STEP 6: Update the frontend product detail page

Add a "Search" section below Classification that shows:
- The search queries used (collapsible)
- The classified URL list as a table: URL (clickable link), title, source_type (with color: green=manufacturer, blue=distributor, gray=third_party)
- A "Run Search" button

STEP 7: Update the enrichment flow

POST /api/products/{id}/enrich should now run triage → search in sequence.
Update status to "enriching" at start, keep it there until all phases complete.

Add TAVILY_API_KEY and FIRECRAWL_API_KEY to backend/.env
Add to requirements.txt: tavily-python, firecrawl-py
```

### What you should have after Phase 2:
- Makita wire brush: EAN lookup finds "Makita" as brand, search finds distributor pages
- Texas HTZ5800: search finds texas-garden.com (manufacturer) + distributors
- Valvoline: search finds valvoline.com product page
- URL classifications visible in the UI with source type badges

---

# PHASE 3: SCRAPE + EXTRACT

## Give this to the AI:

```
I'm building a product data enrichment pipeline. Phase 2 (search) is done — we have a ranked list of URLs for each product. Now implement Phase 3: scrape those pages and extract structured product data.

CONTEXT: We have URLs classified as manufacturer/distributor/third_party. We need to scrape each page with Firecrawl, then use Claude to extract product specifications into our Pydantic schema. Different product types use different schemas (StandardProduct for tools, AccessoryProduct for brushes/blades, LiquidProduct for oils).

STEP 1: Create backend/pipeline/extract.py

async def extract_product_data(product_id: int) -> dict:
    """
    Takes a product ID, loads search results (URL list), scrapes top URLs
    with Firecrawl, extracts product data with Claude, merges results from
    multiple sources using survivorship rules.
    """

The extraction flow for each URL:

1. Scrape with Firecrawl:
    from firecrawl import FirecrawlApp
    firecrawl = FirecrawlApp(api_key=FIRECRAWL_API_KEY)
    scraped = firecrawl.scrape_url(url, params={"formats": ["markdown"]})
    page_content = scraped.get("markdown", "")

2. Send page content + the correct Pydantic schema to Claude for extraction.

CRITICAL: Select the schema based on the product_type from classification:
    - standard_product → StandardProduct schema
    - accessory → AccessoryProduct schema  
    - liquid → LiquidProduct schema
    - For other types, use StandardProduct as default

3. The extraction Claude call is the most important prompt in the entire system. Get this right:

SYSTEM PROMPT for extraction:
```
You are extracting product specifications from a web page for a data enrichment pipeline.

You will receive:
- The product identity (brand, model number, EAN)
- The source URL and its type (manufacturer, distributor, third_party)
- The page content as markdown
- A JSON schema describing the exact fields to extract

YOUR JOB: Find the values for each field in the schema from the page content.

CRITICAL RULES:

1. DIMENSIONS — PRODUCT vs PACKAGING:
   Look carefully at whether dimensions are for the PRODUCT ITSELF or for the SHIPPING BOX / PACKAGING.
   Clues for packaging: "package dimensions", "shipping dimensions", "box size", "karton", "embalaža", "Verpackung".
   Clues for product: "product dimensions", "tool dimensions", "net dimensions", specifications table without packaging mention.
   Set dimension_type to "product" or "packaging" accordingly. If unclear, set to "packaging" (safer assumption).

2. CONFIDENCE:
   - If source_type is "manufacturer": set confidence to "official"
   - If source_type is "authorized_distributor": set confidence to "third_party"
   - If source_type is "third_party": set confidence to "third_party"
   - If you're INFERRING a value (not directly stated): set confidence to "inferred"

3. UNITS:
   Always record the ORIGINAL unit from the page in the "unit" field (e.g., "mm", "cm", "inches", "kg", "lbs", "L").
   Do NOT convert units. We normalize later.

4. DO NOT HALLUCINATE:
   If a value is NOT on the page, leave it as null/not_found. Do NOT make up values.
   Do NOT copy values from the product name unless they match what's on the page.
   If the page mentions a value but it's ambiguous, set notes explaining the ambiguity.

5. SOURCE URL:
   Set source_url to the provided URL for every field you extract from this page.

6. IMAGES:
   If you see image URLs on the page, extract the highest-resolution product image URL for image_url.

Respond with ONLY valid JSON matching the provided schema. No markdown fences. No explanation.
```

USER PROMPT:
```
Extract product data from this page.

Product: {brand} {model_number}
EAN: {ean}
Source URL: {url}
Source type: {source_type}

Respond with JSON matching this schema:
{selected_schema.model_json_schema()}

Page content (truncated to fit):
{page_content[:10000]}
```

STEP 2: Multi-source merging (survivorship rules)

After extracting from ALL URLs (up to 5), merge the results. For each field in the schema:

def merge_field(extractions: list[EnrichedField]) -> EnrichedField:
    """
    Given multiple extractions of the same field from different sources,
    pick the winner.
    """
    # Filter out not_found values
    # If any value is from a manufacturer (confidence="official"), use it
    # If multiple third_party sources agree on the same value, use it with confidence="third_party"
    # If only one source has the value, use it but keep confidence as-is
    # If sources disagree and no manufacturer source, flag with notes="sources disagree: {values}"

Implement this as a simple Python function, not an LLM call. This is deterministic logic.

STEP 3: Country of origin (specialized search)

After the main extraction, if country_of_origin is still not_found, run a SEPARATE Tavily search:

query = f"{brand} country of origin manufacturing"

Then send the search results (just the content snippets, no need to scrape full pages) to Claude:

SYSTEM PROMPT:
```
Determine the country of origin (manufacturing country) for this product based on the search results.

RULES:
- If the search results explicitly state where this specific product is manufactured, report that.
- If only the brand's general manufacturing country is mentioned, report that with notes="inferred from brand, not product-specific".
- Common patterns: many power tool brands manufacture in China. Valvoline has plants in multiple countries.
- Set confidence to "third_party" if from a reliable source, "inferred" if you're guessing from brand info.
- If you truly cannot determine, return not_found.

Return JSON: {"value": "country name or null", "source_url": "url or null", "confidence": "...", "notes": "..."}
```

STEP 4: Color determination

If color is still not_found after extraction, AND there are product image URLs:
- Send ONE image URL to Claude (claude-sonnet-4-5-20250929 supports image URLs via the messages API)

SYSTEM PROMPT:
```
What is the primary color of this product? 
Return JSON: {"value": "color name", "confidence": "inferred", "notes": "determined from product image"}
If the product is metallic/silver (like a wire brush or metal tool), say "silver/metallic".
If the product is a liquid in a container, describe the container color.
Return ONLY the JSON.
```

Send the image as a URL in a content block with type "image" (use the Anthropic messages API image_url format).

If no image URLs were found, check if the product name contains color words (common Slovenian: črna=black, bela=white, rdeča=red, modra=blue, zelena=green, rumena=yellow, oranžna=orange). Extract with confidence "inferred".

STEP 5: Store extraction results

Save the merged extraction result to products.extraction_result as JSON.
Add detailed log entries: which URLs were scraped, what was extracted from each, how merging resolved conflicts.

STEP 6: Wire up the API

POST /api/products/{id}/extract endpoint:
1. Check search_result exists
2. Scrape top URLs (max 5) with Firecrawl
3. Extract from each with Claude
4. Merge results
5. Run country of origin lookup if needed
6. Run color determination if needed
7. Return merged extraction result

STEP 7: Update frontend product detail page

Add an "Extraction" section showing:
- The final merged result as a clean table: Field | Value | Unit | Source | Confidence | Dim Type
- Confidence as colored badges (green=official, yellow=third_party, orange=inferred, red=not_found)
- Dimension type flag (if applicable) shown as a small label: "📦 Packaging" or "📏 Product"
- A collapsible section showing per-source extractions (what each URL contributed)
- "Run Extraction" button

STEP 8: Update POST /api/products/{id}/enrich to run triage → search → extract in sequence.

Handle errors gracefully:
- If Firecrawl fails on a URL: log the error, skip to next URL
- If Claude returns invalid JSON: retry once, then log error and skip that URL
- If ALL URLs fail: set extraction_result to empty schema with all fields as not_found
```

### What you should have after Phase 3:
- Texas HTZ5800: dimensions extracted from manufacturer page, flagged as packaging, weight found, color inferred from image
- Makita D-39914: diameter (50mm) extracted from distributor pages, weight found if available, color = silver/metallic
- Valvoline: volume (20L) extracted, container dimensions from distributor, weight estimated
- Per-field source URLs and confidence visible in the UI

---

# PHASE 4: VALIDATION + NORMALIZATION + OUTPUT

## Give this to the AI:

```
I'm building a product data enrichment pipeline. Phase 3 (extraction) is done — we have structured product data with sources and confidence scores. Now implement Phase 4: normalize units, run sanity checks, and produce the final output.

STEP 1: Create backend/utils/normalization.py

Pure Python functions, NO LLM calls:

def normalize_to_cm(value: float, unit: str) -> float:
    """Convert any length to centimeters."""
    conversions = {"mm": 0.1, "cm": 1.0, "m": 100.0, "in": 2.54, "inches": 2.54, "inch": 2.54, "ft": 30.48}
    return round(value * conversions.get(unit.lower().strip(), 1.0), 2)

def normalize_to_kg(value: float, unit: str) -> float:
    """Convert any weight to kilograms."""
    conversions = {"g": 0.001, "kg": 1.0, "lb": 0.4536, "lbs": 0.4536, "oz": 0.02835}
    return round(value * conversions.get(unit.lower().strip(), 1.0), 3)

def normalize_to_liters(value: float, unit: str) -> float:
    """Convert any volume to liters."""
    conversions = {"ml": 0.001, "cl": 0.01, "dl": 0.1, "l": 1.0, "gal": 3.785, "qt": 0.9464, "fl_oz": 0.02957}
    return round(value * conversions.get(unit.lower().strip(), 1.0), 3)

def normalize_field(field: EnrichedField, target_unit: str) -> EnrichedField:
    """
    Takes an EnrichedField with a value and unit, normalizes to target unit.
    Returns a new EnrichedField with normalized value and updated unit.
    Keeps original value in notes.
    """

STEP 2: Create backend/pipeline/validate.py

async def validate_product(product_id: int) -> dict:
    """
    Takes a product ID, loads extraction result, normalizes all units,
    runs a sanity check with Claude, assigns final confidence scores,
    and produces the final validation_result.
    """

Sub-steps:

2a. Normalize all units:
    - All length/dimension fields → cm
    - All weight fields → kg
    - All volume fields → L
    - Store original value + unit in the notes field

2b. Sanity check via Claude (ONE call for the entire product):

SYSTEM PROMPT:
```
You are a data quality checker for enriched product data.

Review the extracted and normalized product data below. Check for:

1. PLAUSIBILITY: Does the weight make sense for this product type? A wire brush should be <0.5 kg. A 20L oil canister should be ~18 kg. A hedge trimmer should be 2-6 kg.

2. DIMENSION CONSISTENCY: If height, length, and width are all provided, do they form a plausible shape? Is any dimension suspiciously large or small?

3. DATA CONFLICTS: Does any extracted value contradict the original product name? (e.g., product name says "20L" but volume extracted as "5L")

4. MISSING CRITICAL DATA: For the product type, which fields SHOULD have data but don't?

Return JSON:
{
    "overall_quality": "good" | "acceptable" | "needs_review",
    "issues": [
        {"field": "weight", "issue": "description", "severity": "warning" | "error"}
    ],
    "review_reason": "null or explanation of why human review is needed"
}
```

USER PROMPT:
```
Product: {brand} {model_number} ({product_type})
Original name: {product_name}
EAN: {ean}

Normalized extracted data:
{extraction_result as formatted JSON}

Check this data for quality issues.
```

2c. Build the final validation result: the extraction_result with normalized values + the sanity check results + a review_flag (true if overall_quality is "needs_review" or any issue has severity "error").

STEP 3: Store validation results

Save to products.validation_result as JSON.
Update status to "done" (or "needs_review" if flagged).
Add log entries.

STEP 4: Wire up the API

POST /api/products/{id}/validate endpoint.
Update POST /api/products/{id}/enrich to run the full pipeline: triage → search → extract → validate.

Also add:
GET /api/products/{id}/export — returns the enriched data as a downloadable JSON or CSV row.
GET /api/export — exports ALL products as a single CSV/XLSX with columns:
    EAN, Product Name, Brand, Product Type, 
    Height (cm), Height Source, Height Confidence, Height Dim Type,
    Length (cm), Length Source, Length Confidence, Length Dim Type,
    Width (cm), Width Source, Width Confidence, Width Dim Type,
    Weight (kg), Weight Source, Weight Confidence,
    Color, Color Source, Color Confidence,
    Country of Origin, CoO Source, CoO Confidence,
    Image URL, Image Source,
    Review Flag, Review Reason

Use pandas + openpyxl to generate the XLSX.

STEP 5: Update frontend product detail page

Add a "Validation" section showing:
- Final enriched data table with normalized values
- Sanity check results (issues as warning/error badges)
- Review flag (big banner if the product needs human review, with the reason)
- "Run Full Pipeline" button that triggers the entire enrichment sequence
- "Export" button that downloads the product data as CSV

STEP 6: Update the dashboard page

- Add a "Enrich All" button that processes all products sequentially
- Add progress indicator (X of Y products processed)
- Add summary stats at the top: total products, enriched count, needs review count, average confidence
- Add an "Export All" button that downloads the full XLSX

STEP 7: Add a comparison view for validation

Since we manually researched 3 products, add a way to compare:

Add a section to the product detail page: "Manual vs Automated" comparison.
Create a simple JSON file at backend/manual_results.json with the manual research data for the 3 test products (keyed by EAN). The comparison shows each field side by side:

| Field | Manual Value | Automated Value | Match? |
|-------|-------------|----------------|--------|
| Brand | Makita | Makita | ✅ |
| Dimensions | Ø 50mm | Ø 50mm | ✅ |
| Weight | ~0.05 kg | 0.06 kg | ⚠️ (~) |
| ...

This is how we prove the pipeline works for the Shoppster proposal.
```

### What you should have after Phase 4:
- Full pipeline running end-to-end for all 3 products
- Normalized data with confidence scores
- Sanity checks catching implausible values
- Export to XLSX matching Shoppster's expected format
- Comparison view showing automated vs. manual research results
- Dashboard with summary stats and bulk operations

---

# PHASE 5: POLISH + ENRICHMENT LOG + PIPELINE VISIBILITY

## Give this to the AI:

```
I'm building a product data enrichment pipeline. The core pipeline (Phases 0-4) is done and working. Now add polish: a detailed enrichment log timeline, better error handling, and retry capability.

STEP 1: Enrichment Log Timeline

The enrichment_log column stores a JSON array of log entries. Each entry has:
{
    "timestamp": "ISO datetime",
    "phase": "triage" | "search" | "extract" | "validate",
    "step": "description of what happened",
    "status": "success" | "warning" | "error",
    "details": "optional longer description",
    "credits_used": {"tavily": 0, "firecrawl": 0, "claude_tokens": 0}
}

Update ALL pipeline functions to write detailed log entries at every step:
- triage: "Classified as {type}", "Brand identified as {brand} via EAN lookup", "Brand not found"
- search: "Search query: '{query}' returned {n} results", "Classified {n} URLs: {n} manufacturer, {n} distributor"
- extract: "Scraped {url} — {status}", "Extracted {n} fields from {url}", "Merged {n} sources", "Country of origin search: {result}", "Color from image: {result}"
- validate: "Normalized {n} fields", "Sanity check: {quality}", "Flagged for review: {reason}"

STEP 2: Enrichment Log UI

On the product detail page, add an "Enrichment Log" section at the bottom. Display as a timeline:

- Each entry is a row with: timestamp (relative, like "2 min ago"), phase badge (colored), step text, status icon (✓/⚠/✗)
- Clicking an entry expands to show the full details
- Color-code by phase: triage=purple, search=blue, extract=green, validate=orange
- Show total credits used at the bottom: "This enrichment used: 3 Tavily searches, 4 Firecrawl scrapes, ~2,400 Claude tokens"

STEP 3: Retry capability

Add a "Retry" button next to each phase section in the product detail view. Clicking it re-runs ONLY that phase (and all subsequent phases, since they depend on it). For example:
- Retry Search: clears search_result, extraction_result, validation_result and reruns from search onward
- Retry Extract: clears extraction_result, validation_result and reruns from extraction onward

STEP 4: Better error handling

In each pipeline step:
- Wrap Firecrawl calls in try/except. On failure: log the error with the URL, wait 3 seconds, retry once. If still fails, log and skip.
- Wrap Claude calls in try/except. On JSON parse failure: retry once with a corrective prompt "Your previous response was not valid JSON. Please respond with ONLY valid JSON matching the schema." If still fails, log the raw response and mark that step as error.
- Wrap Tavily calls in try/except. On failure: log and try the fallback query. If all queries fail, set search_result to empty list and log.

STEP 5: Status improvements

Update the product status to be more granular:
- "pending" — not started
- "classifying" — running triage
- "searching" — running search  
- "extracting" — running extraction
- "validating" — running validation
- "done" — complete, no issues
- "needs_review" — complete but flagged
- "error" — pipeline failed at some step

Show these in the ProductTable with appropriate colors and icons.

STEP 6: Dashboard summary cards

At the top of the dashboard, show 4 cards:
- Total Products (number)
- Enriched (number, green)  
- Needs Review (number, yellow)
- Errors (number, red)

Below the cards, show a simple bar chart or stat:
- Average confidence breakdown: X% official, Y% third_party, Z% inferred, W% not_found
(This can be a simple colored horizontal bar, no need for a charting library)
```

### What you should have after Phase 5:
- Complete, polished validation tool
- Detailed enrichment logs showing exactly what happened at each step
- Retry capability for failed or unsatisfactory phases
- Clear status tracking throughout the pipeline
- Dashboard with at-a-glance summary stats

---

# REFERENCE: LLM CALL SUMMARY

All the LLM calls in the system, in one place:

| Call | Phase | Purpose | Input | Output Schema | Model |
|------|-------|---------|-------|--------------|-------|
| classify_product | Triage | Determine product type, brand, model | Product name + EAN | ProductClassification | claude-sonnet |
| extract_brand_from_ean | Triage | Extract brand from barcodelookup page | Scraped markdown | {brand, product_name, category} | claude-sonnet |
| classify_urls | Search | Rank URLs by source type | URL list + titles | [{url, source_type, reasoning}] | claude-sonnet |
| extract_product_data | Extract | Pull specs from scraped page | Page markdown + schema | StandardProduct / AccessoryProduct / LiquidProduct | claude-sonnet |
| determine_country | Extract | Find country of origin | Tavily search snippets | EnrichedField | claude-sonnet |
| determine_color | Extract | Identify color from image | Product image URL | EnrichedField | claude-sonnet |
| sanity_check | Validate | Check data plausibility | Full extracted data | {overall_quality, issues, review_reason} | claude-sonnet |

**Total per product:** 5–8 Claude calls depending on fallbacks needed.

**Token estimates per product:** ~3,000–6,000 input tokens, ~500–1,000 output tokens across all calls. At claude-sonnet-4-5-20250929 pricing this is roughly $0.02–0.05 per product.

---

# REFERENCE: API ENDPOINTS SUMMARY

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | /api/upload | Upload CSV/XLSX, parse and store products |
| GET | /api/products | List all products with status |
| GET | /api/products/{id} | Full product detail with all enrichment data |
| POST | /api/products/{id}/enrich | Run full pipeline (triage → search → extract → validate) |
| POST | /api/products/{id}/classify | Run triage only |
| POST | /api/products/{id}/search | Run search only |
| POST | /api/products/{id}/extract | Run extraction only |
| POST | /api/products/{id}/validate | Run validation only |
| GET | /api/products/{id}/export | Export single product as JSON |
| GET | /api/export | Export all products as XLSX |
| POST | /api/enrich-all | Run full pipeline on all pending products |

---

# REFERENCE: ENVIRONMENT VARIABLES

```
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxx
TAVILY_API_KEY=tvly-xxxxxxxxxxxxx
FIRECRAWL_API_KEY=fc-xxxxxxxxxxxxx
```

---

# REFERENCE: PYTHON DEPENDENCIES

```
fastapi
uvicorn
pandas
openpyxl
python-dotenv
pydantic
anthropic
tavily-python
firecrawl-py
```
