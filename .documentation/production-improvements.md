# PRODUCTION IMPROVEMENTS GUIDE
## Shoppster Product Enrichment Engine

**Purpose:** This document contains detailed, file-level instructions for an AI LLM coding agent to implement production-hardening improvements. Each section specifies exactly what to change, in which file, and why.

**Context:** This is a LangGraph-orchestrated product data enrichment pipeline (Next.js frontend + Python FastAPI backend + SQLite). It replaces ~80 hours/month of manual product data entry for Shoppster d.o.o. The current prototype works but has critical gaps that would cause failures, data corruption, or cost overruns in production.

**How to use this document:** Work through the phases in order. Each phase is self-contained. Complete Phase 1 before starting Phase 2, etc. Within each phase, implement all items. Test after each phase.

---

# PHASE 1: CRITICAL RELIABILITY FIXES
*These prevent pipeline crashes, stuck products, data corruption, and cost explosions.*

---

## 1.1 Stuck Product Recovery + Double-Run Guard

### Problem
If the pipeline crashes mid-execution (API timeout, server restart, unhandled exception), the product stays in `enriching`/`classifying`/`searching`/`extracting`/`validating` status forever. The UI shows an infinite spinner. There is also nothing preventing two simultaneous enrichment runs on the same product (user clicks "Enrich" twice, or both "Process All" and individual enrich fire).

### Files to modify
- `backend/main.py`
- `backend/db.py`

### Instructions

**A. Add a processing lock check in `main.py`**

In `run_full_enrichment()` (line 102), before setting status to `enriching`, check if the product is already being processed:

```python
async def run_full_enrichment(product_id: int):
    # CHECK: Is product already being processed?
    conn = get_db_connection()
    product_row = conn.execute(
        "SELECT status, updated_at FROM products WHERE id = ?", (product_id,)
    ).fetchone()
    conn.close()

    if not product_row:
        logger.error(f"[Product {product_id}] Not found, skipping")
        return

    ACTIVE_STATUSES = ['enriching', 'classifying', 'searching', 'extracting', 'validating']
    if product_row['status'] in ACTIVE_STATUSES:
        # Check if it's actually stuck (no update in 5 minutes = stuck)
        from datetime import datetime, timedelta
        try:
            last_update = datetime.fromisoformat(product_row['updated_at'])
            if datetime.now() - last_update < timedelta(minutes=5):
                logger.warning(f"[Product {product_id}] Already processing (status={product_row['status']}), skipping")
                return
            else:
                logger.warning(f"[Product {product_id}] Stuck in {product_row['status']} for >5min, restarting...")
        except:
            pass  # If timestamp parsing fails, allow restart

    # ... rest of existing function
```

**B. Add a startup stale-product recovery in `main.py`**

In the `startup_event()` function (line 46), after `init_db()`, add recovery for products stuck in processing states from a previous server session:

```python
@app.on_event("startup")
def startup_event():
    init_db()
    # Recover products stuck from previous server session
    recover_stuck_products()
```

Add this function to `main.py`:

```python
def recover_stuck_products():
    """Reset products stuck in processing states from a previous server session."""
    conn = get_db_connection()
    stuck = conn.execute(
        "SELECT id, status FROM products WHERE status IN ('enriching', 'classifying', 'searching', 'extracting', 'validating')"
    ).fetchall()
    if stuck:
        ids = [row['id'] for row in stuck]
        conn.execute(
            f"UPDATE products SET status = 'error', current_step = 'Server restarted during processing — click retry to re-run', updated_at = CURRENT_TIMESTAMP WHERE id IN ({','.join('?' * len(ids))})",
            ids
        )
        conn.commit()
        logger.warning(f"Recovered {len(ids)} stuck products: {ids}")
    conn.close()
```

**C. Add a stale product watchdog background task in `main.py`**

Add a periodic background task that runs every 60 seconds and marks products stuck for >5 minutes as `error`:

```python
import asyncio

async def watchdog_loop():
    """Background task: detect and recover stuck products."""
    while True:
        await asyncio.sleep(60)
        try:
            conn = get_db_connection()
            # Find products stuck in processing for >5 minutes
            stuck = conn.execute("""
                SELECT id, status, updated_at FROM products
                WHERE status IN ('enriching', 'classifying', 'searching', 'extracting', 'validating')
                AND datetime(updated_at) < datetime('now', '-5 minutes')
            """).fetchall()
            for row in stuck:
                conn.execute(
                    "UPDATE products SET status = 'error', current_step = 'Timed out after 5 minutes of no progress', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (row['id'],)
                )
                append_log(row['id'], {
                    "timestamp": datetime.now().isoformat(),
                    "phase": "pipeline", "step": "watchdog", "status": "error",
                    "details": f"Product timed out in '{row['status']}' state"
                })
                logger.warning(f"[Watchdog] Product {row['id']} timed out in '{row['status']}'")
            if stuck:
                conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"[Watchdog] Error: {e}")

@app.on_event("startup")
async def startup_event():
    init_db()
    recover_stuck_products()
    asyncio.create_task(watchdog_loop())
```

Note: The startup event must become `async` for this to work.

**D. Prevent reset during processing in `main.py`**

In the `reset_product()` endpoint (line 245), add a check:

```python
@app.post("/api/products/{id}/reset")
async def reset_product(id: int):
    conn = get_db_connection()
    product = conn.execute("SELECT * FROM products WHERE id = ?", (id,)).fetchone()
    if not product:
        conn.close()
        raise HTTPException(status_code=404, detail="Product not found")

    ACTIVE_STATUSES = ['enriching', 'classifying', 'searching', 'extracting', 'validating']
    if product['status'] in ACTIVE_STATUSES:
        conn.close()
        raise HTTPException(status_code=409, detail="Cannot reset product while it is being processed")

    # ... rest of existing reset logic
```

---

## 1.2 Retry Logic with Exponential Backoff

### Problem
All external API calls (Firecrawl, Tavily, Claude, Gemini) have either no retry or a single retry with a hardcoded 3-second sleep. In production, APIs have transient failures, rate limits, and slow responses. One bad minute = a batch of failed products.

### Files to modify
- `backend/utils/retry.py` (NEW FILE)
- `backend/pipeline/extract.py`
- `backend/pipeline/search.py`
- `backend/utils/ean_lookup.py`
- `backend/utils/llm.py`
- `backend/utils/gemini_vision.py`

### Instructions

**A. Create `backend/utils/retry.py`**

Create a reusable retry utility with exponential backoff:

```python
"""
Retry utility with exponential backoff for external API calls.
"""

import asyncio
import logging
import time
from functools import wraps
from typing import Callable, Tuple, Type

logger = logging.getLogger("pipeline.retry")

# Default retryable exceptions
RETRYABLE_EXCEPTIONS: Tuple[Type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
)


def retry_sync(
    max_retries: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 30.0,
    retryable_exceptions: Tuple[Type[Exception], ...] = None,
    operation_name: str = "operation",
):
    """
    Synchronous retry decorator with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts (total attempts = max_retries + 1)
        base_delay: Initial delay in seconds (doubles each retry)
        max_delay: Maximum delay between retries
        retryable_exceptions: Tuple of exception types to retry on. If None, retries on all exceptions.
        operation_name: Name for logging
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            exceptions_to_catch = retryable_exceptions or Exception

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions_to_catch as e:
                    last_exception = e
                    if attempt < max_retries:
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        logger.warning(
                            f"[Retry] {operation_name} failed (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                            f"Retrying in {delay:.1f}s..."
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            f"[Retry] {operation_name} failed after {max_retries + 1} attempts: {e}"
                        )

            raise last_exception

        return wrapper
    return decorator


async def retry_async(
    func: Callable,
    *args,
    max_retries: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 30.0,
    operation_name: str = "operation",
    **kwargs,
):
    """
    Async retry helper with exponential backoff.
    Call as: result = await retry_async(some_func, arg1, arg2, max_retries=3)
    """
    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            if asyncio.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            else:
                return func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            if attempt < max_retries:
                delay = min(base_delay * (2 ** attempt), max_delay)
                logger.warning(
                    f"[Retry] {operation_name} failed (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                    f"Retrying in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    f"[Retry] {operation_name} failed after {max_retries + 1} attempts: {e}"
                )

    raise last_exception
```

**B. Apply retry to Firecrawl scraping in `backend/pipeline/extract.py`**

Replace the manual retry logic in `extract_node()` (lines 140-274). Instead of the try/except with a single `await asyncio.sleep(3)` retry, use the retry utility:

```python
from utils.retry import retry_async

# Inside the for loop over urls_to_process:
scraped = await retry_async(
    firecrawl.scrape,
    url,
    formats=['markdown'],
    max_retries=2,
    base_delay=3.0,
    operation_name=f"Firecrawl scrape {_shorten_url(url)}"
)
```

Remove the entire inner `except`/retry block (lines 244-274) and replace with a single clean try/except that logs and continues on final failure.

**C. Apply retry to Tavily searches in `backend/pipeline/search.py`**

Wrap the Tavily search call (line 71) in retry logic:

```python
from utils.retry import retry_async

# Replace: response = client.search(query=q, max_results=7)
response = await retry_async(
    client.search,
    query=q,
    max_results=7,
    max_retries=2,
    base_delay=2.0,
    operation_name=f"Tavily search '{q[:40]}'"
)
```

**D. Apply retry to LLM calls in `backend/utils/llm.py`**

Wrap the `client.messages.create()` call (line 68) in retry logic. Since `classify_with_schema` is synchronous, use the sync decorator:

```python
from utils.retry import retry_sync

# Add retry to the inner API call, not the whole function
# (so JSON parsing errors don't trigger unnecessary retries)
@retry_sync(max_retries=2, base_delay=2.0, operation_name="Claude API")
def _call_claude(client, model_id, full_system, prompt, max_tokens=2048):
    response = client.messages.create(
        model=model_id,
        max_tokens=max_tokens,
        system=full_system,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text
```

Then call `_call_claude()` from `classify_with_schema()`.

**E. Apply retry to Gemini calls in `backend/utils/gemini_vision.py`**

Wrap the `model.generate_content()` calls (lines 59, 121) with retry. Since these are synchronous:

```python
from utils.retry import retry_sync

@retry_sync(max_retries=2, base_delay=2.0, operation_name="Gemini Vision")
def _gemini_generate(model, content):
    return model.generate_content(content)
```

**F. Apply retry to EAN lookup in `backend/utils/ean_lookup.py`**

Wrap `app.scrape()` (line 28):

```python
from utils.retry import retry_async

scraped = await retry_async(
    app.scrape,
    url,
    formats=['markdown'],
    max_retries=2,
    base_delay=3.0,
    operation_name=f"EAN lookup {ean}"
)
```

---

## 1.3 API Call Timeouts

### Problem
No external API call has a timeout set. A single hanging Firecrawl scrape or Tavily search can block the entire pipeline indefinitely. Gemini vision calls to unreachable image URLs can hang for minutes.

### Files to modify
- `backend/pipeline/extract.py`
- `backend/pipeline/search.py`
- `backend/utils/llm.py`
- `backend/utils/gemini_vision.py`
- `backend/utils/ean_lookup.py`

### Instructions

**A. Add timeout to Firecrawl scrape calls**

Firecrawl's Python SDK supports a `timeout` parameter. Add it wherever `firecrawl.scrape()` is called:

In `backend/pipeline/extract.py` line 146:
```python
scraped = firecrawl.scrape(url, formats=['markdown'], timeout=30000)  # 30 seconds
```

In `backend/utils/ean_lookup.py` line 28:
```python
scraped = app.scrape(url, formats=['markdown'], timeout=30000)
```

If the Firecrawl SDK doesn't support a `timeout` parameter natively, wrap the call with `asyncio.wait_for()`:
```python
scraped = await asyncio.wait_for(
    asyncio.to_thread(firecrawl.scrape, url, formats=['markdown']),
    timeout=30.0
)
```

**B. Add timeout to Claude LLM calls**

In `backend/utils/llm.py`, the AnthropicVertex client supports a `timeout` parameter on `messages.create()`:

```python
response = client.messages.create(
    model=model_id,
    max_tokens=2048,
    system=full_system,
    messages=[{"role": "user", "content": prompt}],
    timeout=30.0  # 30 seconds
)
```

**C. Add timeout to Gemini calls**

In `backend/utils/gemini_vision.py`, wrap `model.generate_content()` calls with a timeout. The Vertex AI SDK may support `request_options`. If not, use threading:

```python
import concurrent.futures

def _gemini_generate_with_timeout(model, content, timeout=20):
    """Call Gemini with a timeout."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(model.generate_content, content)
        return future.result(timeout=timeout)
```

Use this wrapper in `detect_color_from_image()` (line 59) and `describe_images()` (line 121).

**D. Add timeout to Tavily search calls**

In `backend/pipeline/search.py` line 71, wrap the Tavily call:

```python
import asyncio

response = await asyncio.wait_for(
    asyncio.to_thread(client.search, query=q, max_results=7),
    timeout=15.0
)
```

---

## 1.4 Robust LLM Output Parsing

### Problem
`backend/utils/llm.py` lines 78-81 use `str.split("```json")` to extract JSON from Claude's response. This is brittle. If Claude returns an explanation instead of JSON, returns multiple code blocks, or returns malformed JSON, the pipeline crashes and the product gets stuck.

### File to modify
- `backend/utils/llm.py`

### Instructions

Replace the JSON extraction logic (lines 75-83) with a more robust approach:

```python
import re

def _extract_json_from_response(content: str) -> str:
    """
    Robustly extract JSON from an LLM response.
    Handles: raw JSON, markdown fences, mixed text+JSON, multiple code blocks.
    """
    content = content.strip()

    # 1. Try raw JSON parse first (ideal case)
    if content.startswith('{') or content.startswith('['):
        return content

    # 2. Extract from markdown fences (```json ... ``` or ``` ... ```)
    fence_patterns = [
        r'```json\s*\n?(.*?)\n?\s*```',
        r'```\s*\n?(.*?)\n?\s*```',
    ]
    for pattern in fence_patterns:
        match = re.search(pattern, content, re.DOTALL)
        if match:
            candidate = match.group(1).strip()
            if candidate.startswith('{') or candidate.startswith('['):
                return candidate

    # 3. Find the first JSON object or array in the text
    # Look for outermost { ... } or [ ... ]
    brace_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', content, re.DOTALL)
    if brace_match:
        return brace_match.group(0)

    bracket_match = re.search(r'\[.*\]', content, re.DOTALL)
    if bracket_match:
        return bracket_match.group(0)

    # 4. Nothing found — return as-is and let Pydantic handle the error
    return content


def classify_with_schema(prompt: str, system: str, schema: Type[T], model: str = "haiku") -> T:
    """..."""
    client = get_raw_client()
    model_id = HAIKU_MODEL if model == "haiku" else SONNET_MODEL

    json_schema = schema.model_json_schema()
    full_system = f"""{system}

Respond with ONLY valid JSON matching this schema:
{json.dumps(json_schema, indent=2)}"""

    try:
        content = _call_claude(client, model_id, full_system, prompt)
        json_str = _extract_json_from_response(content)

        try:
            return schema.model_validate_json(json_str)
        except Exception as parse_error:
            # One retry with corrective prompt
            logger.warning(f"JSON parse failed, retrying with correction: {parse_error}")
            corrective_prompt = (
                f"Your previous response could not be parsed as valid JSON.\n"
                f"Error: {parse_error}\n\n"
                f"Please respond with ONLY valid JSON matching the schema. "
                f"No explanation, no markdown, no code fences. Just the JSON object.\n\n"
                f"Original request: {prompt}"
            )
            content = _call_claude(client, model_id, full_system, corrective_prompt)
            json_str = _extract_json_from_response(content)
            return schema.model_validate_json(json_str)

    except Exception as e:
        logger.error(f"LLM Error (model={model_id}): {e}")
        raise
```

Also replace `print()` on line 86 with `logger.error()`. Add at the top of the file:
```python
logger = logging.getLogger("pipeline.llm")
```

---

## 1.5 Input Validation on CSV Upload

### Problem
`backend/main.py` lines 70-73 accept literally any string for EAN (including `"UNKNOWN"`, `""`, `"N/A"`), have no file size limit, and don't validate required columns exist. Bad data flows through the entire pipeline, wasting API credits on garbage rows.

### Files to modify
- `backend/main.py`
- `backend/schemas.py`

### Instructions

**A. Add EAN validation to `backend/schemas.py`**

```python
import re

def validate_ean(ean: str) -> tuple[bool, str]:
    """
    Validate EAN/UPC format. Returns (is_valid, cleaned_ean).
    Accepts EAN-8 (8 digits), UPC-A (12 digits), EAN-13 (13 digits), EAN-14 (14 digits).
    """
    cleaned = re.sub(r'[\s\-.]', '', str(ean).strip())
    if not cleaned.isdigit():
        return False, cleaned
    if len(cleaned) not in (8, 12, 13, 14):
        return False, cleaned
    return True, cleaned
```

**B. Rewrite the upload endpoint in `backend/main.py`**

Replace lines 52-89 with:

```python
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB

@app.post("/api/upload")
async def upload_products(file: UploadFile = File(...)):
    if not file.filename.endswith(('.csv', '.xlsx')):
        raise HTTPException(status_code=400, detail="Invalid file format. Accepts .csv or .xlsx")

    contents = await file.read()

    # File size check
    if len(contents) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail=f"File too large. Maximum size is {MAX_UPLOAD_SIZE // (1024*1024)} MB")

    try:
        if file.filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(contents))
        else:
            df = pd.read_excel(io.BytesIO(contents))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse file: {str(e)}")

    if df.empty:
        raise HTTPException(status_code=400, detail="File contains no data rows")

    # Validate required columns exist
    # Support multiple column name variants
    ean_col = next((c for c in df.columns if c.lower() in ('ean', 'ean code', 'barcode', 'upc')), None)
    name_col = next((c for c in df.columns if c.lower() in ('name', 'product name', 'naziv', 'product_name', 'productname')), None)

    if not ean_col:
        raise HTTPException(status_code=400, detail=f"Missing required column: EAN. Found columns: {list(df.columns)}")
    if not name_col:
        raise HTTPException(status_code=400, detail=f"Missing required column: Name/Product Name. Found columns: {list(df.columns)}")

    brand_col = next((c for c in df.columns if c.lower() in ('brand', 'blagovna znamka')), None)
    weight_col = next((c for c in df.columns if c.lower() in ('weight', 'teža', 'teza', 'masa')), None)

    from schemas import validate_ean

    conn = get_db_connection()
    c = conn.cursor()

    inserted = 0
    skipped = 0
    errors = []

    for idx, row in df.iterrows():
        row_dict = json.loads(row.to_json())

        ean_raw = str(row[ean_col]) if pd.notna(row[ean_col]) else ''
        name = str(row[name_col]) if pd.notna(row[name_col]) else ''
        brand = str(row[brand_col]) if brand_col and pd.notna(row.get(brand_col)) else None
        weight = str(row[weight_col]) if weight_col and pd.notna(row.get(weight_col)) else None

        # Skip rows with no name
        if not name or name.lower() in ('nan', 'none', 'unknown', ''):
            skipped += 1
            continue

        # Validate EAN
        ean_valid, ean_cleaned = validate_ean(ean_raw)
        if not ean_valid:
            errors.append(f"Row {idx + 2}: Invalid EAN '{ean_raw}'")
            # Still insert but flag it
            ean_cleaned = ean_raw

        # Check for duplicates
        existing = c.execute("SELECT id FROM products WHERE ean = ?", (ean_cleaned,)).fetchone()
        if existing:
            skipped += 1
            continue

        c.execute("""
            INSERT INTO products (ean, product_name, brand, weight, original_data)
            VALUES (?, ?, ?, ?, ?)
        """, (ean_cleaned, name, brand, weight, json.dumps(row_dict)))
        inserted += 1

    conn.commit()
    conn.close()

    result = {"message": f"Inserted {inserted} products", "inserted": inserted, "skipped": skipped}
    if errors:
        result["warnings"] = errors[:20]  # Cap warnings at 20
    return result
```

---

## 1.6 Cost Guardrails

### Problem
Each product makes ~35-40 API calls. There are no daily spending caps, no per-batch limits, and no way to pause if costs are spiraling. An accidental "Process All" on a large dataset could cost $500-1000 in a single run.

### Files to modify
- `backend/main.py`
- `backend/db.py`
- `backend/utils/cost_tracker.py` (NEW FILE)

### Instructions

**A. Create `backend/utils/cost_tracker.py`**

```python
"""
Cost tracking and guardrails for API usage.
Tracks daily API call counts and estimated costs.
Enforces configurable daily limits.
"""

import os
import json
import logging
from datetime import datetime, date
from db import get_db_connection

logger = logging.getLogger("pipeline.costs")

# Default daily limits (can be overridden via env vars)
DEFAULT_DAILY_PRODUCT_LIMIT = int(os.getenv("DAILY_PRODUCT_LIMIT", "200"))
DEFAULT_MAX_BATCH_SIZE = int(os.getenv("MAX_BATCH_SIZE", "50"))

# Estimated costs per API call (in EUR)
COST_ESTIMATES = {
    "tavily": 0.005,
    "firecrawl": 0.01,
    "claude_haiku": 0.003,
    "claude_sonnet": 0.015,
    "gemini_flash": 0.005,
}


def get_daily_stats() -> dict:
    """Get today's processing stats from the enrichment logs."""
    conn = get_db_connection()
    today = date.today().isoformat()

    # Count products processed today
    processed_today = conn.execute(
        "SELECT COUNT(*) as c FROM products WHERE date(updated_at) = ? AND status IN ('done', 'needs_review', 'error')",
        (today,)
    ).fetchone()['c']

    currently_processing = conn.execute(
        "SELECT COUNT(*) as c FROM products WHERE status IN ('enriching', 'classifying', 'searching', 'extracting', 'validating')"
    ).fetchone()['c']

    conn.close()

    return {
        "processed_today": processed_today,
        "currently_processing": currently_processing,
        "daily_limit": DEFAULT_DAILY_PRODUCT_LIMIT,
        "remaining": max(0, DEFAULT_DAILY_PRODUCT_LIMIT - processed_today),
        "max_batch_size": DEFAULT_MAX_BATCH_SIZE,
    }


def check_can_process(requested_count: int) -> tuple[bool, str]:
    """
    Check if we can process the requested number of products.
    Returns (allowed, reason).
    """
    stats = get_daily_stats()

    if requested_count > stats["max_batch_size"]:
        return False, f"Batch size {requested_count} exceeds maximum of {stats['max_batch_size']}. Process in smaller batches."

    if stats["remaining"] < requested_count:
        return False, (
            f"Daily limit would be exceeded. "
            f"Processed today: {stats['processed_today']}/{stats['daily_limit']}. "
            f"Requested: {requested_count}. Remaining: {stats['remaining']}."
        )

    return True, "OK"
```

**B. Enforce limits in `backend/main.py`**

In the `process_all_products()` endpoint (line 170):

```python
from utils.cost_tracker import check_can_process, get_daily_stats, DEFAULT_MAX_BATCH_SIZE

@app.post("/api/products/process-all")
async def process_all_products(background_tasks: BackgroundTasks):
    conn = get_db_connection()
    rows = conn.execute("SELECT id FROM products WHERE status IN ('pending', 'needs_review')").fetchall()
    conn.close()

    product_ids = [row['id'] for row in rows]
    if not product_ids:
        return {"message": "No pending products found"}

    # Enforce batch and daily limits
    allowed, reason = check_can_process(len(product_ids))
    if not allowed:
        raise HTTPException(status_code=429, detail=reason)

    # Cap at max batch size
    batch = product_ids[:DEFAULT_MAX_BATCH_SIZE]
    background_tasks.add_task(process_batch, batch)

    remaining = len(product_ids) - len(batch)
    msg = f"Started processing {len(batch)} products"
    if remaining > 0:
        msg += f" ({remaining} more queued for next batch)"
    return {"message": msg}
```

In the `process_batch_products()` endpoint (line 183), add the same check:

```python
@app.post("/api/products/process-batch")
async def process_batch_products(request: BatchProcessRequest, background_tasks: BackgroundTasks):
    if not request.product_ids:
        return {"message": "No product IDs provided"}

    allowed, reason = check_can_process(len(request.product_ids))
    if not allowed:
        raise HTTPException(status_code=429, detail=reason)

    background_tasks.add_task(process_batch, request.product_ids)
    return {"message": f"Started processing {len(request.product_ids)} products"}
```

**C. Add a cost stats endpoint**

```python
@app.get("/api/dashboard/costs")
def get_cost_stats():
    return get_daily_stats()
```

**D. Add environment variables**

Document in `.env.example`:
```
DAILY_PRODUCT_LIMIT=200
MAX_BATCH_SIZE=50
```

---

## 1.7 Database Connection Safety

### Problem
Database connections are opened and closed manually throughout the codebase. If an exception occurs between `get_db_connection()` and `conn.close()`, the connection leaks. Under high error rates with SQLite, this leads to `"database is locked"` errors.

### File to modify
- `backend/db.py`

### Instructions

Add a context manager to `db.py`:

```python
from contextlib import contextmanager

@contextmanager
def get_db():
    """Context manager for safe database access. Always closes connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # Better concurrent read performance
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

Then gradually migrate callers. For example, in `main.py`:

```python
# Before:
conn = get_db_connection()
product = conn.execute("SELECT * FROM products WHERE id = ?", (id,)).fetchone()
conn.close()

# After:
with get_db() as conn:
    product = conn.execute("SELECT * FROM products WHERE id = ?", (id,)).fetchone()
```

Keep the existing `get_db_connection()` for backward compatibility but add a deprecation comment. Migrate callers incrementally file by file.

Also add `PRAGMA journal_mode=WAL` to `init_db()` — this enables concurrent reads while a write is in progress, which is critical for the frontend polling the database while enrichment is running.

---

# PHASE 2: PRODUCTION INFRASTRUCTURE
*These changes enable real deployment, debugging, and multi-user access.*

---

## 2.1 Environment-Based Configuration

### Problem
The frontend has `http://localhost:8000` hardcoded in `lib/api.ts` (line 8) and `app/page.tsx` (line 75). Backend has CORS hardcoded to `localhost:3000`. The database path is hardcoded. Model names are hardcoded. None of this works in production.

### Files to modify
- `lib/api.ts`
- `app/page.tsx` (and any other files with hardcoded `localhost:8000`)
- `backend/main.py`
- `backend/db.py`
- `backend/utils/llm.py`
- `.env.example` (NEW FILE)

### Instructions

**A. Frontend: Use environment variable for API base**

In `lib/api.ts`, replace line 8:
```typescript
const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
```

Search for ALL occurrences of `localhost:8000` in the frontend codebase (use grep/search) and replace with a reference to this constant. In `app/page.tsx` the export URL (around line 75) should use the same variable:
```typescript
window.open(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api'}/export`, '_blank');
```

**B. Backend CORS from environment**

In `backend/main.py` lines 35-41:
```python
allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**C. Database path from environment**

In `backend/db.py` line 6:
```python
DB_PATH = os.getenv("DATABASE_PATH", "products.db")
```

**D. Model names from environment**

In `backend/utils/llm.py` lines 21-22:
```python
HAIKU_MODEL = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5")
SONNET_MODEL = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-5@20250929")
```

In `backend/utils/gemini_vision.py` line 18:
```python
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
```

**E. Create `.env.example`**

Create a `.env.example` file in the project root:
```
# Required: Google Cloud Vertex AI
VERTEX_PROJECT_ID=your-gcp-project-id
VERTEX_LOCATION=us-east5
GOOGLE_APPLICATION_CREDENTIALS=path/to/service-account.json

# Required: External APIs
TAVILY_API_KEY=tvly-xxxxxxxxxxxxx
FIRECRAWL_API_KEY=fc-xxxxxxxxxxxxx

# Optional: Model overrides
CLAUDE_HAIKU_MODEL=claude-haiku-4-5
CLAUDE_SONNET_MODEL=claude-sonnet-4-5@20250929
GEMINI_MODEL=gemini-3-flash-preview

# Optional: Production settings
ALLOWED_ORIGINS=http://localhost:3000
DATABASE_PATH=products.db
DAILY_PRODUCT_LIMIT=200
MAX_BATCH_SIZE=50

# Frontend
NEXT_PUBLIC_API_URL=http://localhost:8000/api
```

---

## 2.2 Structured Logging

### Problem
Logging is inconsistent: some files use `logger.info()`, some use `print()` (e.g., `ean_lookup.py` line 19, `llm.py` line 86, `normalization.py` line 87). In production, `print()` goes to stdout and is not captured by log aggregators. There is no structured format for machine-readable parsing, and no correlation ID to track a product through all its log entries.

### Files to modify
- `backend/main.py`
- `backend/utils/ean_lookup.py`
- `backend/utils/llm.py`
- `backend/utils/normalization.py`

### Instructions

**A. Replace all `print()` calls with `logger` calls**

In `backend/utils/ean_lookup.py`:
- Line 19: `print("Warning:...")` → `logger.warning("FIRECRAWL_API_KEY not found. Skipping EAN lookup.")`
- Line 27: `print(f"Scraping...")` → `logger.info(f"Scraping...")`
- Line 39: `print("Firecrawl returned no markdown.")` → `logger.warning("Firecrawl returned no markdown for EAN lookup")`
- Line 62: `print(f"EAN Lookup failed: {e}")` → `logger.error(f"EAN Lookup failed: {e}")`
- Add at top: `logger = logging.getLogger("pipeline.ean_lookup")`

In `backend/utils/llm.py`:
- Line 86: `print(f"LLM Error...")` → `logger.error(f"LLM Error...")`
- Add at top: `logger = logging.getLogger("pipeline.llm")`

In `backend/utils/normalization.py`:
- Line 87: `print(f"Normalization failed...")` → `logger.warning(f"Normalization failed...")`
- Add at top: `import logging` and `logger = logging.getLogger("pipeline.normalization")`

**B. Add JSON structured logging option**

In `backend/main.py`, enhance the logging configuration to support both human-readable (dev) and JSON (production) formats:

```python
import sys

LOG_FORMAT = os.getenv("LOG_FORMAT", "human")  # "human" or "json"

if LOG_FORMAT == "json":
    import json as json_mod

    class JSONFormatter(logging.Formatter):
        def format(self, record):
            log_entry = {
                "timestamp": self.formatTime(record),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            if record.exc_info:
                log_entry["exception"] = self.formatException(record.exc_info)
            return json_mod.dumps(log_entry)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    logging.root.handlers = [handler]
    logging.root.setLevel(logging.INFO)
else:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)-20s | %(message)s",
        datefmt="%H:%M:%S",
    )
```

---

## 2.3 Concurrent Processing with Semaphore

### Problem
`backend/main.py` lines 162-166 process products one at a time with a 0.5s sleep between them. For 200 products, this takes 2+ hours. Much of this time is spent waiting for Firecrawl/Tavily/Claude API responses — time that could be used processing another product.

### File to modify
- `backend/main.py`

### Instructions

Replace the sequential `process_batch()` function:

```python
# Configurable concurrency (default 3 parallel products)
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_PRODUCTS", "3"))

async def process_batch(product_ids: List[int]):
    """Process products with controlled concurrency."""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def process_with_semaphore(pid):
        async with semaphore:
            await run_full_enrichment(pid)
            await asyncio.sleep(1.0)  # Brief pause between products for rate limiting

    tasks = [process_with_semaphore(pid) for pid in product_ids]
    await asyncio.gather(*tasks, return_exceptions=True)
```

This processes up to 3 products simultaneously (configurable via `MAX_CONCURRENT_PRODUCTS` env var), which should cut processing time by ~3x while still respecting API rate limits.

Add to `.env.example`:
```
MAX_CONCURRENT_PRODUCTS=3
```

---

## 2.4 API Authentication

### Problem
Every endpoint in the backend is publicly accessible. Anyone who discovers the URL can upload CSVs, trigger enrichment (burning API credits), and download data. There is no authentication at all.

### Files to modify
- `backend/main.py`

### Instructions

Add a simple API key authentication middleware. This is the minimum viable security for an internal tool:

```python
from fastapi import Depends, Security
from fastapi.security import APIKeyHeader

API_KEY = os.getenv("API_KEY")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def verify_api_key(api_key: str = Security(api_key_header)):
    """Verify API key if one is configured. Skip auth if no key is set (dev mode)."""
    if not API_KEY:
        return  # No key configured = dev mode, skip auth
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
```

Apply to all routes that modify data or trigger processing:

```python
@app.post("/api/upload", dependencies=[Depends(verify_api_key)])
@app.post("/api/products/process-all", dependencies=[Depends(verify_api_key)])
@app.post("/api/products/process-batch", dependencies=[Depends(verify_api_key)])
@app.post("/api/products/{id}/enrich", dependencies=[Depends(verify_api_key)])
# etc.
```

On the frontend side, add the API key to all fetch calls in `lib/api.ts`:

```typescript
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || "";

export async function fetchAPI(endpoint: string, options: RequestInit = {}) {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> || {}),
  };
  if (API_KEY) {
    headers["X-API-Key"] = API_KEY;
  }

  const res = await fetch(`${API_BASE_URL}${endpoint}`, {
    ...options,
    headers,
  });
  // ... rest
}
```

---

# PHASE 3: DATA QUALITY & PIPELINE INTELLIGENCE
*These improvements make the enrichment results more accurate and trustworthy.*

---

## 3.1 Smarter Image Filtering (Reduce Gemini Costs)

### Problem
The image cleaning pipeline calls Gemini Vision individually for up to 15 images per product ($0.005/image). For 10,000 products that's $750 just on image analysis. Many of these images could be filtered out with URL pattern matching alone — logos, icons, flags, and social media icons have predictable URL patterns.

### File to modify
- `backend/pipeline/extract.py`

### Instructions

Add a two-tier filtering system:

**Tier 1: URL-based filtering (free, instant)**
The existing `INVALID_IMG_PATTERNS` list (lines 29-50) is good. Expand it and apply it more aggressively BEFORE sending to Gemini:

```python
# Add these to INVALID_IMG_PATTERNS:
INVALID_IMG_PATTERNS_EXTENDED = INVALID_IMG_PATTERNS + [
    '/wp-includes/', '/wp-content/plugins/', '/wp-content/themes/',
    'cookie', 'gdpr', 'consent',
    'delivery-', 'shipping-', 'truck-', 'return-',
    'warranty', 'guarantee',
    'wishlist', 'compare-',
    'captcha', 'recaptcha',
    '/thumb/', '/thumbs/', '_thumb', '-thumb',  # thumbnails (often tiny)
    '50x50', '60x60', '70x70', '80x80', '100x100', '150x150',  # tiny size indicators
]
```

Add a size-based filter that rejects images with dimensions in the URL suggesting they are tiny:
```python
def _is_likely_thumbnail(url: str) -> bool:
    """Check if URL contains size indicators suggesting a tiny image."""
    import re
    # Match patterns like 50x50, 100x100 in URLs
    size_match = re.search(r'(\d+)x(\d+)', url)
    if size_match:
        w, h = int(size_match.group(1)), int(size_match.group(2))
        if w < 150 or h < 150:
            return True
    return False
```

**Tier 2: Only send ambiguous images to Gemini**

In `_clean_images_with_vision()`, pre-filter before the Gemini call:

```python
# Pre-filter with URL patterns (free)
candidates = [url for url in image_urls if not _is_likely_thumbnail(url)]

# Only send remaining ambiguous images to Gemini
if len(candidates) <= 3:
    # Few images = all are probably product images, skip Gemini
    return candidates

# Send to Gemini only the ones we're not sure about
described = describe_images(candidates, product_name)
```

This should reduce Gemini calls by 50-70%.

---

## 3.2 Idempotency Guard

### Problem
If an API call to `/enrich` is retried by the client (network hiccup, user double-click), the product runs through the entire pipeline again. This wastes API credits and can cause data inconsistency.

### File to modify
- `backend/main.py`

### Instructions

Add idempotency tracking to the `enrich_product()` endpoint:

```python
# Track active enrichment tasks
_active_enrichments: set = set()

@app.post("/api/products/{id}/enrich")
async def enrich_product(id: int, background_tasks: BackgroundTasks):
    if id in _active_enrichments:
        return {"message": "Enrichment already in progress for this product", "status": "already_running"}

    _active_enrichments.add(id)

    async def run_and_cleanup(pid):
        try:
            await run_full_enrichment(pid)
        finally:
            _active_enrichments.discard(pid)

    background_tasks.add_task(run_and_cleanup, id)
    return {"message": "Enrichment started"}
```

Do the same in `process_batch()`:

```python
async def process_batch(product_ids: List[int]):
    # Filter out already-processing products
    ids_to_process = [pid for pid in product_ids if pid not in _active_enrichments]
    # ... rest of function
```

---

## 3.3 Enhanced Extraction Prompts

### Problem
The extraction system prompt in `extract.py` (lines 176-210) does not instruct Claude on several common failure modes observed in product data extraction:
- Weight values that include packaging weight vs. product-only weight
- Dimension values swapped (height vs. length vs. width)
- "Net weight" vs. "Gross weight" distinction
- Volume given in product name contradicting scraped data

### File to modify
- `backend/pipeline/extract.py`

### Instructions

Enhance the system prompt (line 176) with additional rules:

Add these to the CRITICAL RULES section:

```
8. WEIGHT DISTINCTION:
   - "Net weight" or "Neto teža" = product only (preferred)
   - "Gross weight" or "Bruto teža" = product + packaging
   - If only one weight is given and unlabeled, use it but add notes="weight type unspecified"
   - If both are given, use net weight and note the gross weight in notes

9. DIMENSION ORDERING:
   - Height = tallest measurement when product is in its normal orientation
   - Length = longest horizontal measurement
   - Width = shorter horizontal measurement
   - If the page lists "LxWxH" or "DxŠxV" (Slovenian: dolžina x širina x višina), map accordingly
   - If only "dimensions: 30x20x15 cm" is given, assume LxWxH order

10. VOLUME VS NAME CHECK:
    - If the product name says "20L" but the page says "5L", flag it in notes
    - Trust the product name for volume when the page data seems to be per-unit or sample size

11. SLOVENIAN LANGUAGE CLUES:
    - "dolžina" = length, "širina" = width, "višina" = height
    - "teža" = weight, "prostornina" = volume
    - "brez embalaže" = without packaging, "z embalažo" = with packaging
```

---

## 3.4 Separate Enrichment Logs Table

### Problem
The `enrichment_log` column in the `products` table stores a JSON array as a TEXT blob. This means you can't query across products ("show me all Firecrawl failures this week") without loading every product's log. As the product count grows, this becomes increasingly slow and limits operational visibility.

### Files to modify
- `backend/db.py`
- `backend/main.py` (minor, for new endpoint)

### Instructions

**A. Add a new table in `db.py`'s `init_db()`**

```python
c.execute("""
    CREATE TABLE IF NOT EXISTS enrichment_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        timestamp TEXT NOT NULL,
        phase TEXT NOT NULL,
        step TEXT NOT NULL,
        status TEXT NOT NULL,
        details TEXT,
        credits_used TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (product_id) REFERENCES products(id)
    )
""")
c.execute("CREATE INDEX IF NOT EXISTS idx_logs_product ON enrichment_logs(product_id)")
c.execute("CREATE INDEX IF NOT EXISTS idx_logs_phase ON enrichment_logs(phase)")
c.execute("CREATE INDEX IF NOT EXISTS idx_logs_status ON enrichment_logs(status)")
```

**B. Update `append_log()` in `db.py`**

Write to BOTH the JSON column (for backward compatibility) and the new table:

```python
def append_log(product_id: int, entry: dict):
    """Append a log entry to both the legacy JSON column and the new logs table."""
    conn = get_db_connection()

    # Legacy: JSON column
    product = conn.execute("SELECT enrichment_log FROM products WHERE id = ?", (product_id,)).fetchone()
    existing = json.loads(product['enrichment_log']) if product and product['enrichment_log'] else []
    existing.append(entry)
    conn.execute("UPDATE products SET enrichment_log = ? WHERE id = ?", (json.dumps(existing), product_id))

    # New: structured table
    conn.execute("""
        INSERT INTO enrichment_logs (product_id, timestamp, phase, step, status, details, credits_used)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        product_id,
        entry.get("timestamp", datetime.now().isoformat()),
        entry.get("phase", "unknown"),
        entry.get("step", "unknown"),
        entry.get("status", "unknown"),
        entry.get("details"),
        json.dumps(entry.get("credits_used")) if entry.get("credits_used") else None,
    ))

    conn.commit()
    conn.close()
```

**C. Add query endpoints in `main.py`**

```python
@app.get("/api/logs/errors")
def get_recent_errors(limit: int = 50):
    """Get recent error logs across all products."""
    conn = get_db_connection()
    logs = conn.execute("""
        SELECT el.*, p.product_name, p.ean
        FROM enrichment_logs el
        JOIN products p ON el.product_id = p.id
        WHERE el.status = 'error'
        ORDER BY el.created_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(l) for l in logs]

@app.get("/api/logs/stats")
def get_log_stats():
    """Get aggregate stats from enrichment logs."""
    conn = get_db_connection()
    today = date.today().isoformat()

    stats = {}
    for phase in ['triage', 'search', 'extract', 'validate']:
        row = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors,
                SUM(CASE WHEN status = 'warning' THEN 1 ELSE 0 END) as warnings
            FROM enrichment_logs
            WHERE phase = ? AND date(created_at) = ?
        """, (phase, today)).fetchone()
        stats[phase] = dict(row)

    conn.close()
    return stats
```

---

# PHASE 4: UI/UX IMPROVEMENTS
*These changes make the tool more usable for the humans who review and manage enrichment.*

---

## 4.1 Human Review Workflow

### Problem
Products marked `needs_review` have no way for a reviewer to approve, edit, reject, or add notes from the UI. The reviewer has to look at the data, then... do nothing. There's no workflow.

### Files to modify
- `backend/main.py`
- `components/ProductDetail.tsx`

### Instructions

**A. Add review action endpoints in `backend/main.py`**

```python
class ReviewAction(BaseModel):
    action: Literal["approve", "reject", "edit"]
    notes: str | None = None
    field_edits: dict | None = None  # {"color": {"value": "black"}, "weight": {"value": 2.5}}

@app.post("/api/products/{id}/review")
async def review_product(id: int, review: ReviewAction):
    conn = get_db_connection()
    product = conn.execute("SELECT * FROM products WHERE id = ?", (id,)).fetchone()
    if not product:
        conn.close()
        raise HTTPException(status_code=404, detail="Product not found")

    if review.action == "approve":
        conn.execute(
            "UPDATE products SET status = 'done', current_step = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (id,)
        )
        append_log(id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "review", "step": "approved", "status": "success",
            "details": review.notes or "Manually approved by reviewer"
        })

    elif review.action == "reject":
        conn.execute(
            "UPDATE products SET status = 'pending', current_step = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (id,)
        )
        append_log(id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "review", "step": "rejected", "status": "warning",
            "details": review.notes or "Rejected by reviewer — will need re-processing"
        })

    elif review.action == "edit" and review.field_edits:
        # Apply manual edits to the validation result
        if product['validation_result']:
            val_result = json.loads(product['validation_result'])
            normalized = val_result.get('normalized_data', {})
            for field, edit in review.field_edits.items():
                if field in normalized and isinstance(normalized[field], dict):
                    normalized[field]['value'] = edit.get('value', normalized[field].get('value'))
                    normalized[field]['confidence'] = 'official'  # Manual = highest confidence
                    normalized[field]['notes'] = f"Manually edited by reviewer: {review.notes or ''}"
            val_result['normalized_data'] = normalized
            conn.execute(
                "UPDATE products SET validation_result = ?, status = 'done', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (json.dumps(val_result), id)
            )
        append_log(id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "review", "step": "edited", "status": "success",
            "details": f"Fields edited: {list(review.field_edits.keys())}. {review.notes or ''}"
        })

    conn.commit()
    conn.close()
    return {"message": f"Review action '{review.action}' applied"}
```

**B. Add review UI to `components/ProductDetail.tsx`**

Add a review panel that appears when `status === "needs_review"`:

- A prominent yellow banner: "This product needs human review"
- Show the `review_reason` from the validation report
- Show each flagged issue from `validation_result.report.issues`
- Three action buttons:
  1. **Approve** (green) — marks as `done`, no changes needed
  2. **Edit & Approve** (blue) — opens inline edit mode for flagged fields, then approves
  3. **Reject & Re-run** (red) — resets to `pending` for re-processing
- Optional notes textarea for the reviewer to add context

For the inline edit mode: render each enriched field as an editable input when in edit mode. When the user clicks "Save & Approve", POST to `/api/products/{id}/review` with `action: "edit"` and the field edits.

---

## 4.2 Bulk Operations & Filtering

### Problem
The product table has no filtering. If you have 500 products, you can't quickly find the 12 that need review or the 8 that failed. You also can't select all products of a certain status at once.

### File to modify
- `components/ProductTable.tsx`
- `backend/main.py`

### Instructions

**A. Add query parameters to the products endpoint**

In `backend/main.py`, enhance `get_products()`:

```python
@app.get("/api/products")
def get_products(
    status: str = None,
    search: str = None,
    page: int = 1,
    per_page: int = 50,
):
    conn = get_db_connection()

    query = "SELECT * FROM products WHERE 1=1"
    params = []

    if status:
        query += " AND status = ?"
        params.append(status)

    if search:
        query += " AND (product_name LIKE ? OR ean LIKE ? OR brand LIKE ?)"
        search_term = f"%{search}%"
        params.extend([search_term, search_term, search_term])

    # Count total for pagination
    count_query = query.replace("SELECT *", "SELECT COUNT(*) as c")
    total = conn.execute(count_query, params).fetchone()['c']

    query += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([per_page, (page - 1) * per_page])

    products = conn.execute(query, params).fetchall()
    conn.close()

    return {
        "products": [dict(p) for p in products],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page
    }
```

Note: This changes the response format from a list to an object with pagination. Update the frontend accordingly.

**B. Add filter tabs to `ProductTable.tsx`**

Add a row of filter tabs above the table:

```
[All (500)] [Pending (200)] [Processing (5)] [Done (280)] [Needs Review (12)] [Error (3)]
```

Each tab filters the product list by status. The active tab is highlighted. The count updates from the stats endpoint.

**C. Add a search bar**

Add a text input above the table that filters by product name or EAN as the user types (debounced, 300ms). Send the `search` query parameter to the backend.

**D. Add pagination**

Show page controls at the bottom: "Page 1 of 10" with previous/next buttons. Use the `page` and `per_page` query parameters.

---

## 4.3 Enrichment Progress Detail

### Problem
While a product is processing, the UI only shows the `current_step` text and a spinner. There's no sense of overall progress or ETA. Users can't tell if the pipeline is 10% or 90% done.

### File to modify
- `components/ProductTable.tsx`
- `components/ProductDetail.tsx`

### Instructions

Add a progress indicator based on the pipeline phase:

```
Phase weights:
- triage: 10%
- search: 25%
- extract: 50%
- validate: 15%
```

On the product detail page, show a horizontal progress bar:
- `classifying` → 5% (halfway through triage)
- `searching` → 22% (started search)
- `extracting` → 40% (started extract, heaviest phase)
- `validating` → 87% (almost done)

Calculate from the status and display as a thin progress bar under the status badge.

Also add estimated time remaining based on the `enrichment_log` timestamps:
```
Started 45s ago | Estimated ~30s remaining
```

---

## 4.4 Image Gallery Improvements

### Problem
The product detail page references an image proxy endpoint (`/api/image-proxy`) that needs to work properly. Product images from external URLs may have CORS restrictions, and displaying them directly can expose the user's IP to third-party servers.

### Files to modify
- `app/api/image-proxy/route.ts`
- `components/ProductDetail.tsx`

### Instructions

**A. Ensure the image proxy endpoint works**

The file `app/api/image-proxy/route.ts` should exist and proxy image requests through the Next.js server:

```typescript
import { NextRequest, NextResponse } from 'next/server';

export async function GET(request: NextRequest) {
  const url = request.nextUrl.searchParams.get('url');
  if (!url) {
    return NextResponse.json({ error: 'Missing url parameter' }, { status: 400 });
  }

  try {
    const response = await fetch(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (compatible; ShoppsterBot/1.0)',
      },
      signal: AbortSignal.timeout(10000), // 10s timeout
    });

    if (!response.ok) {
      return NextResponse.json({ error: 'Failed to fetch image' }, { status: response.status });
    }

    const contentType = response.headers.get('content-type') || 'image/jpeg';
    const buffer = await response.arrayBuffer();

    return new NextResponse(buffer, {
      headers: {
        'Content-Type': contentType,
        'Cache-Control': 'public, max-age=86400', // Cache for 24h
      },
    });
  } catch (error) {
    return NextResponse.json({ error: 'Image fetch failed' }, { status: 500 });
  }
}
```

**B. Enhance image display in `ProductDetail.tsx`**

- Show images in a grid layout (3 columns)
- Each image shows a loading skeleton while loading
- Clicking an image opens a lightbox with full-size view
- Show the source URL domain beneath each image (e.g., "bosch.com")
- Add a "Copy URL" button on hover for each image
- If an image fails to load, show a broken-image placeholder instead of nothing

---

## 4.5 Dashboard Enhancements

### Problem
The dashboard only shows basic counts (total, pending, done, error, needs_review, processing). For a production tool replacing 80 hours/month, operators need more operational visibility: daily throughput, success rates, average processing time, cost tracking.

### Files to modify
- `app/page.tsx`
- `backend/main.py`

### Instructions

**A. Enhance the stats endpoint**

Add more metrics to `get_dashboard_stats()`:

```python
@app.get("/api/dashboard/stats")
def get_dashboard_stats():
    conn = get_db_connection()
    today = date.today().isoformat()

    # Existing counts
    total = conn.execute("SELECT COUNT(*) as c FROM products").fetchone()['c']
    pending = conn.execute("SELECT COUNT(*) as c FROM products WHERE status = 'pending'").fetchone()['c']
    done = conn.execute("SELECT COUNT(*) as c FROM products WHERE status = 'done'").fetchone()['c']
    errors = conn.execute("SELECT COUNT(*) as c FROM products WHERE status = 'error'").fetchone()['c']
    needs_review = conn.execute("SELECT COUNT(*) as c FROM products WHERE status = 'needs_review'").fetchone()['c']
    processing = conn.execute("SELECT COUNT(*) as c FROM products WHERE status IN ('enriching','classifying','searching','extracting','validating')").fetchone()['c']

    # New: processed today
    done_today = conn.execute(
        "SELECT COUNT(*) as c FROM products WHERE status IN ('done', 'needs_review') AND date(updated_at) = ?",
        (today,)
    ).fetchone()['c']

    # New: confidence distribution (from done products)
    confidence_stats = {"official": 0, "third_party": 0, "inferred": 0, "not_found": 0}
    done_products = conn.execute(
        "SELECT validation_result FROM products WHERE status = 'done' AND validation_result IS NOT NULL"
    ).fetchall()
    total_fields = 0
    for p in done_products:
        try:
            vr = json.loads(p['validation_result'])
            nd = vr.get('normalized_data', {})
            for field_name, field_data in nd.items():
                if isinstance(field_data, dict) and 'confidence' in field_data:
                    conf = field_data['confidence']
                    if conf in confidence_stats:
                        confidence_stats[conf] += 1
                        total_fields += 1
        except:
            pass

    conn.close()

    return {
        "total": total, "pending": pending, "done": done,
        "errors": errors, "needs_review": needs_review, "processing": processing,
        "done_today": done_today,
        "confidence_distribution": confidence_stats,
        "total_enriched_fields": total_fields,
        "success_rate": round((done / max(done + errors, 1)) * 100, 1),
    }
```

**B. Display enhanced stats on the dashboard**

Add to the dashboard UI:
- A "Today" section showing products processed today
- A confidence distribution bar (horizontal stacked bar using colored divs, no chart library needed):
  ```
  [████ 45% official ██ 30% third_party █ 20% inferred ░ 5% not_found]
  ```
- Success rate percentage
- A "Cost Estimate" card showing estimated daily spend (products_today * $0.10 avg)

---

# PHASE 5: TESTING & OPERATIONAL EXCELLENCE
*These improvements ensure long-term reliability and prevent regressions.*

---

## 5.1 Integration Tests

### Problem
There are zero tests. Any change to the pipeline could silently break functionality. For a system replacing 80 hours/month of manual work, this is unacceptable.

### Files to create
- `backend/tests/conftest.py`
- `backend/tests/test_normalization.py`
- `backend/tests/test_llm_parsing.py`
- `backend/tests/test_upload.py`
- `backend/tests/test_pipeline_flow.py`

### Instructions

**A. Create `backend/tests/conftest.py`**

```python
import pytest
import os
import sys

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

@pytest.fixture
def test_db(tmp_path):
    """Create a temporary test database."""
    import db
    original_path = db.DB_PATH
    db.DB_PATH = str(tmp_path / "test.db")
    db.init_db()
    yield db.DB_PATH
    db.DB_PATH = original_path
```

**B. Create `backend/tests/test_normalization.py`**

Test all unit conversions:

```python
from utils.normalization import normalize_to_cm, normalize_to_kg, normalize_to_liters, normalize_field
from schemas import EnrichedField

def test_mm_to_cm():
    assert normalize_to_cm(230, "mm") == 23.0

def test_inches_to_cm():
    assert normalize_to_cm(10, "inches") == 25.4

def test_g_to_kg():
    assert normalize_to_kg(2300, "g") == 2.3

def test_lbs_to_kg():
    result = normalize_to_kg(5, "lbs")
    assert abs(result - 2.268) < 0.001

def test_ml_to_liters():
    assert normalize_to_liters(500, "ml") == 0.5

def test_normalize_field_preserves_metadata():
    field = EnrichedField(value=230, unit="mm", confidence="official", source_url="https://example.com")
    normalized = normalize_field(field, "length")
    assert normalized.value == 23.0
    assert normalized.unit == "cm"
    assert normalized.confidence == "official"
    assert normalized.source_url == "https://example.com"
    assert "230 mm" in normalized.notes

def test_normalize_field_handles_none():
    field = EnrichedField(value=None, unit=None)
    result = normalize_field(field, "length")
    assert result.value is None
```

**C. Create `backend/tests/test_llm_parsing.py`**

Test the JSON extraction logic (without making real API calls):

```python
from utils.llm import _extract_json_from_response

def test_raw_json():
    result = _extract_json_from_response('{"product_type": "standard_product", "brand": "Bosch"}')
    assert '"Bosch"' in result

def test_markdown_fenced():
    content = '```json\n{"product_type": "liquid"}\n```'
    result = _extract_json_from_response(content)
    assert '"liquid"' in result

def test_mixed_text_and_json():
    content = 'Here is the classification:\n\n{"product_type": "accessory", "brand": "Makita"}\n\nThis product is...'
    result = _extract_json_from_response(content)
    assert '"Makita"' in result

def test_no_json_returns_as_is():
    content = "I cannot analyze this product."
    result = _extract_json_from_response(content)
    assert result == content
```

**D. Create `backend/tests/test_upload.py`**

Test CSV upload validation:

```python
from schemas import validate_ean

def test_valid_ean13():
    valid, cleaned = validate_ean("4006066002288")
    assert valid is True
    assert cleaned == "4006066002288"

def test_valid_ean8():
    valid, cleaned = validate_ean("12345678")
    assert valid is True

def test_invalid_ean_letters():
    valid, cleaned = validate_ean("ABC123")
    assert valid is False

def test_invalid_ean_too_short():
    valid, cleaned = validate_ean("123")
    assert valid is False

def test_ean_with_spaces():
    valid, cleaned = validate_ean("4006 0660 02288")
    assert valid is True
    assert cleaned == "4006066002288"

def test_ean_unknown():
    valid, cleaned = validate_ean("UNKNOWN")
    assert valid is False
```

**E. Add pytest to `backend/requirements.txt`**

```
pytest
pytest-asyncio
```

**F. Run tests with:**
```bash
cd backend && python -m pytest tests/ -v
```

---

## 5.2 PostgreSQL Migration Path

### Problem
SQLite is single-writer, has no connection pooling, no replication, no backups, and locks on every write. With concurrent processing (Phase 2.3) and multiple users, SQLite will become the bottleneck.

### Instructions

This is a larger migration that should be done when you're ready to deploy beyond localhost. The migration path:

1. Replace `sqlite3` with `psycopg2` (or `asyncpg` for async) in `backend/db.py`
2. Replace `?` parameter placeholders with `%s` (PostgreSQL syntax)
3. Replace `AUTOINCREMENT` with `SERIAL` in the schema
4. Replace `TEXT DEFAULT CURRENT_TIMESTAMP` with `TIMESTAMP DEFAULT NOW()`
5. Replace `datetime('now', '-5 minutes')` with `NOW() - INTERVAL '5 minutes'`
6. Add connection pooling (e.g., `psycopg2.pool.ThreadedConnectionPool`)
7. Add `DATABASE_URL` environment variable

The SQL queries in this codebase are simple enough that the migration is mostly find-and-replace. No complex joins or SQLite-specific features are used.

---

# PHASE 6: ADDITIONAL IMPROVEMENTS (NICE-TO-HAVE)

---

## 6.1 Firecrawl Extract Mode

### Problem
Currently, pages are scraped to markdown (Firecrawl scrape mode) and then Claude extracts structured data from the markdown. Firecrawl has an "Extract" mode that takes a Pydantic schema directly and returns structured JSON — this could eliminate one LLM call per URL.

### File to modify
- `backend/pipeline/extract.py`

### Instructions

Try Firecrawl's extract mode first, fall back to scrape+Claude if it fails:

```python
try:
    # Try Firecrawl extract mode (cheaper, faster)
    extracted = firecrawl.extract(
        url,
        schema=TargetSchema.model_json_schema(),
        formats=['json']
    )
    if extracted and extracted.get('data'):
        extraction = TargetSchema.model_validate(extracted['data'])
        extractions.append(extraction)
        continue  # Skip Claude call
except:
    pass  # Fall back to scrape + Claude

# Existing scrape + Claude logic...
```

---

## 6.2 Webhook / Notification on Completion

### Problem
When processing a large batch (100+ products), the user has to keep the browser open and watch. There's no notification when the batch completes.

### File to modify
- `backend/main.py`

### Instructions

Add an optional webhook callback:

```python
class BatchProcessRequest(BaseModel):
    product_ids: List[int]
    webhook_url: str | None = None  # Optional callback URL

async def process_batch(product_ids: List[int], webhook_url: str = None):
    # ... existing processing logic ...

    # When batch completes, notify
    if webhook_url:
        import httpx
        async with httpx.AsyncClient() as client:
            try:
                await client.post(webhook_url, json={
                    "event": "batch_complete",
                    "total": len(product_ids),
                    "done": done_count,
                    "errors": error_count,
                    "needs_review": review_count,
                })
            except:
                logger.warning(f"Webhook notification failed: {webhook_url}")
```

This enables future integrations (Slack notifications, email alerts, etc.) without modifying the core pipeline.

---

## 6.3 Export Format Improvements

### Problem
The XLSX export includes raw field names and is not formatted for SAP import. Shoppster needs specific column names and formats.

### File to modify
- `backend/main.py` (the `_build_export_row` function)

### Instructions

Add a configurable export template:

```python
# SAP-ready column mapping
SAP_EXPORT_COLUMNS = {
    "EAN": "ean",
    "Naziv": "product_name",
    "Blagovna znamka": "brand",
    "Tip izdelka": "product_type",
    "Višina (cm)": "height",
    "Širina (cm)": "width",
    "Dolžina (cm)": "length",
    "Teža (kg)": "weight",
    "Prostornina (L)": "volume",
    "Premer (cm)": "diameter",
    "Barva": "color",
    "Država izvora": "country_of_origin",
    "Slika URL": "image_url",
    "Status kakovosti": "quality_status",
    "Zaupanje": "confidence",
}
```

Add a query parameter to the export endpoint: `?format=sap` vs `?format=default`.

---

## 6.4 Delete Products

### Problem
There is no way to delete products from the database through the UI. If bad data was uploaded, the only option is to reset it.

### Files to modify
- `backend/main.py`
- `components/ProductTable.tsx`

### Instructions

**A. Add delete endpoint**

```python
@app.delete("/api/products/{id}", dependencies=[Depends(verify_api_key)])
async def delete_product(id: int):
    conn = get_db_connection()
    product = conn.execute("SELECT status FROM products WHERE id = ?", (id,)).fetchone()
    if not product:
        conn.close()
        raise HTTPException(status_code=404, detail="Product not found")

    ACTIVE_STATUSES = ['enriching', 'classifying', 'searching', 'extracting', 'validating']
    if product['status'] in ACTIVE_STATUSES:
        conn.close()
        raise HTTPException(status_code=409, detail="Cannot delete product while it is being processed")

    conn.execute("DELETE FROM enrichment_logs WHERE product_id = ?", (id,))
    conn.execute("DELETE FROM products WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return {"message": f"Product {id} deleted"}

@app.delete("/api/products/batch", dependencies=[Depends(verify_api_key)])
async def delete_products_batch(request: BatchProcessRequest):
    """Delete multiple products at once."""
    conn = get_db_connection()
    deleted = 0
    for pid in request.product_ids:
        product = conn.execute("SELECT status FROM products WHERE id = ?", (pid,)).fetchone()
        if product and product['status'] not in ['enriching', 'classifying', 'searching', 'extracting', 'validating']:
            conn.execute("DELETE FROM enrichment_logs WHERE product_id = ?", (pid,))
            conn.execute("DELETE FROM products WHERE id = ?", (pid,))
            deleted += 1
    conn.commit()
    conn.close()
    return {"message": f"Deleted {deleted} products"}
```

**B. Add delete button to the floating action bar in `ProductTable.tsx`**

Add a "Delete" button (red, with confirmation dialog) next to the "Run Enrichment" button in the floating action bar that appears when products are selected.

---

# IMPLEMENTATION CHECKLIST

Use this to track progress:

- [ ] **Phase 1.1** — Stuck product recovery + double-run guard
- [ ] **Phase 1.2** — Retry logic with exponential backoff (`utils/retry.py`)
- [ ] **Phase 1.3** — API call timeouts on all external calls
- [ ] **Phase 1.4** — Robust LLM JSON parsing
- [ ] **Phase 1.5** — Input validation on CSV upload
- [x] **Phase 1.6** — Cost guardrails (daily limits, batch limits)
- [ ] **Phase 1.7** — Database connection safety (context manager)
- [ ] **Phase 2.1** — Environment-based configuration
- [ ] **Phase 2.2** — Structured logging (replace all `print()`)
- [ ] **Phase 2.3** — Concurrent processing with semaphore
- [ ] **Phase 2.4** — API key authentication
- [x] **Phase 3.1** — Smarter image filtering
- [ ] **Phase 3.2** — Idempotency guard
- [ ] **Phase 3.3** — Enhanced extraction prompts
- [ ] **Phase 3.4** — Separate enrichment logs table
- [ ] **Phase 4.1** — Human review workflow
- [ ] **Phase 4.2** — Bulk operations & filtering & pagination
- [ ] **Phase 4.3** — Enrichment progress detail
- [ ] **Phase 4.4** — Image gallery / proxy fixes
- [x] **Phase 4.5** — Dashboard enhancements
- [ ] **Phase 5.1** — Integration tests
- [ ] **Phase 5.2** — PostgreSQL migration path (when ready)
- [ ] **Phase 6.1** — Firecrawl extract mode
- [ ] **Phase 6.2** — Webhook notifications
- [ ] **Phase 6.3** — SAP-ready export format
- [ ] **Phase 6.4** — Delete products functionality
