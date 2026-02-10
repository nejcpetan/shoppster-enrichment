from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import pandas as pd
import io
import json
import os
import logging
import asyncio
from datetime import datetime
from typing import List
from pydantic import BaseModel
from db import get_db_connection, init_db
from schemas import ProductResponse
from graph import enrichment_pipeline, ProductState

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
def startup_event():
    init_db()

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

# --- Enrichment Pipeline ---

async def run_full_enrichment(product_id: int):
    """
    Invokes the LangGraph enrichment pipeline for a single product.
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

        # Set initial status
        conn = get_db_connection()
        conn.execute(
            "UPDATE products SET status = 'enriching', current_step = 'Initializing pipeline...', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (product_id,)
        )
        conn.commit()
        conn.close()

        # Build initial state
        initial_state: ProductState = {
            "product_id": product_id,
            "has_brand": False,
            "has_search_results": False,
            "error": None,
        }

        # Run the LangGraph pipeline
        result = await enrichment_pipeline.ainvoke(initial_state)

        if result.get("error"):
            raise Exception(result["error"])

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


async def process_batch(product_ids: List[int]):
    """Process products sequentially (to respect API rate limits)."""
    for pid in product_ids:
        await run_full_enrichment(pid)
        await asyncio.sleep(0.5)

# --- Static sub-paths FIRST (before parameterized {id} routes) ---

@app.post("/api/products/process-all")
async def process_all_products(background_tasks: BackgroundTasks):
    conn = get_db_connection()
    rows = conn.execute("SELECT id FROM products WHERE status IN ('pending', 'needs_review')").fetchall()
    conn.close()

    product_ids = [row['id'] for row in rows]
    if not product_ids:
        return {"message": "No pending products found"}

    background_tasks.add_task(process_batch, product_ids)
    return {"message": f"Started processing {len(product_ids)} products"}

@app.post("/api/products/process-batch")
async def process_batch_products(request: BatchProcessRequest, background_tasks: BackgroundTasks):
    if not request.product_ids:
        return {"message": "No product IDs provided"}
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
    background_tasks.add_task(run_full_enrichment, id)
    return {"message": "Enrichment started"}

@app.post("/api/products/{id}/classify")
async def trigger_classify(id: int, background_tasks: BackgroundTasks):
    """Run Phase 1 (Triage) only."""
    from pipeline.triage import triage_node
    async def run():
        conn = get_db_connection()
        conn.execute("UPDATE products SET status = 'classifying', current_step = 'Starting classification...', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (id,))
        conn.commit()
        conn.close()
        await triage_node({"product_id": id, "has_brand": False, "has_search_results": False, "error": None})
    background_tasks.add_task(run)
    return {"message": "Classification started"}

@app.post("/api/products/{id}/search")
async def trigger_search(id: int, background_tasks: BackgroundTasks):
    from pipeline.search import search_node
    async def run():
        await search_node({"product_id": id, "has_brand": True, "has_search_results": False, "error": None})
    background_tasks.add_task(run)
    return {"message": "Search started"}

@app.post("/api/products/{id}/extract")
async def trigger_extract(id: int, background_tasks: BackgroundTasks):
    from pipeline.extract import extract_node
    async def run():
        await extract_node({"product_id": id, "has_brand": True, "has_search_results": True, "error": None})
    background_tasks.add_task(run)
    return {"message": "Extraction started"}

@app.post("/api/products/{id}/validate")
async def trigger_validate(id: int, background_tasks: BackgroundTasks):
    from pipeline.validate import validate_node
    async def run():
        await validate_node({"product_id": id, "has_brand": True, "has_search_results": True, "error": None})
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
