import json
from datetime import datetime
from db import get_db_connection
from schemas import ProductClassification
from utils.llm import classify_with_schema


def _append_log(product_id: int, entry: dict):
    """Append a log entry to the product's enrichment_log."""
    conn = get_db_connection()
    product = conn.execute("SELECT enrichment_log FROM products WHERE id = ?", (product_id,)).fetchone()
    existing = json.loads(product['enrichment_log']) if product and product['enrichment_log'] else []
    existing.append(entry)
    conn.execute("UPDATE products SET enrichment_log = ? WHERE id = ?", (json.dumps(existing), product_id))
    conn.commit()
    conn.close()


async def classify_product(product_id: int):
    """
    Phase 1: Classification Triage
    Loads product, asks Claude to classify it, updates DB.
    """
    # Set granular status
    conn = get_db_connection()
    conn.execute("UPDATE products SET status = 'classifying', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (product_id,))
    conn.commit()
    
    # 1. Load Product
    product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    
    if not product:
        conn.close()
        raise ValueError(f"Product {product_id} not found")

    product_dict = dict(product)
    conn.close()
    
    # 2. Prepare Prompt
    system_prompt = """You are a product classification system for an ecommerce data enrichment pipeline.

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

Respond with ONLY valid JSON matching the provided schema. No other text."""

    user_prompt = f"""Classify this product:

Product name: {product_dict['product_name']}
EAN: {product_dict['ean']}
Existing brand field: {product_dict['brand'] or "empty"}
Existing weight: {product_dict['weight'] or "empty"}
"""

    # 3. Call LLM
    try:
        classification = classify_with_schema(
            prompt=user_prompt,
            system=system_prompt,
            schema=ProductClassification
        )
        
        # 4. Update Database
        result_json = classification.model_dump_json()
        
        # Create log entry
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "phase": "triage",
            "status": "success",
            "step": "classification",
            "details": f"Classified as {classification.product_type}, brand: {classification.brand or 'unknown'} ({classification.brand_confidence})",
            "credits_used": {"claude_tokens": 800}
        }
        
        # Append to existing log
        conn = get_db_connection()
        existing_log_row = conn.execute("SELECT enrichment_log FROM products WHERE id = ?", (product_id,)).fetchone()
        existing_log = json.loads(existing_log_row['enrichment_log']) if existing_log_row and existing_log_row['enrichment_log'] else []
        existing_log.append(log_entry)
        
        conn.execute("""
            UPDATE products 
            SET classification_result = ?, 
                status = 'enriching', 
                enrichment_log = ?,
                product_type = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (result_json, json.dumps(existing_log), classification.product_type, product_id))
        
        conn.commit()
        conn.close()
        
        return classification

    except Exception as e:
        # Log error
        _append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "triage",
            "status": "error",
            "step": "classification",
            "details": f"Classification failed: {str(e)}"
        })
        print(f"Classification failed: {e}")
        raise e
