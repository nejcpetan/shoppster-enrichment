
import json
from datetime import datetime
from pydantic import BaseModel
from typing import Literal, List, Optional
from db import get_db_connection
from utils.llm import classify_with_schema
from utils.normalization import normalize_field
from schemas import (
    StandardProduct, AccessoryProduct, LiquidProduct, 
    ProductClassification
)

class ValidationIssue(BaseModel):
    field: str
    issue: str
    severity: Literal["warning", "error"]

class ValidationReport(BaseModel):
    overall_quality: Literal["good", "acceptable", "needs_review"]
    issues: List[ValidationIssue]
    review_reason: Optional[str] = None

class ValidatedProductData(BaseModel):
    normalized_data: dict 
    report: ValidationReport


def _append_log(product_id: int, entry: dict):
    """Append a log entry to the product's enrichment_log."""
    conn = get_db_connection()
    product = conn.execute("SELECT enrichment_log FROM products WHERE id = ?", (product_id,)).fetchone()
    existing = json.loads(product['enrichment_log']) if product and product['enrichment_log'] else []
    existing.append(entry)
    conn.execute("UPDATE products SET enrichment_log = ? WHERE id = ?", (json.dumps(existing), product_id))
    conn.commit()
    conn.close()


async def validate_product(product_id: int):
    """
    Phase 4: Validation
    Normalizes data -> Claude Sanity Check -> Final Result
    """
    # Set granular status
    conn = get_db_connection()
    conn.execute("UPDATE products SET status = 'validating', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (product_id,))
    conn.commit()
    
    # 1. Load Context
    product_row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not product_row:
        conn.close()
        raise ValueError(f"Product {product_id} not found")
        
    product = dict(product_row)
    conn.close()
    
    if not product['extraction_result']:
        raise ValueError("Extraction results missing. Run Phase 3 first.")
        
    extraction_json = json.loads(product['extraction_result'])
    classification = ProductClassification.model_validate_json(product['classification_result'])
    
    # 2. Rehydrate Schema
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
    
    # 3. Normalize Data
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

    # Apply to standard fields
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
    
    _append_log(product_id, {
        "timestamp": datetime.now().isoformat(),
        "phase": "validate", "step": "normalization", "status": "success",
        "details": f"Normalized {normalized_count} fields to standard units (cm, kg, L)"
    })
    
    # 4. Sanity Check (LLM)
    system_prompt = """You are a data quality checker for enriched product data.

Review the extracted and normalized product data below. Check for:

1. PLAUSIBILITY: Does the weight make sense for this product type? A wire brush should be <0.5 kg. A 20L oil canister should be ~18 kg. A hedge trimmer should be 2-6 kg.

2. DIMENSION CONSISTENCY: If height, length, and width are all provided, do they form a plausible shape? Is any dimension suspiciously large or small?

3. DATA CONFLICTS: Does any extracted value contradict the original product name? (e.g., product name says "20L" but volume extracted as "5L")

4. MISSING CRITICAL DATA: For the product type, which fields SHOULD have data but don't?

Return JSON matching the schema provided."""

    user_prompt = f"""Product: {classification.brand} {classification.model_number} ({classification.product_type})
Original name: {product['product_name']}
EAN: {product['ean']}

Normalized Data:
{normalized_model.model_dump_json(indent=2)}

Check this data for quality issues.
"""

    try:
        report = classify_with_schema(
            prompt=user_prompt,
            system=system_prompt,
            schema=ValidationReport
        )
        
        _append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "validate", "step": "sanity_check", "status": "success",
            "details": f"Quality: {report.overall_quality}, {len(report.issues)} issues found",
            "credits_used": {"claude_tokens": 800}
        })
    except Exception as e:
        # If sanity check fails, still save the data with a default report
        report = ValidationReport(
            overall_quality="needs_review",
            issues=[ValidationIssue(field="system", issue=f"Sanity check failed: {str(e)}", severity="warning")],
            review_reason="Automated sanity check could not complete"
        )
        _append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "validate", "step": "sanity_check", "status": "error",
            "details": f"Sanity check failed: {str(e)}"
        })
    
    # 5. Save Result
    final_result = ValidatedProductData(
        normalized_data=normalized_model.model_dump(),
        report=report
    )
    
    # Determine final status
    final_status = "done"
    if report.overall_quality == "needs_review":
        final_status = "needs_review"
    
    has_errors = any(i.severity == "error" for i in report.issues)
    if has_errors:
        final_status = "needs_review"
    
    conn = get_db_connection()
    conn.execute(
        "UPDATE products SET validation_result = ?, status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (final_result.model_dump_json(), final_status, product_id)
    )
    conn.commit()
    conn.close()
    
    _append_log(product_id, {
        "timestamp": datetime.now().isoformat(),
        "phase": "validate", "step": "complete", "status": "success",
        "details": f"Validation complete. Status: {final_status}"
    })
    
    return final_result
