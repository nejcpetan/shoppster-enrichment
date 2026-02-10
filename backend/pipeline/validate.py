"""
Pipeline Node: Validate (Phase 4)
Agent role: Normalize units, run sanity check, assign final quality score.
Tools: Claude Haiku 4.5
"""

import json
import logging
from datetime import datetime
from db import get_db_connection, update_step, append_log
from utils.llm import classify_with_schema
from utils.normalization import normalize_field
from schemas import (
    StandardProduct, AccessoryProduct, LiquidProduct,
    ProductClassification, ValidationReport, ValidatedProductData,
    ValidationIssue
)

logger = logging.getLogger("pipeline.validate")


async def validate_node(state: dict) -> dict:
    """
    LangGraph node: Phase 4 — Validation.
    Normalizes data → sanity check → final result.
    """
    product_id = state["product_id"]

    logger.info(f"[Product {product_id}] ▶ VALIDATE — Normalizing and checking data")
    update_step(product_id, "validating", "Loading extracted data...")

    # Load context
    conn = get_db_connection()
    product_row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not product_row:
        conn.close()
        return {"error": f"Product {product_id} not found"}

    product = dict(product_row)
    conn.close()

    if not product['extraction_result']:
        return {"error": "Extraction results missing. Run Phase 3 first."}

    extraction_json = json.loads(product['extraction_result'])
    classification = ProductClassification.model_validate_json(product['classification_result'])

    # Select schema
    schema_map = {
        "standard_product": StandardProduct,
        "accessory": AccessoryProduct,
        "liquid": LiquidProduct,
        "electronics": StandardProduct,
        "soft_good": StandardProduct,
        "other": StandardProduct
    }
    TargetSchema = schema_map.get(classification.product_type, StandardProduct)
    data_model = TargetSchema.model_validate(extraction_json)

    # Normalize units
    update_step(product_id, "validating", "Normalizing units (cm, kg, L)...")
    normalized_model = data_model.model_copy()
    normalized_count = 0

    def apply_norm(field_name, target_type):
        nonlocal normalized_count
        if hasattr(normalized_model, field_name):
            field = getattr(normalized_model, field_name)
            if field and field.value is not None:
                norm_field = normalize_field(field, target_type)
                setattr(normalized_model, field_name, norm_field)
                normalized_count += 1

    apply_norm('height', 'length')
    apply_norm('width', 'length')
    apply_norm('length', 'length')
    apply_norm('diameter', 'length')
    apply_norm('thickness', 'length')
    apply_norm('container_height', 'length')
    apply_norm('container_width', 'length')
    apply_norm('container_depth', 'length')
    apply_norm('weight', 'weight')
    apply_norm('volume', 'volume')

    logger.info(f"[Product {product_id}]   Normalized {normalized_count} fields to standard units")
    append_log(product_id, {
        "timestamp": datetime.now().isoformat(),
        "phase": "validate", "step": "normalization", "status": "success",
        "details": f"Normalized {normalized_count} fields to standard units"
    })

    # Sanity check via Claude
    logger.info(f"[Product {product_id}]   Calling Claude Haiku 4.5 for sanity check...")
    update_step(product_id, "validating", "Running sanity check...")

    system_prompt = """You are a data quality checker for enriched product data.

Check for:
1. PLAUSIBILITY: Does weight make sense? A wire brush < 0.5 kg, a hedge trimmer 2-6 kg, 20L oil canister ~18 kg.
2. DIMENSION CONSISTENCY: Do dimensions form a plausible shape?
3. DATA CONFLICTS: Does any value contradict the product name? (e.g., name says "20L" but volume is "5L")
4. MISSING CRITICAL DATA: Which fields SHOULD have data but don't?

Return JSON matching the provided schema."""

    user_prompt = f"""Product: {classification.brand} {classification.model_number} ({classification.product_type})
Original name: {product['product_name']}
EAN: {product['ean']}

Normalized Data:
{normalized_model.model_dump_json(indent=2)}

Check this data for quality issues."""

    try:
        report = classify_with_schema(
            prompt=user_prompt,
            system=system_prompt,
            schema=ValidationReport,
            model="haiku"
        )

        logger.info(f"[Product {product_id}]   ✓ Quality: {report.overall_quality}, {len(report.issues)} issues")
        append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "validate", "step": "sanity_check", "status": "success",
            "details": f"Quality: {report.overall_quality}, {len(report.issues)} issues",
            "credits_used": {"claude_tokens": 600}
        })
    except Exception as e:
        logger.error(f"[Product {product_id}]   ✗ Sanity check failed: {e}")
        report = ValidationReport(
            overall_quality="needs_review",
            issues=[ValidationIssue(field="system", issue=f"Sanity check failed: {str(e)}", severity="warning")],
            review_reason="Automated sanity check could not complete"
        )
        append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "validate", "step": "sanity_check", "status": "error",
            "details": f"Sanity check failed: {str(e)}"
        })

    # Save final result
    final_result = ValidatedProductData(
        normalized_data=normalized_model.model_dump(),
        report=report
    )

    final_status = "done"
    if report.overall_quality == "needs_review":
        final_status = "needs_review"
    if any(i.severity == "error" for i in report.issues):
        final_status = "needs_review"

    conn = get_db_connection()
    conn.execute(
        "UPDATE products SET validation_result = ?, status = ?, current_step = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (final_result.model_dump_json(), final_status, product_id)
    )
    conn.commit()
    conn.close()

    logger.info(f"[Product {product_id}]   ✓ Final status: {final_status}")
    logger.info(f"[Product {product_id}] ■ PIPELINE COMPLETE — {final_status}")
    append_log(product_id, {
        "timestamp": datetime.now().isoformat(),
        "phase": "validate", "step": "complete", "status": "success",
        "details": f"Validation complete. Final status: {final_status}"
    })

    return {}
