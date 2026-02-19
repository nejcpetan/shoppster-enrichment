"""
Pipeline Node: Triage (Phase 1)
Agent role: Classify product, identify brand, parse product name.
Tools: Claude Haiku 4.5
"""

import json
import logging
from datetime import datetime
from db import get_db_connection, update_step, append_log
from utils.llm import classify_with_schema
from schemas import ProductClassification

logger = logging.getLogger("pipeline.triage")


async def triage_node(state: dict) -> dict:
    """
    LangGraph node: Phase 1 — Triage / Classification.
    Parses product name, classifies type, identifies brand.
    """
    product_id = state["product_id"]
    cost_tracker = state.get("cost_tracker")

    logger.info(f"[Product {product_id}] ▶ TRIAGE — Starting classification")
    update_step(product_id, "classifying", "Parsing product name...")
    
    # Load product
    conn = get_db_connection()
    product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not product:
        conn.close()
        return {"error": f"Product {product_id} not found"}
    product = dict(product)
    conn.close()

    logger.info(f"[Product {product_id}]   Product: {product['product_name']} (EAN: {product['ean']})")

    # Build prompt
    system_prompt = """You are a product classification expert for a data enrichment pipeline.

Given a product name (often in Slovenian), EAN code, and any existing data:
1. PARSE the product name to extract: brand, model number, color hints, size hints
2. CLASSIFY the product type into one of: standard_product, accessory, liquid, soft_good, electronics, other
3. Provide reasoning for your classification
4. MANUFACTURER DOMAIN: Identify the brand's official website domain (e.g., "makita.com", "bosch.com", "texas-garden.com").
   Return just the domain — no "http://", no "www." prefix, no paths.
   Set to null if the brand has no obvious website or you cannot determine it confidently.

PRODUCT TYPE RULES:
- standard_product: Physical products with standard dimensions (H/L/W). Tools, machines, appliances.
- accessory: Small parts/attachments defined by diameter, arbor size, etc. Wire brushes, drill bits, saw blades.
- liquid: Liquids, oils, chemicals. Defined by volume, not physical dimensions.
- soft_good: Textiles, clothing, bags.
- electronics: Pure electronic devices.
- other: If nothing else fits.

BRAND DETECTION:
- Look for known brands in the product name (Texas, Makita, Bosch, DeWalt, Valvoline, etc.)
- brand_confidence: "certain" if brand is explicitly stated, "likely" if inferred, "unknown" if can't determine"""

    user_prompt = f"""Product Name: {product['product_name']}
EAN: {product['ean']}
Existing Brand: {product.get('brand', 'None')}
Existing Weight: {product.get('weight', 'None')}"""

    logger.info(f"[Product {product_id}]   Calling Claude Haiku 4.5 for classification...")
    update_step(product_id, "classifying", "Running classification model...")

    try:
        classification, usage = classify_with_schema(
            prompt=user_prompt,
            system=system_prompt,
            schema=ProductClassification,
            model="haiku",
            return_usage=True
        )

        # Track cost (with cache metrics)
        if cost_tracker:
            cost_tracker.add_llm_call(
                usage["model"], usage["input_tokens"], usage["output_tokens"],
                phase="triage",
                cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0),
                cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
            )

        # Save to DB
        conn = get_db_connection()
        conn.execute("""
            UPDATE products 
            SET classification_result = ?, product_type = ?, current_step = 'Classification complete',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (classification.model_dump_json(), classification.product_type, product_id))
        conn.commit()
        conn.close()

        logger.info(f"[Product {product_id}]   ✓ Classified: type={classification.product_type}, brand={classification.brand} ({classification.brand_confidence})")
        append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "triage", "step": "classify", "status": "success",
            "details": f"Type: {classification.product_type}, Brand: {classification.brand} ({classification.brand_confidence})",
            "credits_used": {"claude_in": usage["input_tokens"], "claude_out": usage["output_tokens"]}
        })

        has_brand = (
            classification.brand is not None 
            and classification.brand_confidence != "unknown"
        )

        return {"has_brand": has_brand}

    except Exception as e:
        logger.error(f"[Product {product_id}]   ✗ Triage FAILED: {e}")
        append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "triage", "step": "classify", "status": "error",
            "details": str(e)
        })
        return {"error": str(e)}
