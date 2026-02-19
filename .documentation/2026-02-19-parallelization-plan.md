# Pipeline Parallelization & Throughput Optimization

## Context

The enrichment pipeline processes products **fully sequentially** — one product at a time, one API call at a time. For 100 products this takes ~2 hours 14 minutes. The goal is to scale to 1,000+ products with a target of ~100 products in 15-20 minutes, using configurable concurrency that adapts to the customer's API tier (Firecrawl Free → Standard, Vertex AI quotas).

### Current bottlenecks

1. **Batch loop** (`main.py:299`): Products processed one at a time with `time.sleep(0.5)` between each
2. **Search queries** (`search.py:111`): 2-3 Tavily searches run sequentially
3. **URL scraping** (`extract.py:344`): 5-8 Firecrawl scrapes run one at a time per product
4. **LLM extraction** (`extract.py:428,512`): Pass 1 + Pass 2 per URL, sequential
5. **Third-party caching** (`extract.py:595`): 3 pages scraped sequentially even if gap fill won't need them

### Rate limits by Firecrawl tier

| Plan | `/scrape` RPM | `/search` RPM | Concurrent browsers |
|------|--------------|--------------|-------------------|
| Free | 10 | 5 | 2 |
| Hobby | 100 | 50 | 5 |
| Standard | 500 | 250 | 50 |
| Growth | 5,000 | 2,500 | 100 |

### Other service limits

- **Tavily**: 100 RPM (free), 1,000 credits/month
- **Claude on Vertex AI**: Project-specific QPM (typically 30-60+ for Haiku), cached tokens don't count toward ITPM
- **Claude direct API (Haiku 4.5)**: Tier 1: 50 RPM / 50K ITPM. Tier 2: 1,000 RPM / 450K ITPM

### Time estimates

| Scenario | Per product | 100 products | 1,000 products |
|----------|-----------|-------------|---------------|
| Current (sequential) | ~80s | ~2h 14m | ~22h |
| After optimization (Free tier, 2 concurrent) | ~55s | ~1h 10m | ~11h |
| After optimization (Standard, 10 concurrent) | ~35s | ~5-7 min | ~50-60 min |
| After optimization (Standard, 20 concurrent) | ~35s | ~3-4 min | ~30-35 min |

---

## Implementation Plan

### Step 1: Rate Limiter Utility

**New file:** `backend/utils/rate_limiter.py`

Create an async rate limiter that enforces both concurrency and RPM limits per service:

```python
class AsyncRateLimiter:
    def __init__(self, name: str, max_concurrent: int, max_rpm: int):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.rpm_limit = max_rpm
        self.name = name
        # Token bucket for RPM enforcement
        self.tokens = max_rpm
        self.last_refill = time.monotonic()

    async def acquire(self):
        """Wait for both concurrency slot AND RPM token."""
        await self.semaphore.acquire()
        await self._wait_for_rpm_token()

    def release(self):
        self.semaphore.release()

    @asynccontextmanager
    async def throttle(self):
        await self.acquire()
        try:
            yield
        finally:
            self.release()
```

Add a **factory function** that reads config and returns pre-configured limiters:

```python
def create_rate_limiters() -> Dict[str, AsyncRateLimiter]:
    tier = os.getenv("FIRECRAWL_TIER", "free").lower()
    # Map tier → limits from the table above
    return {
        "firecrawl_scrape": AsyncRateLimiter("firecrawl_scrape", concurrent, scrape_rpm),
        "firecrawl_search": AsyncRateLimiter("firecrawl_search", concurrent, search_rpm),
        "llm": AsyncRateLimiter("llm", llm_concurrent, llm_rpm),
        "tavily": AsyncRateLimiter("tavily", 10, 100),
    }
```

**Config env vars:**
- `FIRECRAWL_TIER`: `free` | `hobby` | `standard` | `growth` (sets scrape/search RPM + concurrency)
- `MAX_CONCURRENT_PRODUCTS`: How many products to process in parallel (default: 1 for free, 10 for standard)
- `LLM_MAX_RPM`: Override LLM RPM limit (default: 50)
- `LLM_MAX_CONCURRENT`: Override LLM concurrency (default: 5)

### Step 2: Parallelize extraction scraping

**File:** `backend/pipeline/extract.py`

Replace the sequential URL scraping loop (line 344) with parallel scraping using the rate limiter:

```python
# Before: sequential
for result in urls_to_process:
    scraped = firecrawl.scrape(url, formats=['markdown'])
    # ... Pass 1 + Pass 2 ...

# After: parallel scrape, then parallel extract
async def _scrape_url(url, source_type, firecrawl, rate_limiter, product_id, cost_tracker):
    async with rate_limiter.throttle():
        scraped = firecrawl.scrape(url, formats=['markdown'])
        # ... cache page ...
        return (url, source_type, markdown)

# Scrape all URLs in parallel (rate-limited)
scrape_tasks = [_scrape_url(r['url'], r['source_type'], firecrawl, limiters['firecrawl_scrape'], ...)
                for r in urls_to_process + urls_to_cache_only]
scrape_results = await asyncio.gather(*scrape_tasks, return_exceptions=True)

# Then extract from scraped pages (LLM calls, also rate-limited)
for url, source_type, markdown in successful_scrapes:
    if source_type != 'third_party':  # Only extract from official + authorized
        async with limiters['llm'].throttle():
            dim_extraction = classify_with_schema(...)  # Pass 1
        async with limiters['llm'].throttle():
            content_extraction = classify_with_schema(...)  # Pass 2
```

Key design choice: scrape ALL URLs in parallel (including third-party), but only run LLM extraction on manufacturer + authorized. This front-loads all scraping into one parallel burst.

Pass 1 and Pass 2 for the same URL stay sequential (Pass 2 benefits from Mode B cache hit on Pass 1's cached markdown). But different URLs can run their passes concurrently.

### Step 3: Parallelize search queries

**File:** `backend/pipeline/search.py`

Replace sequential Tavily search loop with parallel:

```python
# Before: sequential
for q in queries[:max_general_queries]:
    response = client.search(query=q, max_results=7)

# After: parallel with rate limiter
async def _run_search(client, query, rate_limiter):
    async with rate_limiter.throttle():
        return client.search(query=query, max_results=7)

search_tasks = [_run_search(client, q, limiters['tavily']) for q in queries[:max_general_queries]]
results = await asyncio.gather(*search_tasks, return_exceptions=True)
```

Also parallelize the manufacturer search with the first general search.

### Step 4: Defer third-party scraping (reduce work per product)

**File:** `backend/pipeline/extract.py`

Currently all third-party pages are scraped during extract, even if gap fill won't need them. Instead:

1. **Extract node**: Only scrape manufacturer + authorized URLs. Don't scrape third-party.
2. **Gap fill node** (`backend/pipeline/gap_fill.py`): Check for gaps first. If gaps exist, THEN scrape third-party pages (using the rate limiter). If no gaps, skip entirely.

This saves 1-3 Firecrawl credits per product when extraction is already complete (~40% of products based on gap fill early-exit patterns).

Requires: pass the rate limiters through the LangGraph state (or use a global singleton).

### Step 5: Concurrent product processing

**File:** `backend/main.py`

Replace the sequential batch loop with an async worker pool:

```python
# Before:
def process_batch(product_ids: List[int]):
    for pid in product_ids:
        run_full_enrichment(pid)
        time.sleep(0.5)

# After:
async def process_batch_async(product_ids: List[int]):
    max_concurrent = int(os.getenv("MAX_CONCURRENT_PRODUCTS", "1"))
    product_semaphore = asyncio.Semaphore(max_concurrent)
    limiters = create_rate_limiters()  # Shared across all products

    async def process_one(pid):
        async with product_semaphore:
            await run_full_enrichment_async(pid, limiters)

    await asyncio.gather(*[process_one(pid) for pid in product_ids])
```

Key changes:
- `run_full_enrichment()` becomes async (no more `_run_async_in_thread`)
- Rate limiters are **shared** across all concurrent products — this is what prevents exceeding API limits
- Product-level semaphore controls how many products run simultaneously
- Remove the hardcoded `time.sleep(0.5)` — rate limiters handle throttling

### Step 6: Pass rate limiters through the pipeline

**Option A (recommended): Global singleton**

```python
# backend/utils/rate_limiter.py
_limiters: Dict[str, AsyncRateLimiter] = None

def get_rate_limiters() -> Dict[str, AsyncRateLimiter]:
    global _limiters
    if _limiters is None:
        _limiters = create_rate_limiters()
    return _limiters
```

Each pipeline node imports `get_rate_limiters()` and uses it. Simple, no state changes needed.

**Option B: Through LangGraph state**

Add `rate_limiters` to `ProductState`. More explicit but requires changing the state type and all nodes.

Recommendation: Option A. Rate limiters are infrastructure, not per-product state.

### Step 7: Retry with exponential backoff

**File:** `backend/utils/rate_limiter.py`

Add retry logic for 429 errors from any service:

```python
async def with_retry(fn, max_retries=3, base_delay=1.0):
    for attempt in range(max_retries):
        try:
            return await fn()
        except (HTTPError, RateLimitError) as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
            await asyncio.sleep(delay)
```

Wrap all Firecrawl, Tavily, and Claude calls with this.

---

## Files to Modify

| File | Change |
|------|--------|
| `backend/utils/rate_limiter.py` | **New** — AsyncRateLimiter, tier configs, retry logic |
| `backend/main.py` | Async batch processor, remove sequential loop + sleep |
| `backend/pipeline/extract.py` | Parallel scraping, parallel LLM extraction per URL |
| `backend/pipeline/search.py` | Parallel Tavily searches |
| `backend/pipeline/gap_fill.py` | Deferred third-party scraping (scrape on demand) |
| `backend/graph.py` | No structural changes needed (nodes stay sequential per product) |

---

## Configuration Reference

```env
# Firecrawl tier — sets scrape RPM, search RPM, concurrency
FIRECRAWL_TIER=free          # free|hobby|standard|growth

# How many products to process simultaneously
MAX_CONCURRENT_PRODUCTS=1    # 1 for free, 10-20 for standard

# LLM rate limits (override if your Vertex AI quotas differ)
LLM_MAX_RPM=50
LLM_MAX_CONCURRENT=5

# Existing cost guardrails (unchanged)
DAILY_PRODUCT_LIMIT=200
MAX_BATCH_SIZE=50
MAX_DAILY_COST_USD=50
```

---

## Verification

1. **Free tier smoke test**: Set `FIRECRAWL_TIER=free`, `MAX_CONCURRENT_PRODUCTS=1`. Process 3 products. Verify behavior matches current (sequential, no 429 errors).
2. **Concurrency test**: Set `MAX_CONCURRENT_PRODUCTS=3`. Process 5 products. Verify:
   - Multiple products show "enriching" simultaneously in the UI
   - SSE events stream correctly for all concurrent products
   - No SQLite locking errors (WAL mode should handle this)
   - Rate limits are respected (check logs for RPM tracking)
3. **Standard tier test**: Set `FIRECRAWL_TIER=standard`, `MAX_CONCURRENT_PRODUCTS=10`. Process 20 products. Verify:
   - Scraping happens in parallel (multiple scrape logs interleaved)
   - Total time is ~3-4 min, not ~26 min
   - No 429 errors from any service
4. **Deferred scraping test**: Process a product that doesn't need gap fill. Verify third-party pages are NOT scraped (saves credits).
5. **429 retry test**: Temporarily set `LLM_MAX_RPM=2`. Process 3 products. Verify retries happen with backoff and products eventually complete.

---

## Implementation Order

1. Rate limiter utility (Step 1) — foundation, no behavior change
2. Parallel scraping in extract.py (Step 2) — biggest single speedup
3. Async batch processor in main.py (Step 5) — enables multi-product concurrency
4. Parallel searches (Step 3) — smaller win but easy
5. Deferred third-party scraping (Step 4) — cost optimization
6. Retry logic (Step 7) — resilience
