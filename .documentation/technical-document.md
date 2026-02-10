# TECHNICAL ARCHITECTURE
## Product Data Enrichment Engine

**Version:** 1.0 // February 2026
**Classification:** Internal / WebFast

---

## 1. Technology Stack Overview

Every component in the stack is either open-source or a pay-per-use API. No SaaS subscriptions lock us into vendor platforms. No proprietary workflow tools that Shoppster could license themselves.

| Component | Technology | Role |
|-----------|-----------|------|
| Language | Python 3.11+ | All pipeline code, schemas, orchestration |
| Orchestration | LangGraph (MIT license) | State machine for pipeline flow, branching, retries |
| Web Search | Tavily API | Find product pages from brand/model/EAN queries |
| Web Scraping | Firecrawl API | Scrape pages, extract structured data, handle PDFs |
| Reasoning LLM | Claude (via Vertex AI) | Classification, extraction, validation, confidence scoring |
| Vision LLM | Gemini (via Vertex AI) | Image analysis for color determination |
| EAN Lookup | EAN-Search.org API | Brand identification fallback for unknown products |
| EAN Lookup #2 | barcodelookup.com | Supplementary product info (scraped via Firecrawl) |
| Schema Validation | Pydantic v2 | Type-safe product schemas, LLM output validation |
| Data Processing | pandas + openpyxl | CSV/XLSX input parsing and output generation |
| Prototyping | n8n (self-hosted) | Visual workflow testing before production build |
| Observability | LangSmith (optional) | Token tracking, trace debugging, cost monitoring |

---

## 2. Technology Justifications

### 2.1 LangGraph — Pipeline Orchestration

LangGraph is LangChain's graph-based agent orchestration framework, now the recommended approach for all production AI agent systems. It models workflows as **state machines** where each node is a processing step and edges define flow + branching logic.

Why LangGraph over alternatives:

**It has a data-enrichment template.** LangChain maintains an official open-source template that implements exactly our pattern: takes a research topic, does web research, returns structured JSON via Pydantic schemas. We adapt this to product enrichment.

- Template repo: https://github.com/langchain-ai/data-enrichment

**State persistence.** Each product's enrichment state is maintained across phases. If a scrape fails in Phase 3, the system can retry or branch to fallback sources without losing data collected in Phases 1–2.

**Conditional branching.** Easy products (manufacturer page found, all data extracted) skip directly to validation. Hard products (no brand, no manufacturer page) branch into fallback strategies. This non-linear flow maps naturally to LangGraph's graph model.

**Structured output enforcement.** Native integration with Pydantic via `llm.with_structured_output()`. Every LLM call returns validated, typed data — not free-text that needs post-processing.

**Parallel processing.** LangGraph's `Send` API can spin up parallel workers for batch processing multiple products simultaneously, each with isolated state.

Key documentation:
- LangGraph overview: https://www.langchain.com/langgraph
- Workflows & agents guide: https://docs.langchain.com/oss/python/langgraph/workflows-agents
- Data enrichment template: https://github.com/langchain-ai/data-enrichment

### 2.2 Firecrawl — Web Scraping

Firecrawl is the primary data acquisition tool. It handles the hardest part of web research: getting clean, structured data out of arbitrary web pages.

**Scrape mode:** Takes a URL, returns clean markdown or HTML. Handles JavaScript rendering, anti-bot protection, CAPTCHAs. One page = one credit. No per-site configuration needed.

**Extract mode:** Takes a URL + Pydantic schema, returns structured JSON matching the schema. This is the key feature — instead of scraping raw HTML and then parsing it with an LLM, Firecrawl does extraction in one step. Reduces token cost and latency.

**PDF support:** Can extract text from PDF spec sheets and product documentation, which is critical for products where specs are in downloadable PDFs rather than on the product page.

**Search endpoint:** Firecrawl also has its own search API that can be used as a backup to Tavily or for site-specific searches.

Key documentation:
- Firecrawl docs: https://docs.firecrawl.dev
- Python SDK: https://docs.firecrawl.dev/sdks/python
- Extract mode: https://docs.firecrawl.dev/features/extract
- Pricing: Free 500 credits, then $19/mo for 3,000 credits — https://www.firecrawl.dev/pricing

### 2.3 Tavily — Web Search for AI Agents

Tavily is a search API built specifically for LLM-powered systems. Unlike Google Custom Search or Bing API, Tavily returns pre-processed, LLM-ready content with relevance scoring and source attribution.

**Why not just Google Search API?** Google Custom Search returns raw URLs and snippets that require additional processing. Tavily returns aggregated content from up to 20 sources per query, ranked by AI relevance. This means our LLM receives higher-quality context with less token waste.

**LangGraph integration:** Tavily is a first-class tool in the LangChain/LangGraph ecosystem. The data-enrichment template uses Tavily as its default search provider. Drop-in integration, no custom wrappers needed.

**Free tier:** 1,000 credits per month (1 credit = 1 basic search). Sufficient for validation phase and early production. Paid tier: $50/month for 15,000 credits.

Key documentation:
- Tavily docs: https://docs.tavily.com
- Python SDK: https://docs.tavily.com/documentation/python/tavily-search/getting-started
- Pricing/credits: https://docs.tavily.com/documentation/api-credits

### 2.4 Claude API (via Google Vertex AI)

Claude serves as the reasoning engine throughout the pipeline. It handles tasks that require understanding, judgment, and structured output generation — not just pattern matching.

Specific roles in the pipeline:

**Phase 1 — Product name parsing:** Extracts embedded information from Slovenian product names. Example: "TEXAS HTZ5800 Akumulatorske škarje za živo mejo" → brand: Texas, model: HTZ5800, type: hedge trimmer.

**Phase 1 — Product classification:** Determines product type (standard, accessory, liquid, soft good, etc.) which controls which Pydantic schema is applied.

**Phase 3 — Structured extraction:** Takes scraped markdown from Firecrawl and extracts product attributes into the Pydantic schema. Handles edge cases: distinguishing product vs. packaging dimensions, parsing specification tables in various formats, handling multilingual content.

**Phase 4 — Validation:** Cross-references extracted data against original product record. Flags inconsistencies (e.g., extracted weight is 50kg for a wire brush — likely wrong). Assigns confidence tiers based on source type.

Key documentation:
- Claude on Vertex AI: https://cloud.google.com/vertex-ai/generative-ai/docs/partner-models/use-claude
- LangChain Anthropic integration: https://python.langchain.com/docs/integrations/chat/anthropic/

### 2.5 Gemini API (via Google Vertex AI)

Gemini handles **multimodal tasks** — specifically, analyzing product images to determine color when it's not listed in text specifications. We pass the product image URL and ask Gemini to identify the primary product color. Gemini is chosen over Claude for this specific task because of cost efficiency on image tokens.

Key documentation:
- Gemini on Vertex AI: https://cloud.google.com/vertex-ai/generative-ai/docs/multimodal/overview
- LangChain Google GenAI: https://python.langchain.com/docs/integrations/chat/google_generative_ai/

### 2.6 Pydantic v2 — Schema Enforcement

Pydantic is the schema backbone of the entire system. It serves three critical functions:

**Product-type schemas:** Different Pydantic models for different product types. A `StandardProduct` has height, length, width. An `AccessoryProduct` has diameter, drive_size. A `LiquidProduct` has volume, container_height, container_diameter. The classification step in Phase 1 selects which schema to use.

**LLM output validation:** LangGraph/LangChain's `.with_structured_output(PydanticModel)` forces the LLM to return data conforming to the schema. If the LLM returns invalid data, Pydantic catches it and the system can retry.

**Confidence metadata:** Each enriched field includes source_url, confidence_tier, and dimension_type as part of the Pydantic model. This metadata travels with the data through the entire pipeline.

Example schema structure (simplified):

```python
class EnrichedField(BaseModel):
    value: str | float | None
    source_url: str | None
    confidence: Literal["official", "third_party", "inferred", "not_found"]
    dimension_type: Literal["product", "packaging", "na"] = "na"

class StandardProduct(BaseModel):
    height_cm: EnrichedField
    length_cm: EnrichedField
    width_cm: EnrichedField
    weight_kg: EnrichedField
    color: EnrichedField
    country_of_origin: EnrichedField

class AccessoryProduct(BaseModel):
    diameter_mm: EnrichedField
    arbor_size_mm: EnrichedField | None
    weight_kg: EnrichedField
    color: EnrichedField
    # No H/L/W — not applicable for this product type
```

Key documentation:
- Pydantic v2 models: https://docs.pydantic.dev/latest/concepts/models/
- Pydantic with LangChain structured output: https://python.langchain.com/docs/how_to/structured_output/

### 2.7 EAN-Search.org

EAN-Search.org maintains a database of over 1 billion EAN/UPC codes with associated product information (brand, product name, category). We use it as a **brand identification fallback** when the brand cannot be parsed from the product name. The API returns basic product information from an EAN code, which is often sufficient to identify the manufacturer and construct the search query for Phase 2.

Key documentation:
- API docs: https://www.ean-search.org/ean-database-api.html

Alternative: barcodelookup.com can be scraped via Firecrawl using the URL pattern `https://www.barcodelookup.com/{EAN}` — free when scraped, returns brand, product name, category, and related links.

---

## 3. Pipeline Data Flow

Each product moves through the pipeline as a state object. Below is the detailed data flow for each phase, showing inputs, processing, and outputs.

### 3.1 Phase 1: Triage

| Step | Details |
|------|---------|
| **Input** | Single product row from Shoppster CSV: EAN, product_name, brand (maybe), weight (maybe), any existing data |
| **1.0 Parse Name** | Claude extracts embedded info from product_name: model number, color hints, size hints, product category keywords |
| **1.1 Classify Type** | Claude classifies into: `standard_product` \| `accessory` \| `liquid` \| `soft_good` \| `electronics` \| `other`. Returns the classification + reasoning |
| **1.2 Select Schema** | Based on classification, system selects the appropriate Pydantic schema (StandardProduct, AccessoryProduct, LiquidProduct, etc.) |
| **1.3 Identify Brand** | If brand is empty/unclear: try parsing from product name → if still unknown, query EAN-Search.org → if still unknown, scrape barcodelookup.com/{EAN} via Firecrawl |
| **Output** | Enrichment state object: original data + product_type + selected_schema + identified_brand + parsed_attributes |

### 3.2 Phase 2: Search

| Step | Details |
|------|---------|
| **Input** | Enrichment state from Phase 1 with brand + model + EAN + product type |
| **2.0 Build Query** | Construct search query: `"{brand} {model_number} specifications"` as primary. `"{brand} {model_number} datasheet"` as secondary. EAN-only search as fallback |
| **2.1 Tavily Search** | Execute search via Tavily. Get top 5–10 results with content snippets. AI-ranked for relevance to product specifications |
| **2.2 Classify Results** | Claude evaluates search results: which URLs are manufacturer pages vs. distributor pages vs. irrelevant. Assigns source_type: `manufacturer` \| `authorized_distributor` \| `third_party` \| `irrelevant` |
| **2.3 Priority Sort** | Sort URLs by source priority: manufacturer first, then authorized distributors, then others. Limit to top 3–5 URLs to scrape |
| **Output** | Ordered list of URLs to scrape with source_type classification |

### 3.3 Phase 3: Extract

| Step | Details |
|------|---------|
| **Input** | Ordered URL list from Phase 2 + selected Pydantic schema from Phase 1 |
| **3.0 Scrape Pages** | Firecrawl scrapes each URL. Returns markdown content. If Extract mode is available for the schema, use it directly |
| **3.1 LLM Extraction** | Claude takes scraped markdown + Pydantic schema and extracts structured data. Instructed to: flag product vs. packaging dimensions, note the exact source location on the page, skip fields where data is ambiguous |
| **3.2 Multi-Source Merge** | If multiple sources scraped, apply survivorship rules: manufacturer value wins; if no manufacturer, value appearing in 2+ sources wins; single-source value accepted with lower confidence |
| **3.3 Image Analysis** | If color not found in text: pass product image URL to Gemini for visual color identification. Confidence: `inferred` |
| **3.4 Specialized Lookups** | Country of origin: dedicated search `"{brand} manufacturing country"` or `"{brand} country of origin"`. Always flagged as lower confidence |
| **Output** | Partially or fully populated Pydantic model with source URLs and raw confidence data |

### 3.4 Phase 4: Validate

| Step | Details |
|------|---------|
| **Input** | Populated Pydantic model from Phase 3 + original product data |
| **4.0 Normalize Units** | Python code (not LLM): convert all dimensions to cm, all weights to kg, all volumes to L. Handle: mm→cm, inches→cm, lbs→kg, oz→kg, ml→L, gallons→L |
| **4.1 Sanity Check** | Claude cross-references: does the weight make sense for this product type? Are dimensions in a plausible range? Does the extracted brand match the EAN brand? |
| **4.2 Confidence Scoring** | Assign final confidence tiers: `official` (manufacturer source), `third_party` (distributor, single source), `inferred` (visual analysis, pattern matching), `not_found` (no data) |
| **4.3 Flag Exceptions** | Flag products for human review when: brand could not be confirmed, sources disagree on a value, dimensions might be packaging not product, confidence is all inferred/not_found, data seems implausible |
| **Output** | Validated Pydantic model with confidence scores, flags, and normalized values |

### 3.5 Phase 5: Output

| Step | Details |
|------|---------|
| **Input** | Validated Pydantic model for each product |
| **5.0 Merge** | Combine original Shoppster data with enriched data. Original values preserved; enriched values added in new columns |
| **5.1 Format** | Generate output XLSX: original columns + per-attribute enriched columns + source_url columns + confidence columns + dimension_type column + review_flag column |
| **5.2 Report** | Generate summary: total products processed, fill rate per attribute, confidence distribution, list of flagged products with reasons |
| **Output** | Enriched XLSX + enrichment report |

---

## 4. Survivorship Rules

When multiple sources provide data for the same attribute, the following rules determine which value is used:

| Rule | Logic |
|------|-------|
| **Source hierarchy** | Manufacturer official page > authorized distributor > third-party retailer > inferred/calculated. Higher-priority source always wins |
| **Multi-source agreement** | If no manufacturer source exists, a value appearing in 2 or more distributor/retailer sources is accepted with `third_party` confidence |
| **Single-source acceptance** | A value from a single non-manufacturer source is accepted but flagged with `third_party` confidence and a note |
| **Conflict resolution** | If two sources of equal priority disagree, the product is flagged for human review. No automatic resolution of contradictions |
| **Dimension type priority** | Product dimensions preferred over packaging dimensions. If only packaging dimensions available, accepted but flagged as `dimension_type: packaging` |
| **Country of origin** | Manufacturer "About" page > product documentation > distributor listing > inferred from brand HQ. Always lower confidence unless from official source |
| **Weight normalization** | If existing weight in Shoppster data matches manufacturer data: confirmed (`official`). If different: manufacturer value wins, discrepancy noted |

---

## 5. Error Handling and Edge Cases

### 5.1 Scrape Failures

If Firecrawl fails to scrape a URL (timeout, 403, anti-bot block): retry once after 5 seconds. If retry fails, skip that URL and move to next source in the priority list. If all URLs fail, flag the product for manual review with the list of attempted URLs.

### 5.2 LLM Extraction Failures

If Claude returns data that does not validate against the Pydantic schema: retry with a more explicit prompt. If retry fails, record which fields could not be extracted and flag for review. Never force invalid data into the schema.

### 5.3 No Search Results

If Tavily returns no relevant results for the initial query: construct fallback queries (EAN-only search, product name without brand, broader category search). If all queries fail, check barcodelookup.com for the EAN. If still nothing, flag as `not_found` for all attributes.

### 5.4 Brand Cannot Be Identified

If the brand cannot be determined from the product name, EAN-Search.org, or barcodelookup.com: flag the product. Without a brand, the search quality degrades significantly. These products will likely need manual brand identification before automated enrichment can proceed.

### 5.5 Product Page is PDF-Only

Some manufacturers provide specifications only as downloadable PDF catalogs. Firecrawl supports PDF extraction. If the search results point to a PDF: scrape it via Firecrawl, then use Claude to extract structured data from the PDF text. Same Pydantic schema, same confidence scoring.

---

## 6. Cost Model

Estimated per-product cost based on the enrichment pipeline:

| Component | Usage per Product | Est. Cost |
|-----------|------------------|-----------|
| Tavily | 2–4 search queries | €0.01–0.02 |
| Firecrawl | 3–5 page scrapes | €0.02–0.04 |
| Claude (Vertex) | 4–6 LLM calls (classify, extract, validate) | €0.03–0.08 |
| Gemini (Vertex) | 0–1 image analysis call | €0.00–0.01 |
| EAN-Search | 0–1 lookup (only if brand unknown) | €0.00–0.01 |
| **Total per product** | **Typical enrichment** | **€0.06–0.16** |

For a batch of 1,000 products, estimated infrastructure cost: **€60–160**. This excludes WebFast's service fee and setup time. Monthly platform subscriptions (Firecrawl $19, Tavily $50) cover the volume for batches up to approximately 3,000–5,000 products per month.

---

## 7. Security and Data Handling

Product data from Shoppster is processed locally on WebFast's infrastructure. The enrichment pipeline runs on a self-hosted server. LLM API calls send product names and scraped web content to Claude/Gemini via Google Vertex AI (covered under Google Cloud's data processing agreements). No Shoppster data is stored in any third-party SaaS platform. Firecrawl and Tavily receive only search queries and URLs, not Shoppster's internal product data.

Output files are delivered to Shoppster and retained by WebFast only for the agreed retention period.
