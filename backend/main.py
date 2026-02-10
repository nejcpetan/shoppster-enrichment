from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import pandas as pd
import io
import json
import sqlite3
import os
import asyncio
from datetime import datetime
from typing import List
from pydantic import BaseModel
from db import get_db_connection, init_db
from schemas import ProductResponse
from pipeline.triage import classify_product
from pipeline.search import search_product
from pipeline.extract import extract_product_data
from pipeline.validate import validate_product

app = FastAPI()

# Add CORS middleware
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
            
        products_to_insert = []
        conn = get_db_connection()
        c = conn.cursor()
        
        for _, row in df.iterrows():
            row_dict = json.loads(row.to_json())
            
            # Flexible Column Mapping
            ean = str(row_dict.get('EAN', row_dict.get('ean', 'UNKNOWN')))
            name = str(row_dict.get('Name', row_dict.get('name', row_dict.get('Product Name', row_dict.get('naziv', 'UNKNOWN')))))
            brand = str(row_dict.get('Brand', row_dict.get('brand', None)))
            weight = str(row_dict.get('Weight', row_dict.get('weight', None)))
            
            if ean == 'UNKNOWN' and name == 'UNKNOWN':
                continue # Skip empty rows

            c.execute("""
                INSERT INTO products (ean, product_name, brand, weight, original_data)
                VALUES (?, ?, ?, ?, ?)
            """, (ean, name, brand, weight, json.dumps(row_dict)))
            
        conn.commit()
        conn.close()
        
        return {"message": f"Successfully processed {len(df)} products"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/products", response_model=List[ProductResponse])
def get_products():
    conn = get_db_connection()
    products = conn.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(p) for p in products]

async def run_full_enrichment(product_id: int):
    """
    Orchestrator for the full enrichment pipeline.
    """
    try:
        print(f"Starting enrichment for product {product_id}")
        
        # Set status to enriching immediately
        conn = get_db_connection()
        conn.execute("UPDATE products SET status = 'enriching' WHERE id = ?", (product_id,))
        conn.commit()
        conn.close()
        
        # Phase 1
        print(f"[{product_id}] Phase 1: Classification...")
        await classify_product(product_id)
        
        # Phase 2
        print(f"[{product_id}] Phase 2: Search...")
        await search_product(product_id)
        
        # Phase 3
        print(f"[{product_id}] Phase 3: Extraction...")
        await extract_product_data(product_id)
        
        # Phase 4
        print(f"[{product_id}] Phase 4: Validation...")
        await validate_product(product_id)
        
        print(f"Enrichment completed for product {product_id}")
        
    except Exception as e:
        print(f"Enrichment failed for {product_id}: {e}")
        conn = get_db_connection()
        # Log the error to enrichment_log
        product_row = conn.execute("SELECT enrichment_log FROM products WHERE id = ?", (product_id,)).fetchone()
        existing_log = json.loads(product_row['enrichment_log']) if product_row and product_row['enrichment_log'] else []
        existing_log.append({
            "timestamp": datetime.now().isoformat(),
            "phase": "pipeline", "step": "error", "status": "error",
            "details": str(e)
        })
        conn.execute(
            "UPDATE products SET status = 'error', enrichment_log = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", 
            (json.dumps(existing_log), product_id)
        )
        conn.commit()
        conn.close()

async def process_batch(product_ids: List[int]):
    """
    Process products with limited concurrency.
    """
    for pid in product_ids:
        await run_full_enrichment(pid)
        await asyncio.sleep(0.5)

# Static sub-paths MUST come before the parameterized {id} route
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
    """
    Triggers enrichment for specific list of product IDs.
    """
    if not request.product_ids:
        return {"message": "No product IDs provided"}
        
    background_tasks.add_task(process_batch, request.product_ids)
    return {"message": f"Started processing {len(request.product_ids)} products"}

# Parameterized routes AFTER static ones to avoid "process-batch" matching as {id}
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

@app.post("/api/products/{id}/search")
async def trigger_search(id: int, background_tasks: BackgroundTasks):
    background_tasks.add_task(search_product, id)
    return {"message": "Search started"}

@app.post("/api/products/{id}/extract")
async def trigger_extract(id: int, background_tasks: BackgroundTasks):
    background_tasks.add_task(extract_product_data, id)
    return {"message": "Extraction started"}

@app.post("/api/products/{id}/validate")
async def trigger_validate(id: int, background_tasks: BackgroundTasks):
    background_tasks.add_task(validate_product, id)
    return {"message": "Validation started"}

@app.post("/api/products/{id}/classify")
async def trigger_classify(id: int, background_tasks: BackgroundTasks):
    """Run Phase 1 (Classification) only."""
    background_tasks.add_task(classify_product, id)
    return {"message": "Classification started"}

@app.post("/api/products/{id}/reset")
async def reset_product(id: int):
    """Reset a product to pending so it can be re-processed."""
    conn = get_db_connection()
    product = conn.execute("SELECT * FROM products WHERE id = ?", (id,)).fetchone()
    if not product:
        conn.close()
        raise HTTPException(status_code=404, detail="Product not found")
    conn.execute("""
        UPDATE products 
        SET status = 'pending', 
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
    """Export a single product's enriched data as XLSX."""
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

def _build_export_row(row: dict) -> dict:
    """Build a flat export row from a product database row."""
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


@app.get("/api/dashboard/stats")
def get_dashboard_stats():
    """Summary statistics for the dashboard cards."""
    conn = get_db_connection()
    total = conn.execute("SELECT COUNT(*) as c FROM products").fetchone()['c']
    pending = conn.execute("SELECT COUNT(*) as c FROM products WHERE status = 'pending'").fetchone()['c']
    done = conn.execute("SELECT COUNT(*) as c FROM products WHERE status = 'done'").fetchone()['c']
    errors = conn.execute("SELECT COUNT(*) as c FROM products WHERE status = 'error'").fetchone()['c']
    needs_review = conn.execute("SELECT COUNT(*) as c FROM products WHERE status = 'needs_review'").fetchone()['c']
    processing = conn.execute("SELECT COUNT(*) as c FROM products WHERE status IN ('enriching','classifying','searching','extracting','validating')").fetchone()['c']
    conn.close()
    return {
        "total": total,
        "pending": pending,
        "done": done,
        "errors": errors,
        "needs_review": needs_review,
        "processing": processing
    }


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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
