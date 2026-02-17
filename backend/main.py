from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import pandas as pd
import io
import json
import os
import logging
import asyncio
import time
from datetime import datetime
from typing import List
from pydantic import BaseModel
from db import get_db_connection, init_db, update_step
from schemas import ProductResponse
from graph import enrichment_pipeline, ProductState
from events import event_bus, format_sse

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(name)-20s │ %(message)s",
    datefmt="%H:%M:%S",
)
# Quiet down noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("anthropic").setLevel(logging.WARNING)
logging.getLogger("google").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

logger = logging.getLogger("pipeline.api")

app = FastAPI(title="Product Enrichment Engine", version="2.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class BatchProcessRequest(BaseModel):
    product_ids: List[int]

@app.on_event("startup")
async def startup_event():
    init_db()
    # Register main event loop with event bus for thread-safe SSE delivery
    event_bus.set_loop(asyncio.get_running_loop())

# --- Helper: run async pipeline in a separate thread ---

def _run_async_in_thread(async_fn, *args):
    """Run an async function in a new event loop in the current thread.
    This isolates blocking I/O (SQLite, sync HTTP) from the main event loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(async_fn(*args))
    finally:
        loop.close()

def _publish_final_status(product_id: int):
    """Read current status from DB and publish SSE event.
    Needed because pipeline nodes write terminal status directly to DB."""
    conn = get_db_connection()
    row = conn.execute("SELECT status, current_step FROM products WHERE id = ?", (product_id,)).fetchone()
    conn.close()
    if row:
        event_bus.publish_product_event(product_id, {
            "type": "status",
            "status": row["status"],
            "current_step": row["current_step"],
        })

# --- Upload ---

@app.post("/api/upload")
async def upload_products(file: UploadFile = File(...)):
    if not file.filename.endswith(('.csv', '.xlsx')):
        raise HTTPException(status_code=400, detail="Invalid file format")

    contents = await file.read()

    try:
        if file.filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(contents))
        else:
            df = pd.read_excel(io.BytesIO(contents))

        conn = get_db_connection()
        c = conn.cursor()

        for _, row in df.iterrows():
            row_dict = json.loads(row.to_json())
            ean = str(row_dict.get('EAN', row_dict.get('ean', 'UNKNOWN')))
            name = str(row_dict.get('Name', row_dict.get('name', row_dict.get('Product Name', row_dict.get('naziv', 'UNKNOWN')))))
            brand = str(row_dict.get('Brand', row_dict.get('brand', None)))
            weight = str(row_dict.get('Weight', row_dict.get('weight', None)))

            if ean == 'UNKNOWN' and name == 'UNKNOWN':
                continue

            c.execute("""
                INSERT INTO products (ean, product_name, brand, weight, original_data)
                VALUES (?, ?, ?, ?, ?)
            """, (ean, name, brand, weight, json.dumps(row_dict)))

        conn.commit()
        conn.close()

        return {"message": f"Successfully processed {len(df)} products"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Product Listing ---

@app.get("/api/products", response_model=List[ProductResponse])
def get_products():
    conn = get_db_connection()
    products = conn.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(p) for p in products]

# --- SSE Endpoints ---

@app.get("/api/events/products")
async def sse_products():
    """Global SSE stream — emits status/log events for all products."""
    async def event_generator():
        queue = event_bus.subscribe("products")
        try:
            yield format_sse("connected", {"message": "Connected to global product stream"})
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    event_type = event.get("type", "status")
                    yield format_sse(event_type, event)
                except asyncio.TimeoutError:
                    # Keep-alive comment
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            event_bus.unsubscribe("products", queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/events/products/{product_id}")
async def sse_product(product_id: int):
    """Per-product SSE stream — emits status/log events for a single product."""
    async def event_generator():
        # Send initial snapshot
        conn = get_db_connection()
        product = conn.execute("SELECT id, status, current_step, enrichment_log FROM products WHERE id = ?", (product_id,)).fetchone()
        conn.close()
        if product:
            snapshot = {
                "product_id": product_id,
                "status": product["status"],
                "current_step": product["current_step"],
            }
            yield format_sse("snapshot", snapshot)

        channel = f"product:{product_id}"
        queue = event_bus.subscribe(channel)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    event_type = event.get("type", "status")
                    yield format_sse(event_type, event)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            event_bus.unsubscribe(channel, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# --- Enrichment Pipeline ---
# These functions are SYNC and run in a thread (via BackgroundTasks)
# to keep the main event loop free for SSE streams and API requests.

def run_full_enrichment(product_id: int):
    """
    Invokes the LangGraph enrichment pipeline for a single product.
    Runs in a thread — all blocking I/O is isolated from the main event loop.
    """
    try:
        # Load product name for logging
        conn = get_db_connection()
        product_row = conn.execute("SELECT product_name FROM products WHERE id = ?", (product_id,)).fetchone()
        product_name = product_row['product_name'] if product_row else f"ID:{product_id}"
        conn.close()

        logger.info(f"")
        logger.info(f"{'='*60}")
        logger.info(f"[Product {product_id}] ▶ PIPELINE START — {product_name}")
        logger.info(f"{'='*60}")

        # Set initial status (publishes SSE event via thread-safe event bus)
        update_step(product_id, "enriching", "Initializing pipeline...")

        # Build initial state
        initial_state: ProductState = {
            "product_id": product_id,
            "has_brand": False,
            "has_search_results": False,
            "error": None,
        }

        # Run the async LangGraph pipeline in its own event loop (in this thread)
        result = _run_async_in_thread(enrichment_pipeline.ainvoke, initial_state)

        if result.get("error"):
            raise Exception(result["error"])

        # Publish final status for SSE clients.
        # Pipeline nodes (e.g. validate) write terminal status directly to DB
        # without calling update_step(), so we read it back and publish here.
        conn = get_db_connection()
        final_row = conn.execute("SELECT status, current_step FROM products WHERE id = ?", (product_id,)).fetchone()
        conn.close()
        if final_row:
            event_bus.publish_product_event(product_id, {
                "type": "status",
                "status": final_row["status"],
                "current_step": final_row["current_step"],
            })

        logger.info(f"[Product {product_id}] ✓ PIPELINE COMPLETE — {product_name}")
        logger.info(f"{'='*60}")

    except Exception as e:
        logger.error(f"[Product {product_id}] ✗ PIPELINE FAILED — {e}")
        conn = get_db_connection()
        product_row = conn.execute("SELECT enrichment_log FROM products WHERE id = ?", (product_id,)).fetchone()
        existing_log = json.loads(product_row['enrichment_log']) if product_row and product_row['enrichment_log'] else []
        existing_log.append({
            "timestamp": datetime.now().isoformat(),
            "phase": "pipeline", "step": "error", "status": "error",
            "details": str(e)
        })
        conn.execute(
            "UPDATE products SET status = 'error', current_step = NULL, enrichment_log = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(existing_log), product_id)
        )
        conn.commit()
        conn.close()

        # Publish error event
        event_bus.publish_product_event(product_id, {
            "type": "status",
            "status": "error",
            "current_step": None,
        })


def process_batch(product_ids: List[int]):
    """Process products sequentially (to respect API rate limits).
    Runs in a thread via BackgroundTasks."""
    for pid in product_ids:
        run_full_enrichment(pid)
        time.sleep(0.5)

# --- Static sub-paths FIRST (before parameterized {id} routes) ---

@app.post("/api/products/process-all")
async def process_all_products(background_tasks: BackgroundTasks):
    conn = get_db_connection()
    rows = conn.execute("SELECT id FROM products WHERE status IN ('pending', 'needs_review')").fetchall()
    product_ids = [row['id'] for row in rows]

    if not product_ids:
        conn.close()
        return {"message": "No pending products found"}

    # Set status immediately before background task — fixes race condition
    for pid in product_ids:
        conn.execute(
            "UPDATE products SET status = 'enriching', current_step = 'Queued for processing...', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (pid,)
        )
    conn.commit()
    conn.close()

    # Publish SSE events for all products
    for pid in product_ids:
        event_bus.publish_product_event(pid, {
            "type": "status",
            "status": "enriching",
            "current_step": "Queued for processing...",
        })

    background_tasks.add_task(process_batch, product_ids)
    return {"message": f"Started processing {len(product_ids)} products"}

@app.post("/api/products/process-batch")
async def process_batch_products(request: BatchProcessRequest, background_tasks: BackgroundTasks):
    if not request.product_ids:
        return {"message": "No product IDs provided"}

    # Set status immediately before background task — fixes race condition
    conn = get_db_connection()
    for pid in request.product_ids:
        conn.execute(
            "UPDATE products SET status = 'enriching', current_step = 'Queued for processing...', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (pid,)
        )
    conn.commit()
    conn.close()

    # Publish SSE events for all products
    for pid in request.product_ids:
        event_bus.publish_product_event(pid, {
            "type": "status",
            "status": "enriching",
            "current_step": "Queued for processing...",
        })

    background_tasks.add_task(process_batch, request.product_ids)
    return {"message": f"Started processing {len(request.product_ids)} products"}

# --- Parameterized routes AFTER static ones ---

@app.get("/api/products/{id}", response_model=ProductResponse)
def get_product(id: int):
    conn = get_db_connection()
    product = conn.execute("SELECT * FROM products WHERE id = ?", (id,)).fetchone()
    conn.close()

    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    return dict(product)

@app.post("/api/products/{id}/enrich")
async def enrich_product(id: int, background_tasks: BackgroundTasks):
    # Set status immediately — fixes race condition
    conn = get_db_connection()
    conn.execute(
        "UPDATE products SET status = 'enriching', current_step = 'Starting enrichment...', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (id,)
    )
    conn.commit()
    conn.close()
    event_bus.publish_product_event(id, {
        "type": "status",
        "status": "enriching",
        "current_step": "Starting enrichment...",
    })

    background_tasks.add_task(run_full_enrichment, id)
    return {"message": "Enrichment started"}

@app.post("/api/products/{id}/classify")
async def trigger_classify(id: int, background_tasks: BackgroundTasks):
    """Run Phase 1 (Triage) only."""
    # Set status immediately
    conn = get_db_connection()
    conn.execute(
        "UPDATE products SET status = 'classifying', current_step = 'Starting classification...', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (id,)
    )
    conn.commit()
    conn.close()
    event_bus.publish_product_event(id, {
        "type": "status",
        "status": "classifying",
        "current_step": "Starting classification...",
    })

    from pipeline.triage import triage_node
    def run():
        _run_async_in_thread(
            triage_node,
            {"product_id": id, "has_brand": False, "has_search_results": False, "error": None}
        )
        _publish_final_status(id)
    background_tasks.add_task(run)
    return {"message": "Classification started"}

@app.post("/api/products/{id}/search")
async def trigger_search(id: int, background_tasks: BackgroundTasks):
    # Set status immediately
    conn = get_db_connection()
    conn.execute(
        "UPDATE products SET status = 'searching', current_step = 'Starting search...', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (id,)
    )
    conn.commit()
    conn.close()
    event_bus.publish_product_event(id, {
        "type": "status",
        "status": "searching",
        "current_step": "Starting search...",
    })

    from pipeline.search import search_node
    def run():
        _run_async_in_thread(
            search_node,
            {"product_id": id, "has_brand": True, "has_search_results": False, "error": None}
        )
        _publish_final_status(id)
    background_tasks.add_task(run)
    return {"message": "Search started"}

@app.post("/api/products/{id}/extract")
async def trigger_extract(id: int, background_tasks: BackgroundTasks):
    # Set status immediately
    conn = get_db_connection()
    conn.execute(
        "UPDATE products SET status = 'extracting', current_step = 'Starting extraction...', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (id,)
    )
    conn.commit()
    conn.close()
    event_bus.publish_product_event(id, {
        "type": "status",
        "status": "extracting",
        "current_step": "Starting extraction...",
    })

    from pipeline.extract import extract_node
    def run():
        _run_async_in_thread(
            extract_node,
            {"product_id": id, "has_brand": True, "has_search_results": True, "error": None}
        )
        _publish_final_status(id)
    background_tasks.add_task(run)
    return {"message": "Extraction started"}

@app.post("/api/products/{id}/validate")
async def trigger_validate(id: int, background_tasks: BackgroundTasks):
    # Set status immediately
    conn = get_db_connection()
    conn.execute(
        "UPDATE products SET status = 'validating', current_step = 'Starting validation...', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (id,)
    )
    conn.commit()
    conn.close()
    event_bus.publish_product_event(id, {
        "type": "status",
        "status": "validating",
        "current_step": "Starting validation...",
    })

    from pipeline.validate import validate_node
    def run():
        _run_async_in_thread(
            validate_node,
            {"product_id": id, "has_brand": True, "has_search_results": True, "error": None}
        )
        _publish_final_status(id)
    background_tasks.add_task(run)
    return {"message": "Validation started"}

@app.post("/api/products/{id}/reset")
async def reset_product(id: int):
    conn = get_db_connection()
    product = conn.execute("SELECT * FROM products WHERE id = ?", (id,)).fetchone()
    if not product:
        conn.close()
        raise HTTPException(status_code=404, detail="Product not found")
    conn.execute("""
        UPDATE products
        SET status = 'pending', current_step = NULL,
            classification_result = NULL, search_result = NULL,
            extraction_result = NULL, validation_result = NULL,
            enrichment_log = NULL, product_type = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (id,))
    conn.commit()
    conn.close()

    # Publish reset event
    event_bus.publish_product_event(id, {
        "type": "status",
        "status": "pending",
        "current_step": None,
    })

    return {"message": f"Product {id} reset to pending"}

@app.get("/api/products/{id}/export")
def export_single_product(id: int):
    conn = get_db_connection()
    product = conn.execute("SELECT * FROM products WHERE id = ?", (id,)).fetchone()
    conn.close()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    row = dict(product)
    export_row = _build_export_row(row)

    df = pd.DataFrame([export_row])
    stream = io.BytesIO()
    df.to_excel(stream, index=False)
    stream.seek(0)

    filename = f"product_{id}_{row['ean']}.xlsx"
    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

# --- Dashboard ---

@app.get("/api/dashboard/stats")
def get_dashboard_stats():
    conn = get_db_connection()
    total = conn.execute("SELECT COUNT(*) as c FROM products").fetchone()['c']
    pending = conn.execute("SELECT COUNT(*) as c FROM products WHERE status = 'pending'").fetchone()['c']
    done = conn.execute("SELECT COUNT(*) as c FROM products WHERE status = 'done'").fetchone()['c']
    errors = conn.execute("SELECT COUNT(*) as c FROM products WHERE status = 'error'").fetchone()['c']
    needs_review = conn.execute("SELECT COUNT(*) as c FROM products WHERE status = 'needs_review'").fetchone()['c']
    processing = conn.execute("SELECT COUNT(*) as c FROM products WHERE status IN ('enriching','classifying','searching','extracting','validating')").fetchone()['c']
    conn.close()
    return {
        "total": total, "pending": pending, "done": done,
        "errors": errors, "needs_review": needs_review, "processing": processing
    }

# --- Export All ---

@app.get("/api/export")
def export_products():
    conn = get_db_connection()
    products = conn.execute("SELECT * FROM products").fetchall()
    conn.close()

    export_data = [_build_export_row(dict(p)) for p in products]

    df = pd.DataFrame(export_data)
    stream = io.BytesIO()
    df.to_excel(stream, index=False)
    stream.seek(0)

    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=enriched_products.xlsx"}
    )


def _build_export_row(row: dict) -> dict:
    export_row = {
        "ID": row['id'],
        "EAN": row['ean'],
        "Name": row['product_name'],
        "Status": row['status'],
    }

    enriched_data = {}
    if row.get('validation_result'):
        try:
            val_res = json.loads(row['validation_result'])
            enriched_data = val_res.get('normalized_data', {})
            export_row['Quality Status'] = val_res.get('report', {}).get('overall_quality', 'unknown')
        except: pass
    elif row.get('extraction_result'):
        try:
            enriched_data = json.loads(row['extraction_result'])
            export_row['Quality Status'] = 'raw_extraction'
        except: pass

    for field in ['height', 'width', 'length', 'weight', 'volume', 'color', 'country_of_origin', 'diameter']:
        val = None
        unit = None
        if field in enriched_data:
            field_obj = enriched_data[field]
            if field_obj and isinstance(field_obj, dict):
                val = field_obj.get('value')
                unit = field_obj.get('unit')

        export_row[f"{field.replace('_', ' ').title()}"] = val
        export_row[f"{field.replace('_', ' ').title()} Unit"] = unit

    if row.get('classification_result'):
        try:
            cls_res = json.loads(row['classification_result'])
            export_row['Type'] = cls_res.get('product_type')
            export_row['Brand'] = cls_res.get('brand')
        except: pass

    return export_row


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
