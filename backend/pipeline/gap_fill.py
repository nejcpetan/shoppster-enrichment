"""
Pipeline Node: Gap Fill (Phase 4.5)

Fills critical data gaps using cached third-party pages from the scraped_pages table.
Only runs if validation identified critical missing fields AND third-party pages are cached.

Strategy:
  1. Load extraction_result, identify critical gaps
  2. If no gaps or no cached third-party pages -> return immediately (zero cost)
  3. Run a single targeted LLM call per third-party page using GapFillExtraction schema
  4. Merge gap-filled data into extraction_result (never overwrite existing data)
  5. Normalize any gap-filled dimensions
  6. Early exit: stop checking pages once all gaps are filled

Tools: Claude Haiku 4.5 (1 call per page, targeted extraction)
"""

import json
import logging
from datetime import datetime
from db import get_db_connection, update_step, append_log, get_scraped_pages, mark_page_gap_filled
from utils.llm import classify_with_schema
from utils.normalization import normalize_dimension_set
from schemas import (
    EnrichedProduct, ProductClassification, EnrichedField,
    GapFillExtraction, WarrantyInfo,
)

logger = logging.getLogger("pipeline.gap_fill")


# ─── Critical Gap Definitions ────────────────────────────────────────────────
# Only these fields trigger gap-fill. Others (net dims, marketing desc,
# features, tech specs, color, COO, images) have their own gap-fill mechanisms
# or are not critical enough to justify extra LLM calls.

CRITICAL_GAP_CHECKS = {
    "net_weight": lambda m: (
        m.dimensions.net.weight.value is None
        or m.dimensions.net.weight.confidence == 'not_found'
    ),
    "packaged_weight": lambda m: (
        m.dimensions.packaged.weight.value is None
        or m.dimensions.packaged.weight.confidence == 'not_found'
    ),
    "packaged_dims": lambda m: all([
        m.dimensions.packaged.height.value is None or m.dimensions.packaged.height.confidence == 'not_found',
        m.dimensions.packaged.length.value is None or m.dimensions.packaged.length.confidence == 'not_found',
        m.dimensions.packaged.width.value is None or m.dimensions.packaged.width.confidence == 'not_found',
    ]),
    "warranty": lambda m: (
        m.warranty.duration.value is None
        or m.warranty.duration.confidence == 'not_found'
    ),
    "short_description": lambda m: (
        m.descriptions.short_description.value is None
    ),
}


def _identify_gaps(model: EnrichedProduct) -> list[str]:
    """Return list of critical gap names that are missing."""
    gaps = []
    for gap_name, check_fn in CRITICAL_GAP_CHECKS.items():
        if check_fn(model):
            gaps.append(gap_name)
    return gaps


def _build_gap_fill_prompt(gaps: list[str], confidence_level: str, url: str) -> str:
    """Build a targeted prompt asking only for the missing fields."""
    sections = []

    if "net_weight" in gaps:
        sections.append("""NET WEIGHT: Find the product's net weight (without packaging).
Look for: "net weight", "weight", "Gewicht", "teža", "teža izdelka", "neto teža", "masa".
Set value (numeric only, no units), unit (kg, g, lb), confidence, source_url.""")

    if "packaged_weight" in gaps:
        sections.append("""PACKAGED WEIGHT: Find the packaged/gross weight (with packaging/box).
Look for: "gross weight", "shipping weight", "package weight", "bruto teža", "Bruttogewicht", "Versandgewicht".
Set value (numeric only), unit (kg, g, lb), confidence, source_url.""")

    if "packaged_dims" in gaps:
        sections.append("""PACKAGED DIMENSIONS: Find the package/box dimensions.
Look for: "package size", "shipping dimensions", "carton size", "dimenzije paketa", "Verpackungsmaße", "box dimensions".
Extract all three: packaged_height, packaged_length, packaged_width.
Set value (numeric only), unit (cm, mm), confidence, source_url for each.""")

    if "warranty" in gaps:
        sections.append("""WARRANTY: Find warranty information.
Look for: "warranty", "garancija", "Garantie", "garanzia", "guarantee", "jamstvo", warranty tables/sections.
Extract: warranty_duration (e.g., "2 years", "24 months"), warranty_type (manufacturer/retailer), warranty_conditions.""")

    if "short_description" in gaps:
        sections.append("""SHORT DESCRIPTION: Find a brief product summary (1-2 sentences).
Look for the product tagline or opening description near the product title.
Keep the original language (do not translate). Set to empty string if not found.""")

    prompt = f"""Extract ONLY the following missing fields from the page content provided.
Set confidence to "{confidence_level}" and source_url to "{url}" for all found values.
If a field is not found on this page, leave it as the default (null/empty).

FIELDS TO EXTRACT:

""" + "\n\n".join(sections)

    return prompt


def _all_gaps_filled(gaps: list[str], results: list[GapFillExtraction]) -> bool:
    """Check if all critical gaps have been filled by accumulated results."""
    for gap in gaps:
        if gap == "net_weight":
            if not any(r.net_weight.value is not None for r in results):
                return False
        elif gap == "packaged_weight":
            if not any(r.packaged_weight.value is not None for r in results):
                return False
        elif gap == "packaged_dims":
            has_h = any(r.packaged_height.value is not None for r in results)
            has_l = any(r.packaged_length.value is not None for r in results)
            has_w = any(r.packaged_width.value is not None for r in results)
            if not (has_h and has_l and has_w):
                return False
        elif gap == "warranty":
            if not any(r.warranty_duration for r in results):
                return False
        elif gap == "short_description":
            if not any(r.short_description for r in results):
                return False
    return True


def _merge_gap_fill(
    model: EnrichedProduct,
    gaps: list[str],
    results: list[GapFillExtraction],
) -> list[str]:
    """
    Merge gap-filled data into the existing EnrichedProduct model IN-PLACE.
    Only fills fields that are currently missing (never overwrites existing data).
    Returns list of field names that were successfully filled.
    """
    filled = []

    def _first_valid_field(attr_name: str) -> EnrichedField | None:
        """Pick the first non-null result across all gap-fill pages."""
        for r in results:
            field = getattr(r, attr_name, None)
            if field and field.value is not None and field.confidence != 'not_found':
                return field
        return None

    if "net_weight" in gaps:
        val = _first_valid_field("net_weight")
        if val:
            model.dimensions.net.weight = val
            filled.append("net_weight")

    if "packaged_weight" in gaps:
        val = _first_valid_field("packaged_weight")
        if val:
            model.dimensions.packaged.weight = val
            filled.append("packaged_weight")

    if "packaged_dims" in gaps:
        h = _first_valid_field("packaged_height")
        l_val = _first_valid_field("packaged_length")
        w = _first_valid_field("packaged_width")
        if h:
            model.dimensions.packaged.height = h
            filled.append("packaged_height")
        if l_val:
            model.dimensions.packaged.length = l_val
            filled.append("packaged_length")
        if w:
            model.dimensions.packaged.width = w
            filled.append("packaged_width")

    if "warranty" in gaps:
        for r in results:
            if r.warranty_duration:
                model.warranty.duration = EnrichedField(
                    value=r.warranty_duration,
                    confidence="third_party",
                    notes="Gap-filled from third-party page"
                )
                model.warranty.type = r.warranty_type or None
                model.warranty.conditions = r.warranty_conditions or None
                model.warranty.confidence = "third_party"
                filled.append("warranty")
                break

    if "short_description" in gaps:
        for r in results:
            if r.short_description:
                model.descriptions.short_description = EnrichedField(
                    value=r.short_description,
                    confidence="third_party",
                    notes="Gap-filled from third-party page"
                )
                filled.append("short_description")
                break

    return filled


async def gap_fill_node(state: dict) -> dict:
    """
    LangGraph node: Phase 4.5 -- Gap Fill.
    Reads cached third-party pages and does targeted extraction for missing critical fields.
    Only runs if there are critical gaps AND cached third-party pages exist.
    """
    product_id = state["product_id"]
    cost_tracker = state.get("cost_tracker")

    update_step(product_id, "gap_filling", "Checking for critical data gaps...")

    # Load current extraction result
    conn = get_db_connection()
    product_row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not product_row:
        conn.close()
        return {"error": f"Product {product_id} not found"}
    product = dict(product_row)
    conn.close()

    if not product['extraction_result']:
        logger.info(f"[Product {product_id}]   No extraction result, skipping gap fill")
        return {}

    extraction_data = json.loads(product['extraction_result'])
    model = EnrichedProduct.model_validate(extraction_data)
    classification = ProductClassification.model_validate_json(product['classification_result'])

    # Step 1: Identify critical gaps
    gaps = _identify_gaps(model)

    if not gaps:
        logger.info(f"[Product {product_id}]   No critical gaps found, skipping gap fill")
        append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "gap_fill", "step": "check", "status": "success",
            "details": "No critical gaps -- skipping gap fill"
        })
        update_step(product_id, "gap_filling", "No critical gaps found")
        return {}

    logger.info(f"[Product {product_id}]   Critical gaps found: {', '.join(gaps)}")

    # Step 2: Get cached third-party pages
    third_party_pages = get_scraped_pages(product_id, source_type='third_party', only_unextracted=True)

    if not third_party_pages:
        logger.info(f"[Product {product_id}]   No cached third-party pages available for gap fill")
        append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "gap_fill", "step": "check", "status": "warning",
            "details": f"Gaps found ({', '.join(gaps)}) but no third-party pages cached"
        })
        update_step(product_id, "gap_filling", "No third-party pages to check")
        return {}

    update_step(product_id, "gap_filling", f"Filling {len(gaps)} gaps from {len(third_party_pages)} pages...")

    append_log(product_id, {
        "timestamp": datetime.now().isoformat(),
        "phase": "gap_fill", "step": "start", "status": "success",
        "details": f"Gaps: {', '.join(gaps)} | Pages available: {len(third_party_pages)}"
    })

    # Step 3: Run targeted extraction on each third-party page
    #
    # Caching strategy: Mode A (system prompt cached across pages).
    # The static system prompt + JSON schema is identical for every page in this
    # product's gap-fill run, so page 2+ gets a cache READ (0.1x) on the system
    # prefix. Page content goes in the user message (no cache_control) — this
    # AVOIDS the 1.25x cache-write premium that Mode B would charge on 10-30K
    # tokens of markdown per page with no subsequent read.
    gap_fill_results: list[GapFillExtraction] = []

    # Static system prompt — identical across all pages → cached after first call
    gap_fill_system = f"""You are a product data extraction assistant.
Product: {classification.brand} {classification.model_number} (EAN: {product['ean']})
You will receive scraped third-party page content and instructions to extract specific missing fields.
If a field is not found on the page, leave it as the default (null/empty)."""

    for page in third_party_pages:
        url = page['url']
        markdown = page['markdown']

        if not markdown or len(markdown) < 100:
            continue

        page_content = markdown[:30000]  # Same truncation as main extract
        confidence_level = "third_party"

        gap_prompt = _build_gap_fill_prompt(gaps, confidence_level, url)

        # User message: page content + extraction instructions (varies per page)
        user_message = f"""PAGE CONTENT (Source: {url}):

{page_content}

---

{gap_prompt}"""

        try:
            result, usage = classify_with_schema(
                prompt=user_message,
                system=gap_fill_system,
                schema=GapFillExtraction,
                model="haiku",
                return_usage=True,
                max_tokens=2048,
            )
            gap_fill_results.append(result)

            if cost_tracker:
                cost_tracker.add_llm_call(
                    usage["model"], usage["input_tokens"], usage["output_tokens"],
                    phase="gap_fill",
                    cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0),
                    cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
                )

            mark_page_gap_filled(product_id, url)

            # Summarize what was found
            found_fields = []
            if result.net_weight.value is not None:
                found_fields.append(f"net_weight={result.net_weight.value}")
            if result.packaged_weight.value is not None:
                found_fields.append(f"pkg_weight={result.packaged_weight.value}")
            if result.packaged_height.value is not None:
                found_fields.append("pkg_dims")
            if result.warranty_duration:
                found_fields.append(f"warranty={result.warranty_duration}")
            if result.short_description:
                found_fields.append("short_desc")

            found_summary = ", ".join(found_fields) if found_fields else "nothing new"

            logger.info(f"[Product {product_id}]   Gap fill from {url[:50]}: {found_summary}")
            append_log(product_id, {
                "timestamp": datetime.now().isoformat(),
                "phase": "gap_fill", "step": "extract", "status": "success",
                "details": f"Gap fill from {url[:60]}: {found_summary}",
                "credits_used": {"claude_in": usage["input_tokens"], "claude_out": usage["output_tokens"]}
            })

            # Early exit: if all gaps are now filled, stop processing more pages
            if _all_gaps_filled(gaps, gap_fill_results):
                logger.info(f"[Product {product_id}]   All gaps filled, stopping early")
                break

        except Exception as e:
            logger.warning(f"[Product {product_id}]   Gap fill failed for {url[:50]}: {e}")
            append_log(product_id, {
                "timestamp": datetime.now().isoformat(),
                "phase": "gap_fill", "step": "extract", "status": "error",
                "details": f"Gap fill failed for {url[:50]}: {e}"
            })

    # Step 4: Merge gap-filled data into existing extraction result
    if gap_fill_results:
        fields_filled = _merge_gap_fill(model, gaps, gap_fill_results)

        if fields_filled:
            # Normalize any gap-filled dimensions
            dim_fields = {'net_weight', 'packaged_weight', 'packaged_height', 'packaged_length', 'packaged_width'}
            if dim_fields & set(fields_filled):
                normalize_dimension_set(model.dimensions.net)
                normalize_dimension_set(model.dimensions.packaged)

            # Save updated extraction result
            conn = get_db_connection()
            conn.execute(
                "UPDATE products SET extraction_result = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (model.model_dump_json(), product_id)
            )
            conn.commit()
            conn.close()

            logger.info(f"[Product {product_id}]   Gap fill complete: filled {', '.join(fields_filled)}")
            append_log(product_id, {
                "timestamp": datetime.now().isoformat(),
                "phase": "gap_fill", "step": "merge", "status": "success",
                "details": f"Filled: {', '.join(fields_filled)} from {len(gap_fill_results)} third-party page(s)"
            })
            update_step(product_id, "gap_filling", f"Filled {len(fields_filled)} fields from third-party sources")
        else:
            append_log(product_id, {
                "timestamp": datetime.now().isoformat(),
                "phase": "gap_fill", "step": "merge", "status": "warning",
                "details": "Gap fill ran but no new data found on third-party pages"
            })
            update_step(product_id, "gap_filling", "No new data found on third-party pages")
    else:
        update_step(product_id, "gap_filling", "No third-party pages had usable content")

    return {}
