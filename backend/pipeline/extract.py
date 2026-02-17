"""
Pipeline Node: Extract (Phase 3) — v3

Two-pass extraction architecture:
  Pass 1: Structured data (dimensions, color, COO, images)
  Pass 2: Content extraction (descriptions, features, tech specs, warranty)
  Plus: regex-based PDF/document link extraction

Image filtering: Deterministic (HTTP HEAD + URL heuristics) — no AI calls.
Only Gemini call: 1× color detection on the best product image.

Tools: Firecrawl, Claude Haiku 4.5, Gemini 2.0 Flash (color vision only), Tavily (COO search)
"""

import os
import re
import json
import logging
import asyncio
import httpx
from datetime import datetime
from typing import List, Type, Dict, Any
from urllib.parse import urljoin, urlparse
from firecrawl import FirecrawlApp
from pydantic import BaseModel
from tavily import TavilyClient
from db import get_db_connection, update_step, append_log
from utils.llm import classify_with_schema, get_raw_client, HAIKU_MODEL
from schemas import (
    EnrichedProduct, EnrichedField, ProductClassification,
    DimensionsExtraction, ContentExtraction, TechnicalSpec,
    ProductDimensions, DimensionSet, ProductDescriptions,
    TechnicalData, WarrantyInfo, ProductDocument, ProductDocuments,
)

logger = logging.getLogger("pipeline.extract")


# ─── Image Filtering Constants ────────────────────────────────────────────────

VALID_IMG_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.webp', '.gif']
INVALID_IMG_PATTERNS = [
    '.pdf', '.svg',
    '/manual/', '/usermanual/', '/documentation/',
    '/icon/', '/icons/', 'favicon', 'logo', 'sprite', 'placeholder',
    '1x1', 'pixel', 'spacer', 'blank', 'transparent',
    '/flag/', '/flags/', 'flag-', 'flag_',
    'rating', 'star-', 'stars-', 'star_', 'stars_',
    'badge', 'ribbon', 'seal', 'sticker',
    'social', 'facebook', 'twitter', 'instagram', 'linkedin', 'youtube', 'pinterest', 'tiktok',
    'payment', 'visa', 'mastercard', 'paypal', 'stripe', 'amex',
    'arrow', 'chevron', 'caret', 'close', 'hamburger', 'menu-',
    'search-icon', 'cart-icon', 'basket-', 'checkout',
    'loading', 'spinner', 'ajax-loader',
    'avatar', 'profile-pic', 'user-icon',
    'banner', 'ad-', 'ad_', 'advertisement',
    'newsletter', 'subscribe', 'popup',
    '/cms/', '/static/icons/', '/assets/icons/',
    'carousel-arrow', 'slider-arrow', 'nav-',
    'checkmark', 'tick', 'cross', 'x-icon',
    'share-', 'share_', 'email-icon', 'print-icon',
    'trustpilot', 'google-review',
]

# Deterministic image filter thresholds
MIN_IMAGE_SIZE_BYTES = 45_000      # 45 KB — filters thumbnails, icons, spacers
MAX_IMAGE_SIZE_BYTES = 20_000_000  # 20 MB — filters oversized assets
MAX_IMAGES_TO_CHECK = 20
MAX_IMAGES_TO_KEEP = 8


# ─── Utility Functions ────────────────────────────────────────────────────────

def _is_valid_image_url(url: str) -> bool:
    """Check if a URL is likely a product image (not an icon/logo/badge)."""
    url_lower = url.lower()
    if any(p in url_lower for p in INVALID_IMG_PATTERNS):
        return False
    if any(url_lower.endswith(ext) or f'{ext}?' in url_lower for ext in VALID_IMG_EXTENSIONS):
        return True
    if re.search(r'\.(jpg|jpeg|png|webp|gif)(\?|$)', url_lower):
        return True
    return False


def _extract_all_image_urls(markdown: str, base_url: str = "") -> List[str]:
    """Extract all product image URLs from markdown content."""
    md_images = re.findall(r'!\[.*?\]\((.*?)\)', markdown)
    url_images = re.findall(
        r'https?://[^\s\)\"\']+\.(?:jpg|jpeg|png|webp|gif)(?:\?[^\s\)\"\']*)?',
        markdown, re.IGNORECASE
    )
    all_urls = set()
    for url in md_images + url_images:
        url = url.strip()
        if url.startswith('http') and _is_valid_image_url(url):
            if '1x1' not in url and 'spacer' not in url.lower():
                all_urls.add(url)
    return list(all_urls)


def _extract_pdf_links(markdown: str, page_url: str) -> List[Dict[str, str]]:
    """
    Extract all PDF/document links from markdown content using regex.
    Returns list of {title, url, source_page}.
    """
    pdf_links = []
    seen_urls = set()

    # Pattern 1: Markdown links to PDFs — [title](url.pdf)
    md_pdf = re.findall(r'\[([^\]]+)\]\((https?://[^\)]+\.pdf[^\)]*)\)', markdown, re.IGNORECASE)
    for title, url in md_pdf:
        url_clean = url.strip()
        if url_clean not in seen_urls:
            seen_urls.add(url_clean)
            pdf_links.append({"title": title.strip(), "url": url_clean, "source_page": page_url})

    # Pattern 2: Bare PDF URLs
    bare_pdfs = re.findall(r'(https?://[^\s\)\"\']+\.pdf(?:\?[^\s\)\"\']*)?)', markdown, re.IGNORECASE)
    for url in bare_pdfs:
        url_clean = url.strip()
        if url_clean not in seen_urls:
            seen_urls.add(url_clean)
            path = urlparse(url_clean).path
            filename = path.split('/')[-1].replace('.pdf', '').replace('-', ' ').replace('_', ' ')
            pdf_links.append({"title": filename or "Document", "url": url_clean, "source_page": page_url})

    # Pattern 3: Links with download-related text near .pdf
    download_patterns = re.findall(
        r'\[([^\]]*(?:manual|datasheet|download|specification|certificate|safety|brochure|guide|instruction)[^\]]*)\]\((https?://[^\)]+)\)',
        markdown, re.IGNORECASE
    )
    for title, url in download_patterns:
        url_clean = url.strip()
        if url_clean not in seen_urls:
            seen_urls.add(url_clean)
            pdf_links.append({"title": title.strip(), "url": url_clean, "source_page": page_url})

    return pdf_links


def _classify_document_type(title: str, url: str) -> str:
    """Classify a document type based on title and URL patterns. Pure heuristic, no LLM."""
    combined = (title + " " + url).lower()
    if any(k in combined for k in ['manual', 'instruction', 'guide', 'user guide', 'navodila', 'anleitung']):
        return "manual"
    if any(k in combined for k in ['datasheet', 'data sheet', 'specification', 'spec sheet', 'technical data', 'datenblatt']):
        return "datasheet"
    if any(k in combined for k in ['certificate', 'certifikat', 'compliance', 'declaration', 'konformität']):
        return "certificate"
    if any(k in combined for k in ['warranty', 'garancija', 'garantie', 'garanzia']):
        return "warranty"
    if any(k in combined for k in ['safety', 'sicherheit', 'msds', 'sds', 'varnost']):
        return "safety"
    if any(k in combined for k in ['brochure', 'catalog', 'catalogue', 'prospekt', 'katalog', 'flyer']):
        return "brochure"
    return "other"


def _detect_language(title: str, url: str) -> str | None:
    """Detect document language from filename/URL patterns."""
    combined = (title + " " + url).lower()
    if any(k in combined for k in ['_sl', '/sl/', '-si', '_si', 'slovenš', 'slovenski']):
        return "sl"
    if any(k in combined for k in ['_de', '/de/', '-de', 'deutsch', 'german']):
        return "de"
    if any(k in combined for k in ['_en', '/en/', '-en', 'english']):
        return "en"
    if any(k in combined for k in ['_fr', '/fr/', '-fr', 'français', 'french']):
        return "fr"
    if any(k in combined for k in ['_it', '/it/', '-it', 'italiano', 'italian']):
        return "it"
    return None


def _shorten_url(url: str, max_len: int = 40) -> str:
    """Shorten URL for display in step messages."""
    try:
        parsed = urlparse(url)
        return parsed.netloc
    except Exception:
        return url[:max_len]


# ─── Main Extract Node ────────────────────────────────────────────────────────

async def extract_node(state: dict) -> dict:
    """
    LangGraph node: Phase 3 — Extraction (v3).

    Two-pass extraction per URL:
      Pass 1: Structured data (dimensions, color, COO, images)
      Pass 2: Content (descriptions, features, tech specs, warranty)
    Plus: Regex-based PDF/document link collection
    Then: Merge all sources, deterministic image filtering, gap fill (color + COO)
    """
    product_id = state["product_id"]
    cost_tracker = state.get("cost_tracker")

    update_step(product_id, "extracting", "Loading search results...")

    # Load context
    conn = get_db_connection()
    product_row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not product_row:
        conn.close()
        return {"error": f"Product {product_id} not found"}

    product = dict(product_row)
    conn.close()

    if not product['search_result']:
        return {"error": "Search results missing. Run Phase 2 first."}

    search_results = json.loads(product['search_result'])
    classification = ProductClassification.model_validate_json(product['classification_result'])

    # Get top URLs (exclude irrelevant)
    urls_to_process = [
        r for r in search_results['results']
        if r['source_type'] != 'irrelevant'
    ][:3]

    dimension_extractions: List[DimensionsExtraction] = []
    content_extractions: List[ContentExtraction] = []
    content_source_urls: List[str] = []
    all_discovered_images: List[str] = []
    all_pdf_links: List[Dict[str, str]] = []
    fc_api_key = os.getenv("FIRECRAWL_API_KEY")

    if not fc_api_key:
        append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "extract", "step": "init", "status": "error",
            "details": "FIRECRAWL_API_KEY missing"
        })
        return {"error": "FIRECRAWL_API_KEY missing"}

    firecrawl = FirecrawlApp(api_key=fc_api_key)

    # ── Process each URL ──────────────────────────────────────────────────
    for result in urls_to_process:
        url = result['url']
        source_type = result['source_type']

        try:
            logger.info(f"[Product {product_id}]   Scraping {_shorten_url(url)} ({source_type})...")
            update_step(product_id, "extracting", f"Scraping {_shorten_url(url)}...")

            scraped = firecrawl.scrape(url, formats=['markdown'])

            # Track Firecrawl cost
            if cost_tracker:
                cost_tracker.add_api_call("firecrawl", credits=1, phase="extract_scrape")

            markdown = ''
            if hasattr(scraped, 'markdown') and scraped.markdown:
                markdown = scraped.markdown[:40000]
            elif isinstance(scraped, dict):
                markdown = scraped.get('markdown', '')[:40000]

            if not markdown:
                append_log(product_id, {
                    "timestamp": datetime.now().isoformat(),
                    "phase": "extract", "step": f"scrape_{url}", "status": "warning",
                    "details": f"No content from {url}"
                })
                continue

            # Extract images from this page
            page_images = _extract_all_image_urls(markdown, url)
            all_discovered_images.extend(page_images)

            # Extract PDF links from this page
            page_pdfs = _extract_pdf_links(markdown, url)
            all_pdf_links.extend(page_pdfs)

            # Determine confidence level based on source type
            confidence_level = "official" if source_type == "manufacturer" else "third_party"

            # ── Pass 1: Structured Dimensions ─────────────────────────────
            logger.info(f"[Product {product_id}]   Pass 1: Structured extraction from {_shorten_url(url)}...")
            update_step(product_id, "extracting", f"Pass 1: Dimensions from {_shorten_url(url)}...")

            pass1_system = f"""You are extracting PHYSICAL PRODUCT DATA from a product page.
Source type: {source_type} → set confidence to "{confidence_level}".

EXTRACT NET (product itself) AND PACKAGED (with box/packaging) dimensions separately.
Many product pages list both — look for labels like "Net weight", "Package weight", "Brutto/Netto",
"Teža izdelka / Teža paketa", "Product dimensions / Package dimensions".

For each dimension field:
- value: The numeric value (e.g. 45.2). NO UNITS in the value.
- unit: The original unit (cm, mm, kg, g, L, mL, etc.)
- confidence: "{confidence_level}"
- source_url: "{url}"

Also extract:
- color: The product's primary color.
- country_of_origin: Manufacturing country if mentioned.
- Extract the highest-resolution PRODUCT IMAGE URL (not PDFs, icons, or logos).
- image_urls: List ALL product image URLs found on the page."""

            pass1_user = f"""Extract dimensions, weight, and physical data from this page.

Product: {classification.brand} {classification.model_number}
EAN: {product['ean']}
Source URL: {url}
Source type: {source_type}

Page content:
{markdown[:25000]}"""

            try:
                dim_extraction, usage = classify_with_schema(
                    prompt=pass1_user,
                    system=pass1_system,
                    schema=DimensionsExtraction,
                    model="haiku",
                    return_usage=True
                )
                dimension_extractions.append(dim_extraction)

                # Track cost
                if cost_tracker:
                    cost_tracker.add_llm_call(
                        usage["model"], usage["input_tokens"], usage["output_tokens"],
                        phase="extract_pass1"
                    )

                # Collect LLM-extracted images
                if dim_extraction.image_urls:
                    for img in dim_extraction.image_urls:
                        if _is_valid_image_url(img):
                            all_discovered_images.append(img)

                append_log(product_id, {
                    "timestamp": datetime.now().isoformat(),
                    "phase": "extract", "step": "pass1_structured", "status": "success",
                    "details": f"Pass 1 done for {_shorten_url(url)} ({source_type})",
                    "credits_used": {"claude_in": usage["input_tokens"], "claude_out": usage["output_tokens"]}
                })
            except Exception as e:
                logger.warning(f"[Product {product_id}]   Pass 1 failed for {_shorten_url(url)}: {e}")
                append_log(product_id, {
                    "timestamp": datetime.now().isoformat(),
                    "phase": "extract", "step": "pass1_structured", "status": "error",
                    "details": f"Pass 1 failed for {_shorten_url(url)}: {e}"
                })

            # ── Pass 2: Content Extraction ────────────────────────────────
            logger.info(f"[Product {product_id}]   Pass 2: Content extraction from {_shorten_url(url)}...")
            update_step(product_id, "extracting", f"Pass 2: Content from {_shorten_url(url)}...")

            pass2_system = f"""You are extracting marketing and technical content from a product page.

EXTRACT THE FOLLOWING:

1. SHORT DESCRIPTION:
   The brief product summary, usually 1-2 sentences at the top of the product page.
   Copy it verbatim from the page. If in a non-English language, keep the original language.

2. MARKETING DESCRIPTION:
   The longer marketing/promotional text describing product features and benefits.
   This is typically found below the product title. Keep original language.
   If there are multiple marketing paragraphs, combine them.

3. FEATURES:
   A list of product features/highlights. Often presented as bullet points on the page.
   Extract each feature as a separate string. Keep original language.

4. TECHNICAL SPECIFICATIONS:
   ALL key-value pairs from specification/technical data tables on the page.
   Common examples: motor power, voltage, RPM, cutting width, tank capacity, noise level,
   blade length, cable length, speed settings, battery info, etc.
   For each spec: name (exactly as shown), value (exactly as shown), unit (if separate).
   Set confidence to "{confidence_level}" and source_url to "{url}".

5. WARRANTY:
   Look for warranty terms: "garancija", "Garantie", "warranty", "jamstvo", "garanzia".
   Extract duration (e.g., "2 years", "24 mesecev"), type, and any conditions.

RULES:
- DO NOT fabricate content. Only extract what is actually on the page.
- Keep original language text (Slovenian, German, English, etc.) — do NOT translate.
- If a field is not present on the page, leave it empty."""

            pass2_user = f"""Extract descriptions, features, technical specs, and warranty from this page.

Product: {classification.brand} {classification.model_number}
EAN: {product['ean']}
Source URL: {url}

Page content:
{markdown[:30000]}"""

            try:
                content_extraction, usage = classify_with_schema(
                    prompt=pass2_user,
                    system=pass2_system,
                    schema=ContentExtraction,
                    model="haiku",
                    return_usage=True
                )
                content_extractions.append(content_extraction)
                content_source_urls.append(url)

                # Track cost
                if cost_tracker:
                    cost_tracker.add_llm_call(
                        usage["model"], usage["input_tokens"], usage["output_tokens"],
                        phase="extract_pass2"
                    )

                spec_count = len(content_extraction.technical_specs)
                feat_count = len(content_extraction.features)
                logger.info(f"[Product {product_id}]   Pass 2 done: {spec_count} specs, {feat_count} features")

                append_log(product_id, {
                    "timestamp": datetime.now().isoformat(),
                    "phase": "extract", "step": "pass2_content", "status": "success",
                    "details": f"Pass 2 done for {_shorten_url(url)}: {spec_count} tech specs, {feat_count} features, warranty={bool(content_extraction.warranty_duration)}",
                    "credits_used": {"claude_in": usage["input_tokens"], "claude_out": usage["output_tokens"]}
                })
            except Exception as e:
                logger.warning(f"[Product {product_id}]   Pass 2 failed for {_shorten_url(url)}: {e}")
                append_log(product_id, {
                    "timestamp": datetime.now().isoformat(),
                    "phase": "extract", "step": "pass2_content", "status": "error",
                    "details": f"Pass 2 failed for {_shorten_url(url)}: {e}"
                })

        except Exception as e:
            logger.warning(f"[Product {product_id}]   Scrape failed for {_shorten_url(url)}: {e}, retrying...")
            # Retry once
            try:
                update_step(product_id, "extracting", f"Retrying {_shorten_url(url)}...")
                await asyncio.sleep(3)
                scraped = firecrawl.scrape(url, formats=['markdown'])

                # Track retry scrape cost
                if cost_tracker:
                    cost_tracker.add_api_call("firecrawl", credits=1, phase="extract_scrape_retry")

                markdown = ''
                if hasattr(scraped, 'markdown') and scraped.markdown:
                    markdown = scraped.markdown[:40000]
                elif isinstance(scraped, dict):
                    markdown = scraped.get('markdown', '')[:40000]
                if markdown:
                    page_images = _extract_all_image_urls(markdown, url)
                    all_discovered_images.extend(page_images)
                    page_pdfs = _extract_pdf_links(markdown, url)
                    all_pdf_links.extend(page_pdfs)
                    append_log(product_id, {
                        "timestamp": datetime.now().isoformat(),
                        "phase": "extract", "step": "retry_success", "status": "success",
                        "details": f"Retry scrape succeeded for {_shorten_url(url)}"
                    })
            except Exception as retry_e:
                append_log(product_id, {
                    "timestamp": datetime.now().isoformat(),
                    "phase": "extract", "step": "scrape_error", "status": "error",
                    "details": f"Failed {_shorten_url(url)} after retry: {retry_e}"
                })

    # ── Merge Dimensions ──────────────────────────────────────────────────
    logger.info(f"[Product {product_id}]   Merging data from {len(dimension_extractions)} sources...")
    update_step(product_id, "extracting", "Merging structured data...")

    merged = EnrichedProduct()

    # Merge dimension extractions into net/packaged sets
    merged.dimensions = _merge_dimension_extractions(dimension_extractions)

    # Merge color and COO
    merged.color = _pick_best_field([ext.color for ext in dimension_extractions])
    merged.country_of_origin = _pick_best_field([ext.country_of_origin for ext in dimension_extractions])
    merged.image_url = _pick_best_field([ext.image_url for ext in dimension_extractions])

    # ── Merge Content ─────────────────────────────────────────────────────
    update_step(product_id, "extracting", "Merging content data...")
    merged.descriptions = _merge_content_descriptions(content_extractions, content_source_urls)
    merged.technical_data = _merge_technical_specs(content_extractions)
    merged.warranty = _merge_warranty(content_extractions, content_source_urls)

    # ── Process PDFs/Documents ────────────────────────────────────────────
    update_step(product_id, "extracting", "Processing document links...")
    documents = []
    seen_doc_urls = set()
    for pdf in all_pdf_links:
        if pdf["url"] not in seen_doc_urls:
            seen_doc_urls.add(pdf["url"])
            doc_type = _classify_document_type(pdf["title"], pdf["url"])
            language = _detect_language(pdf["title"], pdf["url"])
            documents.append(ProductDocument(
                title=pdf["title"],
                url=pdf["url"],
                doc_type=doc_type,
                language=language,
                source_page=pdf["source_page"]
            ))
    merged.documents = ProductDocuments(documents=documents)

    if documents:
        logger.info(f"[Product {product_id}]   Found {len(documents)} documents/PDFs")
        append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "extract", "step": "documents", "status": "success",
            "details": f"Found {len(documents)} documents: {', '.join(d.doc_type for d in documents)}"
        })

    # ── Deduplicate and store images ──────────────────────────────────────
    unique_images = list(dict.fromkeys(all_discovered_images))[:20]
    merged.image_urls = unique_images

    logger.info(f"[Product {product_id}]   ✓ Merged: {len(dimension_extractions)} dim sources, {len(content_extractions)} content sources, {len(unique_images)} images, {len(documents)} docs")
    append_log(product_id, {
        "timestamp": datetime.now().isoformat(),
        "phase": "extract", "step": "merge", "status": "success",
        "details": f"Merged {len(dimension_extractions)} dim + {len(content_extractions)} content sources, {len(unique_images)} images, {len(documents)} docs"
    })

    # ── Deterministic Image Filtering (no AI) ─────────────────────────────
    if unique_images:
        update_step(product_id, "extracting", f"🖼️ Filtering {len(unique_images)} images (HTTP check)...")
        cleaned_images = await _filter_images_deterministic(unique_images, product_id)
        merged.image_urls = cleaned_images
    else:
        merged.image_urls = []

    # Validate primary image
    if merged.image_url and merged.image_url.value:
        image_url_str = str(merged.image_url.value).lower()
        if not _is_valid_image_url(image_url_str) or (merged.image_urls and str(merged.image_url.value) not in merged.image_urls):
            merged.image_url.value = None
            merged.image_url.confidence = 'not_found'

    # If no primary image but cleaned images exist, use first one
    if (not merged.image_url or not merged.image_url.value) and merged.image_urls:
        merged.image_url = EnrichedField(
            value=merged.image_urls[0],
            confidence="third_party",
            notes="Auto-selected from verified product images"
        )

    # ── Gap Fill: Country of Origin ───────────────────────────────────────
    if (merged.country_of_origin is None or
            merged.country_of_origin.value is None or
            merged.country_of_origin.confidence == 'not_found'):
        update_step(product_id, "extracting", "Searching for country of origin...")
        coo_result = await _fill_country_of_origin(classification.brand, product['ean'], product_id, cost_tracker)
        if coo_result:
            merged.country_of_origin = coo_result

    # ── Gap Fill: Color (Gemini Vision — single call) ─────────────────────
    if (merged.color is None or
            merged.color.value is None or
            merged.color.confidence == 'not_found'):
        image_for_color = None
        if merged.image_url and merged.image_url.value:
            image_for_color = str(merged.image_url.value)

        if image_for_color:
            logger.info(f"[Product {product_id}]   🔍 Calling Gemini Vision for color detection...")
            update_step(product_id, "extracting", "🔍 Gemini Vision: detecting color...")
            color_result = _fill_color_gemini(image_for_color, product_id, cost_tracker)
            if color_result:
                merged.color = color_result
            else:
                color_result = _fill_color_from_name(product['product_name'], product_id)
                if color_result:
                    merged.color = color_result
        else:
            color_result = _fill_color_from_name(product['product_name'], product_id)
            if color_result:
                merged.color = color_result

    # ── Save ──────────────────────────────────────────────────────────────
    conn = get_db_connection()
    conn.execute(
        "UPDATE products SET extraction_result = ?, current_step = 'Extraction complete', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (merged.model_dump_json(), product_id)
    )
    conn.commit()
    conn.close()

    return {}


# ─── Merge Helpers ────────────────────────────────────────────────────────────

def _pick_best_field(fields: List[EnrichedField]) -> EnrichedField:
    """
    Survivorship logic for a single field across multiple sources.
    Priority: official > third_party (multi-agree) > third_party (single) > inferred
    """
    candidates = [f for f in fields if f and f.value is not None and f.confidence != 'not_found']
    if not candidates:
        return EnrichedField()

    official = [c for c in candidates if c.confidence == 'official']
    third_party = [c for c in candidates if c.confidence == 'third_party']
    inferred = [c for c in candidates if c.confidence == 'inferred']

    if official:
        return official[0]
    elif third_party:
        if len(third_party) > 1:
            values = set(str(c.value) for c in third_party)
            if len(values) == 1:
                best = third_party[0].model_copy()
                best.notes = f"Confirmed by {len(third_party)} sources"
                return best
            else:
                best = third_party[0].model_copy()
                best.notes = f"Sources disagree: {', '.join(values)}"
                return best
        return third_party[0]
    elif inferred:
        return inferred[0]
    return EnrichedField()


def _merge_dimension_extractions(extractions: List[DimensionsExtraction]) -> ProductDimensions:
    """Merge dimension extractions from multiple pages into net/packaged sets."""
    dims = ProductDimensions()

    if not extractions:
        return dims

    # Net dimensions
    net_fields = {
        'height': [e.net_height for e in extractions],
        'length': [e.net_length for e in extractions],
        'width': [e.net_width for e in extractions],
        'depth': [e.net_depth for e in extractions],
        'weight': [e.net_weight for e in extractions],
        'diameter': [e.net_diameter for e in extractions],
        'volume': [e.net_volume for e in extractions],
    }
    for field_name, candidates in net_fields.items():
        setattr(dims.net, field_name, _pick_best_field(candidates))

    # Packaged dimensions
    pkg_fields = {
        'height': [e.packaged_height for e in extractions],
        'length': [e.packaged_length for e in extractions],
        'width': [e.packaged_width for e in extractions],
        'depth': [e.packaged_depth for e in extractions],
        'weight': [e.packaged_weight for e in extractions],
    }
    for field_name, candidates in pkg_fields.items():
        setattr(dims.packaged, field_name, _pick_best_field(candidates))

    return dims


def _merge_content_descriptions(
    extractions: List[ContentExtraction],
    source_urls: List[str]
) -> ProductDescriptions:
    """Merge content extractions, preferring longest/most complete content."""
    desc = ProductDescriptions()

    if not extractions:
        return desc

    # Short description: prefer first non-empty, or longest
    short_descs = [(e.short_description, url) for e, url in zip(extractions, source_urls) if e.short_description]
    if short_descs:
        best = max(short_descs, key=lambda x: len(x[0]))
        desc.short_description = EnrichedField(
            value=best[0], source_url=best[1], confidence="third_party"
        )

    # Marketing description: prefer longest
    mktg_descs = [(e.marketing_description, url) for e, url in zip(extractions, source_urls) if e.marketing_description]
    if mktg_descs:
        best = max(mktg_descs, key=lambda x: len(x[0]))
        desc.marketing_description = EnrichedField(
            value=best[0], source_url=best[1], confidence="third_party"
        )

    # Features: deduplicate, merge all
    all_features = []
    seen_features = set()
    for e in extractions:
        for f in e.features:
            f_lower = f.strip().lower()
            if f_lower and f_lower not in seen_features:
                seen_features.add(f_lower)
                all_features.append(f.strip())
    desc.features = all_features

    return desc


def _merge_technical_specs(extractions: List[ContentExtraction]) -> TechnicalData:
    """Merge technical specs from multiple extractions, deduplicate by name."""
    tech = TechnicalData()

    if not extractions:
        return tech

    seen_specs = {}  # name_lower -> TechnicalSpec
    for e in extractions:
        for spec in e.technical_specs:
            key = spec.name.strip().lower()
            if key not in seen_specs:
                seen_specs[key] = spec
            else:
                # Prefer official confidence
                existing = seen_specs[key]
                if spec.confidence == "official" and existing.confidence != "official":
                    seen_specs[key] = spec

    tech.specs = list(seen_specs.values())
    return tech


def _merge_warranty(
    extractions: List[ContentExtraction],
    source_urls: List[str]
) -> WarrantyInfo:
    """Merge warranty info from multiple extractions."""
    warranty = WarrantyInfo()

    for e, url in zip(extractions, source_urls):
        if e.warranty_duration:
            warranty.duration = EnrichedField(
                value=e.warranty_duration,
                source_url=url,
                confidence="third_party"
            )
            warranty.type = e.warranty_type or None
            warranty.conditions = e.warranty_conditions or None
            warranty.source_url = url
            warranty.confidence = "third_party"
            break  # take the first non-empty warranty

    return warranty


# ─── Deterministic Image Filtering ───────────────────────────────────────────

async def _filter_images_deterministic(
    image_urls: List[str],
    product_id: int
) -> List[str]:
    """
    Filter images using HTTP HEAD requests + URL heuristics.
    No AI calls. Checks: file size, content-type, URL patterns.

    Strategy:
    1. Parallel HTTP HEAD requests for speed
    2. Enforce MIN_IMAGE_SIZE_BYTES (45KB) to drop low-res/thumbnails
    3. Sort surviving images by file size (largest = highest quality first)
    4. Keep top MAX_IMAGES_TO_KEEP
    """
    if not image_urls:
        return []

    logger.info(f"[Product {product_id}]   🖼️ Deterministic image filtering: {len(image_urls)} candidates")

    kept_candidates = []  # List of (url, size_bytes)
    checked = 0
    skipped_reasons: Dict[str, int] = {}

    candidates = image_urls[:MAX_IMAGES_TO_CHECK]
    checked = len(candidates)

    async def check_url(client, url):
        try:
            resp = await client.head(url)
            content_type = resp.headers.get("content-type", "").lower()
            cl_str = resp.headers.get("content-length", "0")
            try:
                content_length = int(cl_str)
            except (ValueError, TypeError):
                content_length = 0

            # Must be an image
            if "image" not in content_type:
                return (None, "not_image")

            # Reject SVGs
            if "svg" in content_type:
                return (None, "svg")

            # Check size bounds
            if content_length > 0:
                if content_length < MIN_IMAGE_SIZE_BYTES:
                    return (None, "too_small")
                if content_length > MAX_IMAGE_SIZE_BYTES:
                    return (None, "too_large")
            
            # If content_length is 0 (missing header), we give benefit of doubt 
            # but treat as small size for sorting (1 byte)
            size_for_sort = content_length if content_length > 0 else 1
            return ((url, size_for_sort), None)

        except Exception:
            # On error, we keep it as fallback (size 0)
            return ((url, 0), "head_error_kept")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(5.0, connect=3.0),
        follow_redirects=True,
        limits=httpx.Limits(max_connections=10)
    ) as client:
        tasks = [check_url(client, url) for url in candidates]
        results = await asyncio.gather(*tasks)

    for res, skip_reason in results:
        if res:
            kept_candidates.append(res)
        if skip_reason:
            if skip_reason == "head_error_kept":
                # We kept it, but tracked the error nature? 
                # Actually my logic above returns ((url, 0), "head_error_kept")
                # So if res is present, we keep it.
                pass
            else:
                skipped_reasons[skip_reason] = skipped_reasons.get(skip_reason, 0) + 1

    # Sort: Largest size first (prioritize high resolution)
    kept_candidates.sort(key=lambda x: x[1], reverse=True)

    # Extract clean URLs
    kept_urls = [x[0] for x in kept_candidates[:MAX_IMAGES_TO_KEEP]]

    removed_count = checked - len(kept_urls)
    skip_summary = ", ".join(f"{v} {k}" for k, v in skipped_reasons.items()) if skipped_reasons else "all passed"
    logger.info(f"[Product {product_id}]   ✓ Image filtering: kept {len(kept_urls)}/{checked} sorted by size (removed: {skip_summary})")

    append_log(product_id, {
        "timestamp": datetime.now().isoformat(),
        "phase": "extract", "step": "image_filter", "status": "success",
        "details": f"Kept {len(kept_urls)}/{checked} images. Removed: {skip_summary}"
    })

    return kept_urls


# ─── Gap Fill Agents ──────────────────────────────────────────────────────────

def _fill_color_gemini(image_url: str, product_id: int, cost_tracker=None) -> EnrichedField | None:
    """Use Gemini Flash Vision to detect product color from image. Single call."""
    try:
        from utils.gemini_vision import detect_color_from_image

        result = detect_color_from_image(image_url)

        # Track Gemini cost (estimated tokens since Vertex AI response
        # doesn't always expose usage for generateContent)
        if cost_tracker:
            cost_tracker.add_llm_call(
                "gemini_flash", input_tokens=500, output_tokens=50,
                phase="extract_color"
            )

        if result and result.value:
            append_log(product_id, {
                "timestamp": datetime.now().isoformat(),
                "phase": "extract", "step": "gemini_color_agent", "status": "success",
                "details": f"Gemini detected color: {result.value}",
                "credits_used": {"gemini_flash": 1}
            })
            return result
        else:
            append_log(product_id, {
                "timestamp": datetime.now().isoformat(),
                "phase": "extract", "step": "gemini_color_agent", "status": "warning",
                "details": "Gemini could not determine color"
            })
            return None
    except Exception as e:
        append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "extract", "step": "gemini_color_agent", "status": "error",
            "details": f"Gemini color agent failed: {str(e)}"
        })
        return None


def _fill_color_from_name(product_name: str, product_id: int) -> EnrichedField | None:
    """Fallback: infer color from product name keywords."""
    color_map = {
        "črn": "black", "črna": "black", "črni": "black",
        "bel": "white", "bela": "white", "beli": "white",
        "rdeč": "red", "rdeča": "red",
        "modr": "blue", "modra": "blue", "modri": "blue",
        "zelen": "green", "zelena": "green",
        "rumen": "yellow", "rumena": "yellow",
        "oranžn": "orange", "oranžna": "orange",
        "siv": "gray", "siva": "gray",
        "black": "black", "white": "white", "red": "red",
        "blue": "blue", "green": "green", "yellow": "yellow",
        "orange": "orange", "silver": "silver", "gray": "gray", "grey": "gray",
    }
    name_lower = product_name.lower()
    for keyword, color in color_map.items():
        if keyword in name_lower:
            result = EnrichedField(
                value=color, confidence="inferred",
                notes=f"From product name keyword: '{keyword}'"
            )
            append_log(product_id, {
                "timestamp": datetime.now().isoformat(),
                "phase": "extract", "step": "color_from_name", "status": "success",
                "details": f"Color inferred: {color}"
            })
            return result
    return None


async def _fill_country_of_origin(brand: str | None, ean: str, product_id: int, cost_tracker=None) -> EnrichedField | None:
    """Specialized search for country of origin."""
    if not brand:
        return None

    tavily_key = os.getenv("TAVILY_API_KEY")
    if not tavily_key:
        return None

    try:
        client = TavilyClient(api_key=tavily_key)
        query = f"{brand} country of origin manufacturing"
        response = client.search(query=query, max_results=5)

        # Track Tavily cost
        if cost_tracker:
            cost_tracker.add_api_call("tavily", credits=1, phase="extract_coo")

        snippets = "\n".join([
            f"- {r['title']}: {r.get('content', '')[:300]}"
            for r in response.get('results', [])
        ])

        if not snippets:
            return None

        system_prompt = """Determine the country of origin (manufacturing country) for this product.
If you find it, set confidence to "third_party" if from a reliable source, "inferred" if guessing from brand info.
If you cannot determine, return value as null."""

        result, usage = classify_with_schema(
            prompt=f"Brand: {brand}\nEAN: {ean}\n\nSearch results:\n{snippets}",
            system=system_prompt,
            schema=EnrichedField,
            model="haiku",
            return_usage=True
        )

        # Track Claude cost
        if cost_tracker:
            cost_tracker.add_llm_call(
                usage["model"], usage["input_tokens"], usage["output_tokens"],
                phase="extract_coo"
            )

        append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "extract", "step": "country_of_origin_search", "status": "success",
            "details": f"COO: {result.value} ({result.confidence})",
            "credits_used": {"tavily": 1, "claude_in": usage["input_tokens"], "claude_out": usage["output_tokens"]}
        })

        return result if result.value else None

    except Exception as e:
        append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "extract", "step": "country_of_origin_search", "status": "error",
            "details": str(e)
        })
        return None
