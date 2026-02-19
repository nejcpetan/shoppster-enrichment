"""
Pipeline Node: Validate (Phase 4) — v3
Agent role: Normalize units, apply deterministic corrections, run sanity check, assign final quality score.
Tools: Claude Haiku 4.5

Now handles the unified EnrichedProduct schema with net/packaged dimensions,
descriptions, tech specs, warranty, and documents.

Correction pipeline (deterministic, no LLM calls):
  1. Color normalization: non-English color names → English
  2. Country of origin normalization: strip prefixes ("Made in Germany" → "Germany"), map to standard names
  3. Junk value removal: "N/A", "-", "unknown", etc. → null
"""

import json
import re
import logging
from datetime import datetime
from db import get_db_connection, update_step, append_log
from utils.llm import classify_with_schema
from utils.normalization import normalize_dimension_set
from schemas import (
    EnrichedProduct, ProductClassification,
    ValidationReport, ValidatedProductData, ValidationIssue
)


# ─── Correction Tables ────────────────────────────────────────────────────────

MULTILANG_COLORS: dict[str, str] = {
    # German
    "schwarz": "black", "weiß": "white", "weiss": "white", "rot": "red",
    "blau": "blue", "grün": "green", "gruen": "green", "gelb": "yellow",
    "silber": "silver", "grau": "gray", "braun": "brown", "lila": "purple",
    "gold": "gold", "orange": "orange",
    # French
    "noir": "black", "blanc": "white", "rouge": "red", "bleu": "blue",
    "vert": "green", "jaune": "yellow", "argent": "silver", "gris": "gray",
    "brun": "brown", "violet": "purple",
    # Italian
    "nero": "black", "bianco": "white", "rosso": "red", "blu": "blue",
    "verde": "green", "giallo": "yellow", "argento": "silver", "grigio": "gray",
    "marrone": "brown", "viola": "purple", "arancione": "orange",
    # Spanish
    "negro": "black", "blanco": "white", "rojo": "red", "azul": "blue",
    "naranja": "orange", "marron": "brown",
    # Slovenian
    "črna": "black", "bela": "white", "rdeča": "red", "modra": "blue",
    "zelena": "green", "rumena": "yellow", "srebrna": "silver", "siva": "gray",
    "rjava": "brown", "vijolična": "purple", "oranžna": "orange",
    # Common compound (keep as-is or map)
    "schwarz/gelb": "black/yellow", "gelb/schwarz": "yellow/black",
    "schwarz/rot": "black/red", "rot/schwarz": "red/black",
}

COUNTRY_PREFIX_RE = re.compile(
    r'^(?:made\s+in|manufactured\s+in|product\s+of|hergestellt\s+in|'
    r'fabriqué\s+en|prodotto\s+in|fabricado\s+en|произведено\s+в|origin[:\s]+|'
    r'country\s+of\s+origin[:\s]+|land[:\s]+)\s*',
    re.IGNORECASE
)

COUNTRY_NAME_MAP: dict[str, str] = {
    "de": "Germany", "deutschland": "Germany", "allemagne": "Germany",
    "germania": "Germany", "alemania": "Germany",
    "jp": "Japan", "japon": "Japan", "giappone": "Japan",
    "cn": "China", "chine": "China", "cina": "China", "prc": "China",
    "pr china": "China", "people's republic of china": "China",
    "us": "USA", "usa": "USA", "united states": "USA",
    "united states of america": "USA", "états-unis": "USA", "stati uniti": "USA",
    "it": "Italy", "italia": "Italy", "italie": "Italy",
    "fr": "France", "frankreich": "France", "francia": "France",
    "gb": "UK", "uk": "UK", "great britain": "UK", "united kingdom": "UK",
    "england": "UK",
    "pl": "Poland", "polska": "Poland", "pologne": "Poland", "polen": "Poland",
    "cz": "Czech Republic", "czech republic": "Czech Republic",
    "czechia": "Czech Republic", "tschechien": "Czech Republic",
    "tw": "Taiwan", "chinese taipei": "Taiwan",
    "kr": "South Korea", "south korea": "South Korea", "korea": "South Korea",
    "sk": "Slovakia", "slowakei": "Slovakia",
    "at": "Austria", "österreich": "Austria", "autriche": "Austria",
    "nl": "Netherlands", "holland": "Netherlands", "niederlande": "Netherlands",
    "be": "Belgium", "belgique": "Belgium", "belgien": "Belgium",
    "se": "Sweden", "schweden": "Sweden", "suède": "Sweden",
    "fi": "Finland", "finnland": "Finland", "finlande": "Finland",
    "no": "Norway", "norwegen": "Norway", "norvège": "Norway",
    "dk": "Denmark", "dänemark": "Denmark", "danemark": "Denmark",
    "ch": "Switzerland", "schweiz": "Switzerland", "suisse": "Switzerland",
    "es": "Spain", "españa": "Spain", "spanien": "Spain", "espagne": "Spain",
    "pt": "Portugal",
    "hu": "Hungary", "ungarn": "Hungary", "hongrie": "Hungary",
    "ro": "Romania", "rumänien": "Romania", "roumanie": "Romania",
    "hr": "Croatia", "kroatien": "Croatia", "croatie": "Croatia",
    "si": "Slovenia", "slowenien": "Slovenia", "slovénie": "Slovenia",
    "tr": "Turkey", "türkei": "Turkey", "turquie": "Turkey", "türkiye": "Turkey",
    "in": "India", "indien": "India", "inde": "India",
    "th": "Thailand", "tailandia": "Thailand",
    "vn": "Vietnam", "vietnam": "Vietnam",
    "mx": "Mexico", "méxico": "Mexico", "mexiko": "Mexico",
    "br": "Brazil", "brasilien": "Brazil", "brésil": "Brazil",
}

JUNK_VALUES = {
    "", "n/a", "na", "n.a.", "-", "--", "---", ".", "..", "...", "none", "null",
    "not available", "not specified", "not stated", "unknown", "unbekannt",
    "tbd", "tba", "see description", "see product description", "varies",
    "various", "multiple", "assorted", "?", "no data", "kein", "keine",
    "неизвестно", "/", "na.", "n.d.", "nd",
}

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

    # ── Deterministic corrections ─────────────────────────────────────────
    update_step(product_id, "validating", "Applying data corrections...")
    corrections = _apply_corrections(normalized_model)

    if corrections:
        logger.info(f"[Product {product_id}]   Applied {len(corrections)} correction(s)")
        append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "validate", "step": "corrections", "status": "success",
            "details": f"Applied {len(corrections)} correction(s): " + " | ".join(corrections)
        })

    # ── Sanity check via Claude ───────────────────────────────────────────
    logger.info(f"[Product {product_id}]   Calling Claude Haiku 4.5 for sanity check...")
    update_step(product_id, "validating", "Running sanity check...")

    # Build a concise data summary for the LLM
    data_summary = _build_data_summary(normalized_model)

    system_prompt = """You are a data quality checker for enriched product data.

Check for:
1. PLAUSIBILITY: Does weight make sense? A wire brush < 0.5 kg, a hedge trimmer 2-6 kg, 20L oil canister ~18 kg.
2. DIMENSION CONSISTENCY: Do net dimensions form a plausible shape for this product type?
3. NET vs PACKAGED DIMENSIONS: Think carefully before flagging.
   - Packaged dimensions CAN be SMALLER than net dimensions. This is NORMAL for products that require assembly after unboxing (power tools, furniture, garden equipment, appliances). The product is disassembled/folded in the box and becomes larger once assembled. Do NOT flag this as an error.
   - Only flag dimension inconsistencies as errors when it is physically impossible for the product to fit in the package even disassembled (e.g., a solid metal bar listed as 100 cm net length but 30 cm packaged length — metal cannot fold).
4. NET vs PACKAGED WEIGHT: Small discrepancies (< 5% or < 500g) have already been auto-corrected before you see the data. If you still see packaged weight < net weight, it means the difference is large — flag it only if it is truly implausible (multiple kilograms difference with no reasonable explanation).
5. DATA CONFLICTS: Does any value contradict the product name? (e.g., name says "20L" but volume is "5L")
6. MISSING CRITICAL DATA: Which fields SHOULD have data but don't?
7. DESCRIPTION QUALITY: Is the short description present? Is it a reasonable summary?
8. TECHNICAL SPECS: Do the specifications make sense for this product type?
9. WARRANTY: Is warranty duration reasonable for this product category?

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

        # Track cost (with cache metrics)
        if cost_tracker:
            cost_tracker.add_llm_call(
                usage["model"], usage["input_tokens"], usage["output_tokens"],
                phase="validate",
                cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0),
                cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
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

    # Trust the LLM's overall quality score as the primary signal.
    # "good" and "acceptable" → done. Only "needs_review" → needs_review.
    final_status = "done" if report.overall_quality in ("good", "acceptable") else "needs_review"

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


def _normalize_color(raw: str) -> str | None:
    """Map non-English color names to English. Returns None if no mapping found."""
    return MULTILANG_COLORS.get(raw.strip().lower())


def _normalize_country(raw: str) -> str | None:
    """
    Normalize country of origin strings.
    Strips common prefixes ("Made in Germany" → "Germany"),
    then maps to a standard English country name.
    Returns None if no change is needed.
    """
    stripped = COUNTRY_PREFIX_RE.sub('', raw).strip()
    mapped = COUNTRY_NAME_MAP.get(stripped.lower())
    if mapped:
        return mapped
    # Return the stripped version if prefix was removed but name is unrecognised
    if stripped != raw:
        return stripped
    return None


def _is_junk(val: str) -> bool:
    """Check whether a value is a placeholder that should be nulled out."""
    return val.strip().lower() in JUNK_VALUES or len(val.strip()) <= 1


def _apply_corrections(model: EnrichedProduct) -> list[str]:
    """
    Apply deterministic, zero-cost corrections to the enriched product model in-place.
    Returns a list of human-readable correction descriptions for the log.
    """
    corrections: list[str] = []

    # ── 1. Color normalization (non-English → English) ────────────────────
    if model.color and model.color.value is not None:
        raw = str(model.color.value)
        if _is_junk(raw):
            corrections.append(f"color: removed junk value '{raw}'")
            model.color.value = None
            model.color.confidence = "not_found"
        else:
            mapped = _normalize_color(raw)
            if mapped:
                corrections.append(f"color: '{raw}' → '{mapped}' (language normalization)")
                model.color.value = mapped
                existing_notes = model.color.notes or ""
                model.color.notes = (existing_notes + "; auto-normalized from non-English").lstrip("; ")

    # ── 2. Country of origin normalization ───────────────────────────────
    if model.country_of_origin and model.country_of_origin.value is not None:
        raw = str(model.country_of_origin.value)
        if _is_junk(raw):
            corrections.append(f"country_of_origin: removed junk value '{raw}'")
            model.country_of_origin.value = None
            model.country_of_origin.confidence = "not_found"
        else:
            normalized = _normalize_country(raw)
            if normalized:
                corrections.append(f"country_of_origin: '{raw}' → '{normalized}'")
                model.country_of_origin.value = normalized

    # ── 3. Weight discrepancy normalization ─────────────────────────────
    # When packaged weight is slightly less than net weight (measurement noise,
    # rounding, different sources), normalize both to the heavier value.
    # Only auto-fix when the difference is small (< 5% or < 0.5 kg).
    # Large discrepancies are left for the LLM sanity check to flag.
    net_w = model.dimensions.net.weight
    pkg_w = model.dimensions.packaged.weight
    if (net_w.value is not None and pkg_w.value is not None
            and net_w.unit == pkg_w.unit):  # both already normalized to same unit
        net_val = float(net_w.value)
        pkg_val = float(pkg_w.value)
        if pkg_val < net_val:
            diff = net_val - pkg_val
            heavier = net_val
            # Auto-fix if difference is < 5% of the heavier value OR < 0.5 kg
            threshold_kg = 0.5 if net_w.unit == "kg" else 500  # 500g if unit is g
            if diff < heavier * 0.05 or diff < threshold_kg:
                corrections.append(
                    f"packaged_weight: {pkg_val} → {net_val} {net_w.unit} "
                    f"(was {diff:.3f} {net_w.unit} lighter than net — normalized to net weight)"
                )
                pkg_w.value = net_val
                pkg_w.confidence = "inferred"
                pkg_w.notes = (
                    (pkg_w.notes or "") +
                    f"; auto-normalized: packaged was {diff:.3f} {net_w.unit} lighter than net"
                ).lstrip("; ")

    # ── 4. Description junk removal ───────────────────────────────────────
    for field_name in ['short_description', 'marketing_description']:
        field = getattr(model.descriptions, field_name, None)
        if field and field.value is not None:
            raw = str(field.value)
            if _is_junk(raw):
                corrections.append(f"descriptions.{field_name}: removed junk value '{raw[:40]}'")
                field.value = None

    return corrections


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
