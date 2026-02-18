# LLM Cost Optimization Analysis

> **Date:** 2026-02-18
> **Scope:** All Claude Haiku calls across the product enrichment pipeline
> **Goal:** Reduce LLM spend while maintaining extraction quality

---

## Current LLM Call Breakdown Per Product

| Phase | File | Calls | Est. Input Tokens | Est. Output Tokens | Cost Driver |
|-------|------|-------|-------------------|---------------------|-------------|
| Triage | `pipeline/triage.py` | 1 | ~500 | ~300 | Low |
| EAN Lookup | `graph.py` | 0-1 | ~500 | ~200 | Low (conditional) |
| Search classification | `pipeline/search.py` | 1 | ~600 | ~400 | Low |
| **Extract Pass 1** | `pipeline/extract.py` | **1-3** | **~1,500 each** | **~600 each** | **High** |
| **Extract Pass 2** | `pipeline/extract.py` | **1-3** | **~2,000 each** | **~800 each** | **Highest** |
| Validate | `pipeline/validate.py` | 1 | ~1,200 | ~400 | Medium |
| COO gap fill | `pipeline/extract.py` | 0-1 | ~600 | ~200 | Low |
| Color detection | `utils/gemini_vision.py` | 0-1 | ~500 (Gemini) | ~50 (Gemini) | Low |

### Typical Per-Product Totals

- **Minimum LLM calls:** 5 (triage + search + 1 URL x2 passes + validate)
- **Typical LLM calls:** 8-9 (triage + search + 2 URLs x2 passes + validate + COO)
- **Worst case LLM calls:** 12 (triage + EAN + search + 3 URLs x2 passes + validate + COO)
- **Estimated tokens per product:** ~14,500
- **Estimated Claude cost per product:** $0.05-0.08

### Pricing Reference (from `cost_tracker.py`)

| Model | Input $/M tokens | Output $/M tokens |
|-------|-------------------|---------------------|
| Claude Haiku 4.5 | $1.00 | $5.00 |
| Claude Sonnet 4.5 | $3.00 | $15.00 |
| Gemini 3.0 Flash | $0.50 | $3.00 |

---

## The Core Problem

**The extraction phase accounts for ~60-70% of total per-product LLM cost.**

The root cause: the same scraped page content (25-30K characters) is sent to Claude **twice per URL** — once for dimensions (`extract.py:285-349`) and once for content (`extract.py:355-431`).

With up to 3 URLs processed, that's up to 6 LLM calls where the dominant cost is **duplicate input tokens**.

---

## Recommended Optimizations

### 1. Merge Pass 1 + Pass 2 Into a Single Call Per URL

**Impact: 35-45% total cost reduction**
**Risk: Low**
**Quality impact: None**

Currently in `extract.py`:
- Pass 1 sends `markdown[:25000]` to extract dimensions, color, COO, images
- Pass 2 sends `markdown[:30000]` to extract descriptions, features, specs, warranty

**Change:** Create a combined Pydantic schema (e.g., `FullPageExtraction`) that includes all fields from both `DimensionsExtraction` and `ContentExtraction`. Send the page content once with a merged prompt.

**What this saves:**
- ~25K duplicate input tokens per URL (the scraped page content)
- 1-3 LLM calls per product (one fewer call per URL)
- With 3 URLs: saves ~75K input tokens per product

**Implementation steps:**
1. Create `FullPageExtraction` schema combining both existing schemas
2. Merge the two system prompts into one comprehensive extraction prompt
3. Send `markdown[:30000]` once per URL instead of twice
4. Split the single response back into dimensions + content for downstream merge logic
5. Update cost tracking to use a single `extract` phase instead of `extract_pass1`/`extract_pass2`

---

### 2. Enable Anthropic Prompt Caching

**Impact: 5-10% total cost reduction**
**Risk: None**
**Quality impact: None**

The system prompts (including the appended JSON schema from Pydantic models) are identical across all products for a given phase. Anthropic's prompt caching reduces cached token costs by ~90%.

For each extraction call, the system prompt + JSON schema is ~1,500-2,000 tokens. After the first call in a batch, those tokens become essentially free on subsequent calls.

**Implementation:**
- In `utils/llm.py`, add `cache_control: {"type": "ephemeral"}` to the system message block in the Anthropic API call
- Verify AnthropicVertex on your Vertex AI region supports prompt caching
- No other code changes needed

**Cache pricing (Haiku):**
- Cache write: $1.25/M tokens (one-time)
- Cache read: $0.10/M tokens (90% savings vs $1.00/M)
- Cache TTL: 5 minutes (resets on each hit)

---

### 3. Smart Early-Stop on URL Processing

**Impact: 10-20% total cost reduction**
**Risk: Low (minor reduction in multi-source confirmation)**
**Quality impact: Minimal for well-documented products**

Currently the pipeline always processes up to 3 URLs (`extract.py:219-222`). If the first URL is a **manufacturer** site and yields complete data, the 2nd and 3rd URLs add diminishing value.

**Implementation:**
```python
# After processing first URL, check completeness
if source_type == "manufacturer" and _extraction_is_complete(dim_extraction, content_extraction):
    logger.info(f"[Product {product_id}] Manufacturer data complete, skipping remaining URLs")
    break
```

Define `_extraction_is_complete()` as:
- Has at least 2 of: net_weight, net_height, net_width/length
- Has short_description OR marketing_description
- Has at least 3 technical specs
- Has at least 1 image URL

**What this saves per skipped URL:**
- 1 Firecrawl scrape credit ($0.00083)
- 1-2 Claude Haiku calls (~3,500 input + ~1,400 output tokens)

---

### 4. Replace LLM Validation with Deterministic Rules

**Impact: 5-8% total cost reduction**
**Risk: Low**
**Quality impact: None (rules cover the same checks)**

The validation phase (`validate.py`) sends all extracted data back to Claude for a sanity check. Every check it performs can be coded as a simple rule:

| LLM Validation Check | Deterministic Replacement |
|---|---|
| Weight reasonable for product type? | Lookup table: `{electronics: (0.01, 50), appliance: (0.5, 200), ...}` |
| Packaged >= net dimensions? | `packaged.weight >= net.weight` (with tolerance) |
| Dimensions form plausible shape? | Ratio checks: no dimension > 100x another |
| Description present? | `len(description) > 10` |
| Net dimensions positive? | `all(d > 0 for d in dimensions if d is not None)` |
| Tech specs make sense? | Check for empty values, duplicate keys |
| Warranty reasonable? | Duration regex + range check (0-10 years) |

**Implementation:**
- Create `_validate_deterministic(enriched: EnrichedProduct, classification: ProductClassification) -> ValidationReport`
- Run deterministic checks first
- Only call the LLM if deterministic validation flags ambiguous issues (optional fallback)
- Saves 1 Claude Haiku call (~1,600 tokens) per product

---

### 5. Cache Brand-to-Country-of-Origin Lookups

**Impact: 2-3% total cost reduction**
**Risk: None**
**Quality impact: None**

In `extract.py:918-978`, every product without a COO triggers a Tavily search + Claude call to find the brand's manufacturing country. But brand → country mappings are static ("Makita" is always "Japan").

**Implementation:**
- Add a `brand_coo_cache` table to the SQLite database:
  ```sql
  CREATE TABLE IF NOT EXISTS brand_coo_cache (
      brand TEXT PRIMARY KEY,
      country_of_origin TEXT,
      confidence TEXT,
      cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
  );
  ```
- Before calling Tavily + Claude, check the cache
- After a successful lookup, write to the cache
- Saves 1 Tavily credit + 1 Claude Haiku call for every repeat brand

---

## Projected Savings Summary

| Optimization | LLM Calls Saved/Product | Token Savings | Est. Cost Reduction |
|---|---|---|---|
| Merge Pass 1+2 | 1-3 | ~25K input/URL | **35-45%** |
| Prompt caching | 0 (cheaper calls) | 90% off system prompts | **5-10%** |
| Smart early-stop | 0-4 | Variable | **10-20%** |
| Deterministic validation | 1 | ~1,600 | **5-8%** |
| Brand COO cache | 0-1 | ~800 | **2-3%** |
| **Combined** | | | **~50-70%** |

### At Scale (1,000 products/month)

| Metric | Current | After Optimization |
|---|---|---|
| Claude calls per product | 8-9 typical | 4-5 typical |
| Input tokens per product | ~14,500 | ~6,000-8,000 |
| Est. Claude cost per product | $0.05-0.08 | $0.02-0.03 |
| Est. monthly Claude cost (1K products) | $50-80 | $20-30 |

---

## Implementation Priority

1. **Merge Pass 1+2** — Do this first. Biggest single win, straightforward refactor.
2. **Prompt caching** — Low effort, compound savings. Add after merge.
3. **Smart early-stop** — Quick conditional logic, good marginal gain.
4. **Deterministic validation** — Replace LLM with code rules.
5. **Brand COO cache** — Small win but trivial to implement.

---

## Files to Modify

| File | Changes |
|---|---|
| `backend/pipeline/extract.py` | Merge Pass 1+2, add early-stop logic, add brand COO cache |
| `backend/utils/llm.py` | Add prompt caching support (`cache_control`) |
| `backend/pipeline/validate.py` | Add deterministic validation rules, make LLM call optional |
| `backend/schemas.py` | Add `FullPageExtraction` combined schema |
| `backend/db.py` | Add `brand_coo_cache` table creation |
| `backend/utils/cost_tracker.py` | Update phase names for merged extraction |
