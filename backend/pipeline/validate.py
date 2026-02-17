"""
Pipeline Node: Validate (Phase 4) — v2
Agent role: Normalize units, run sanity check, assign final quality score.
Tools: Claude Haiku 4.5

Now handles the unified EnrichedProduct schema with net/packaged dimensions,
descriptions, tech specs, warranty, and documents.
"""

import json
import logging
from datetime import datetime
from db import get_db_connection, update_step, append_log
from utils.llm import classify_with_schema
from utils.normalization import normalize_dimension_set
from schemas import (
    EnrichedProduct, ProductClassification,
    ValidationReport, ValidatedProductData, ValidationIssue
)

logger = logging.getLogger("pipeline.validate")


async def validate_node(state: dict) -> dict:
    """
    LangGraph node: Phase 4 — Validation.
    Normalizes data → sanity check → final result.
    """
    product_id = state["product_id"]
    cost_tracker = state.get("cost_tracker")

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

    # Parse into unified schema
    data_model = EnrichedProduct.model_validate(extraction_json)

    # ── Normalize units ───────────────────────────────────────────────────
    update_step(product_id, "validating", "Normalizing units (cm, kg, L)...")
    normalized_model = data_model.model_copy(deep=True)
    normalized_count = 0

    # Normalize net dimensions
    net_count = normalize_dimension_set(normalized_model.dimensions.net)
    normalized_count += net_count

    # Normalize packaged dimensions
    pkg_count = normalize_dimension_set(normalized_model.dimensions.packaged)
    normalized_count += pkg_count

    logger.info(f"[Product {product_id}]   Normalized {normalized_count} fields (net: {net_count}, pkg: {pkg_count})")
    append_log(product_id, {
        "timestamp": datetime.now().isoformat(),
        "phase": "validate", "step": "normalization", "status": "success",
        "details": f"Normalized {normalized_count} fields (net: {net_count}, packaged: {pkg_count})"
    })

    # ── Sanity check via Claude ───────────────────────────────────────────
    logger.info(f"[Product {product_id}]   Calling Claude Haiku 4.5 for sanity check...")
    update_step(product_id, "validating", "Running sanity check...")

    # Build a concise data summary for the LLM
    data_summary = _build_data_summary(normalized_model)

    system_prompt = """You are a data quality checker for enriched product data.

Check for:
1. PLAUSIBILITY: Does weight make sense? A wire brush < 0.5 kg, a hedge trimmer 2-6 kg, 20L oil canister ~18 kg.
2. DIMENSION CONSISTENCY: Do net dimensions form a plausible shape? Are packaged dimensions >= net dimensions?
3. NET vs PACKAGED LOGIC: Packaged weight should be >= net weight. Packaged volume should be >= net volume.
4. DATA CONFLICTS: Does any value contradict the product name? (e.g., name says "20L" but volume is "5L")
5. MISSING CRITICAL DATA: Which fields SHOULD have data but don't?
6. DESCRIPTION QUALITY: Is the short description present? Is it a reasonable summary?
7. TECHNICAL SPECS: Do the specifications make sense for this product type?
8. WARRANTY: Is warranty duration reasonable for this product category?
9. DOCUMENTS: Are document URLs valid (not broken, not duplicated)?

Return JSON matching the provided schema."""

    user_prompt = f"""Product: {classification.brand} {classification.model_number} ({classification.product_type})
Original name: {product['product_name']}
EAN: {product['ean']}

Data Summary:
{data_summary}

Check this data for quality issues."""

    try:
        report, usage = classify_with_schema(
            prompt=user_prompt,
            system=system_prompt,
            schema=ValidationReport,
            model="haiku",
            return_usage=True
        )

        # Track cost
        if cost_tracker:
            cost_tracker.add_llm_call(
                usage["model"], usage["input_tokens"], usage["output_tokens"],
                phase="validate"
            )

        logger.info(f"[Product {product_id}]   ✓ Quality: {report.overall_quality}, {len(report.issues)} issues")
        append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "validate", "step": "sanity_check", "status": "success",
            "details": f"Quality: {report.overall_quality}, {len(report.issues)} issues",
            "credits_used": {"claude_in": usage["input_tokens"], "claude_out": usage["output_tokens"]}
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

    # ── Save final result ─────────────────────────────────────────────────
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


def _build_data_summary(model: EnrichedProduct) -> str:
    """Build a concise text summary for the validation LLM."""
    lines = []

    # Net dimensions
    net = model.dimensions.net
    net_vals = []
    for f_name in ['height', 'length', 'width', 'depth', 'weight', 'diameter', 'volume']:
        field = getattr(net, f_name)
        if field and field.value is not None:
            net_vals.append(f"  {f_name}: {field.value} {field.unit or ''} ({field.confidence})")
    if net_vals:
        lines.append("NET DIMENSIONS:")
        lines.extend(net_vals)
    else:
        lines.append("NET DIMENSIONS: (none extracted)")

    # Packaged dimensions
    pkg = model.dimensions.packaged
    pkg_vals = []
    for f_name in ['height', 'length', 'width', 'depth', 'weight']:
        field = getattr(pkg, f_name)
        if field and field.value is not None:
            pkg_vals.append(f"  {f_name}: {field.value} {field.unit or ''} ({field.confidence})")
    if pkg_vals:
        lines.append("PACKAGED DIMENSIONS:")
        lines.extend(pkg_vals)
    else:
        lines.append("PACKAGED DIMENSIONS: (none extracted)")

    # Color + COO
    if model.color and model.color.value:
        lines.append(f"COLOR: {model.color.value} ({model.color.confidence})")
    if model.country_of_origin and model.country_of_origin.value:
        lines.append(f"COUNTRY OF ORIGIN: {model.country_of_origin.value} ({model.country_of_origin.confidence})")

    # Descriptions
    if model.descriptions.short_description and model.descriptions.short_description.value:
        lines.append(f"SHORT DESCRIPTION: {str(model.descriptions.short_description.value)[:200]}")
    if model.descriptions.marketing_description and model.descriptions.marketing_description.value:
        lines.append(f"MARKETING DESCRIPTION: {str(model.descriptions.marketing_description.value)[:300]}")
    if model.descriptions.features:
        lines.append(f"FEATURES: {len(model.descriptions.features)} items")
        for feat in model.descriptions.features[:5]:
            lines.append(f"  - {feat[:100]}")
        if len(model.descriptions.features) > 5:
            lines.append(f"  ... and {len(model.descriptions.features) - 5} more")

    # Technical specs
    if model.technical_data.specs:
        lines.append(f"TECHNICAL SPECS: {len(model.technical_data.specs)} items")
        for spec in model.technical_data.specs[:10]:
            lines.append(f"  {spec.name}: {spec.value} {spec.unit or ''}")
        if len(model.technical_data.specs) > 10:
            lines.append(f"  ... and {len(model.technical_data.specs) - 10} more")

    # Warranty
    if model.warranty.duration and model.warranty.duration.value:
        lines.append(f"WARRANTY: {model.warranty.duration.value} ({model.warranty.type or 'unknown type'})")

    # Documents
    if model.documents.documents:
        lines.append(f"DOCUMENTS: {len(model.documents.documents)} files")
        for doc in model.documents.documents:
            lines.append(f"  [{doc.doc_type}] {doc.title}")

    # Images
    lines.append(f"IMAGES: {len(model.image_urls)} URLs")

    return "\n".join(lines)
