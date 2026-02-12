"""
Pipeline Node: Extract (Phase 3)
Agent role: Scrape product pages, extract structured data, collect ALL images, fill gaps.
Tools: Firecrawl, Claude Haiku 4.5, Gemini 2.0 Flash (color vision), Tavily (COO search)
"""

import os
import re
import json
import logging
import asyncio
from datetime import datetime
from typing import List, Type
from firecrawl import FirecrawlApp
from pydantic import BaseModel
from tavily import TavilyClient
from db import get_db_connection, update_step, append_log
from utils.llm import classify_with_schema, get_raw_client, HAIKU_MODEL
from schemas import (
    StandardProduct, AccessoryProduct, LiquidProduct,
    EnrichedField, ProductClassification
)

logger = logging.getLogger("pipeline.extract")


# Valid image extensions — NOTE: SVGs excluded because they are almost always icons/logos
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


def _is_valid_image_url(url: str) -> bool:
    """Check if a URL is likely a product image (not an icon/logo/badge)."""
    url_lower = url.lower()
    if any(p in url_lower for p in INVALID_IMG_PATTERNS):
        return False
    if any(url_lower.endswith(ext) or f'{ext}?' in url_lower for ext in VALID_IMG_EXTENSIONS):
        return True
    # Allow URLs with image extensions before query params
    if re.search(r'\.(jpg|jpeg|png|webp|gif)(\?|$)', url_lower):
        return True
    return False


def _extract_all_image_urls(markdown: str, base_url: str = "") -> List[str]:
    """Extract all image URLs from markdown content."""
    # Match markdown image syntax: ![alt](url)
    md_images = re.findall(r'!\[.*?\]\((.*?)\)', markdown)
    # Match raw image URLs
    url_images = re.findall(r'https?://[^\s\)\"\']+\.(?:jpg|jpeg|png|webp|gif|svg)(?:\?[^\s\)\"\']*)?', markdown, re.IGNORECASE)

    all_urls = set()
    for url in md_images + url_images:
        url = url.strip()
        if url.startswith('http') and _is_valid_image_url(url):
            # Skip tiny images (likely tracking pixels)
            if '1x1' not in url and 'spacer' not in url.lower():
                all_urls.add(url)

    return list(all_urls)


async def extract_node(state: dict) -> dict:
    """
    LangGraph node: Phase 3 — Extraction.
    Scrapes pages → LLM extraction → collect ALL images → merge → gap filling (Gemini color agent).
    """
    product_id = state["product_id"]

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

    # Select schema based on product type
    schema_map = {
        "standard_product": StandardProduct,
        "accessory": AccessoryProduct,
        "liquid": LiquidProduct,
        "electronics": StandardProduct,
        "soft_good": StandardProduct,
        "other": StandardProduct
    }
    TargetSchema = schema_map.get(classification.product_type, StandardProduct)

    # Get top URLs (exclude irrelevant)
    urls_to_process = [
        r for r in search_results['results']
        if r['source_type'] != 'irrelevant'
    ][:3]

    extractions = []
    all_discovered_images: List[str] = []
    fc_api_key = os.getenv("FIRECRAWL_API_KEY")

    if not fc_api_key:
        append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "extract", "step": "init", "status": "error",
            "details": "FIRECRAWL_API_KEY missing"
        })
        return {"error": "FIRECRAWL_API_KEY missing"}

    firecrawl = FirecrawlApp(api_key=fc_api_key)

    for result in urls_to_process:
        url = result['url']
        try:
            logger.info(f"[Product {product_id}]   Scraping {_shorten_url(url)} ({result['source_type']})...")
            update_step(product_id, "extracting", f"Scraping {_shorten_url(url)}...")

            scraped = firecrawl.scrape(url, formats=['markdown'])
            markdown = ''
            if hasattr(scraped, 'markdown') and scraped.markdown:
                markdown = scraped.markdown[:20000]
            elif isinstance(scraped, dict):
                markdown = scraped.get('markdown', '')[:20000]

            if not markdown:
                append_log(product_id, {
                    "timestamp": datetime.now().isoformat(),
                    "phase": "extract", "step": f"scrape_{url}", "status": "warning",
                    "details": f"No content from {url}"
                })
                continue

            # Extract ALL images from this page
            page_images = _extract_all_image_urls(markdown, url)
            all_discovered_images.extend(page_images)

            append_log(product_id, {
                "timestamp": datetime.now().isoformat(),
                "phase": "extract", "step": "scrape", "status": "success",
                "details": f"Scraped {_shorten_url(url)} — {len(markdown)} chars, {len(page_images)} images found",
                "credits_used": {"firecrawl": 1}
            })

            # LLM Extraction
            logger.info(f"[Product {product_id}]   Calling Claude Haiku 4.5 for extraction from {_shorten_url(url)}...")
            update_step(product_id, "extracting", f"Extracting data from {_shorten_url(url)}...")

            system_prompt = f"""You are extracting product specifications from a web page for a data enrichment pipeline.

You will receive the product identity, the source URL and its type ({result['source_type']}), the page content, and a JSON schema.

YOUR JOB: Find the values for each field in the schema from the page content.

CRITICAL RULES:

1. DIMENSIONS — PRODUCT vs PACKAGING:
   Look carefully at whether dimensions are for the PRODUCT ITSELF or for the SHIPPING BOX / PACKAGING.
   Clues for packaging: "package dimensions", "shipping dimensions", "box size", "karton", "embalaža", "Verpackung".
   Clues for product: "product dimensions", "tool dimensions", specifications table without packaging mention.
   Set dimension_type to "product" or "packaging" accordingly. If unclear, set to "packaging" (safer).

2. CONFIDENCE:
   - If source_type is "manufacturer": set confidence to "official"
   - If source_type is "authorized_distributor": set confidence to "third_party"
   - If source_type is "third_party": set confidence to "third_party"
   - If you're INFERRING a value: set confidence to "inferred"

3. UNITS: Record the ORIGINAL unit from the page. Do NOT convert units.

4. DO NOT HALLUCINATE: If a value is NOT on the page, leave it as null/not_found.

5. SOURCE URL: Set source_url to "{url}" for every field you extract from this page.

6. IMAGES:
   Extract the highest-resolution PRODUCT IMAGE URL from the page.
   CRITICAL IMAGE RULES:
   - NEVER extract images from PDFs (URLs ending in .pdf or containing /manual/ or /usermanual/)
   - ONLY extract direct image URLs (ending in .jpg, .jpeg, .png, .webp, etc.)
   - Prioritize main product photos over icons, thumbnails, or diagrams
   - If the source URL is a PDF or manual, set image_url to null

7. image_urls field: List ALL product image URLs you can find on the page (not logos, icons, or banners)."""

            user_prompt = f"""Extract product data from this page.

Product: {classification.brand} {classification.model_number}
EAN: {product['ean']}
Source URL: {url}
Source type: {result['source_type']}

Page content (truncated):
{markdown}
"""

            extraction = classify_with_schema(
                prompt=user_prompt,
                system=system_prompt,
                schema=TargetSchema,
                model="haiku"
            )
            extractions.append(extraction)

            # Collect LLM-extracted images too
            if hasattr(extraction, 'image_urls') and extraction.image_urls:
                for img in extraction.image_urls:
                    if _is_valid_image_url(img):
                        all_discovered_images.append(img)

            append_log(product_id, {
                "timestamp": datetime.now().isoformat(),
                "phase": "extract", "step": "llm_extract", "status": "success",
                "details": f"Extracted from {_shorten_url(url)} ({result['source_type']})",
                "credits_used": {"claude_tokens": 1200}
            })

        except Exception as e:
            logger.warning(f"[Product {product_id}]   Scrape failed for {_shorten_url(url)}: {e}, retrying...")
            # Retry once
            try:
                update_step(product_id, "extracting", f"Retrying {_shorten_url(url)}...")
                await asyncio.sleep(3)
                scraped = firecrawl.scrape(url, formats=['markdown'])
                markdown = ''
                if hasattr(scraped, 'markdown') and scraped.markdown:
                    markdown = scraped.markdown[:20000]
                elif isinstance(scraped, dict):
                    markdown = scraped.get('markdown', '')[:20000]
                if markdown:
                    page_images = _extract_all_image_urls(markdown, url)
                    all_discovered_images.extend(page_images)

                    extraction = classify_with_schema(
                        prompt=user_prompt, system=system_prompt, schema=TargetSchema, model="haiku"
                    )
                    extractions.append(extraction)
                    append_log(product_id, {
                        "timestamp": datetime.now().isoformat(),
                        "phase": "extract", "step": "retry_success", "status": "success",
                        "details": f"Retry succeeded for {_shorten_url(url)}"
                    })
            except Exception as retry_e:
                append_log(product_id, {
                    "timestamp": datetime.now().isoformat(),
                    "phase": "extract", "step": "scrape_error", "status": "error",
                    "details": f"Failed {_shorten_url(url)} after retry: {retry_e}"
                })

    # Merge results
    logger.info(f"[Product {product_id}]   Merging data from {len(extractions)} sources...")
    update_step(product_id, "extracting", "Merging data from sources...")
    merged_data = _merge_extractions(extractions, TargetSchema)

    # Deduplicate and store all discovered images
    unique_images = list(dict.fromkeys(all_discovered_images))  # preserves order, removes dupes
    merged_data.image_urls = unique_images[:20]  # cap at 20

    logger.info(f"[Product {product_id}]   ✓ Merged: {len(extractions)} sources, {len(unique_images)} images")
    append_log(product_id, {
        "timestamp": datetime.now().isoformat(),
        "phase": "extract", "step": "merge", "status": "success",
        "details": f"Merged {len(extractions)} sources, {len(unique_images)} total images discovered"
    })

    # ===== IMAGE CLEANING PIPELINE =====
    # Stage 1: Gemini Vision describes each candidate image
    # Stage 2: Claude filters to keep only actual product images
    if unique_images and len(unique_images) > 0:
        cleaned_images = await _clean_images_with_vision(
            unique_images[:15],  # cap at 15 to limit API calls
            product['product_name'],
            classification,
            product_id
        )
        merged_data.image_urls = cleaned_images
    else:
        merged_data.image_urls = []

    # Validate primary image URL
    if hasattr(merged_data, 'image_url') and merged_data.image_url and merged_data.image_url.value:
        image_url_str = str(merged_data.image_url.value).lower()
        # Check if primary image survived the cleaning
        if not _is_valid_image_url(image_url_str) or (merged_data.image_urls and str(merged_data.image_url.value) not in merged_data.image_urls):
            append_log(product_id, {
                "timestamp": datetime.now().isoformat(),
                "phase": "extract", "step": "image_validation", "status": "warning",
                "details": f"Filtered invalid primary image: {merged_data.image_url.value}"
            })
            merged_data.image_url.value = None
            merged_data.image_url.confidence = 'not_found'

    # If no primary image but we have cleaned images, use the first one
    if (not merged_data.image_url or not merged_data.image_url.value) and merged_data.image_urls:
        merged_data.image_url = EnrichedField(
            value=merged_data.image_urls[0],
            confidence="third_party",
            notes="Auto-selected from verified product images"
        )

    # Gap fill: Country of Origin
    if hasattr(merged_data, 'country_of_origin') and (
        merged_data.country_of_origin is None or
        merged_data.country_of_origin.value is None or
        merged_data.country_of_origin.confidence == 'not_found'
    ):
        update_step(product_id, "extracting", "Searching for country of origin...")
        coo_result = await _fill_country_of_origin(classification.brand, product['ean'], product_id)
        if coo_result:
            merged_data.country_of_origin = coo_result

    # Gap fill: Color — Gemini Vision Agent
    if hasattr(merged_data, 'color') and (
        merged_data.color is None or
        merged_data.color.value is None or
        merged_data.color.confidence == 'not_found'
    ):
        # Try Gemini Vision first if we have an image
        image_for_color = None
        if merged_data.image_url and merged_data.image_url.value:
            image_for_color = str(merged_data.image_url.value)

        if image_for_color:
            logger.info(f"[Product {product_id}]   🔍 Calling Gemini 3.0 Flash Vision for color detection...")
            update_step(product_id, "extracting", "🔍 Gemini Vision Agent: detecting color...")
            color_result = _fill_color_gemini(image_for_color, product_id)
            if color_result:
                merged_data.color = color_result
            else:
                # Fallback to name-based detection
                color_result = _fill_color_from_name(product['product_name'], product_id)
                if color_result:
                    merged_data.color = color_result
        else:
            # No image available, try name-based
            color_result = _fill_color_from_name(product['product_name'], product_id)
            if color_result:
                merged_data.color = color_result

    # Save
    conn = get_db_connection()
    conn.execute(
        "UPDATE products SET extraction_result = ?, current_step = 'Extraction complete', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (merged_data.model_dump_json(), product_id)
    )
    conn.commit()
    conn.close()

    return {}


def _shorten_url(url: str, max_len: int = 40) -> str:
    """Shorten URL for display in step messages."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.netloc
    except:
        return url[:max_len]


async def _clean_images_with_vision(
    image_urls: List[str],
    product_name: str,
    classification,
    product_id: int
) -> List[str]:
    """
    Two-stage image cleaning pipeline:
    1. Gemini 3.0 Flash describes each image
    2. Claude Haiku filters to keep only actual product images
    
    Returns cleaned list of image URLs.
    """
    from utils.gemini_vision import describe_images

    if not image_urls:
        return []

    logger.info(f"[Product {product_id}]   🖼️ Image cleaning: {len(image_urls)} candidates")
    update_step(product_id, "extracting", f"🖼️ Analyzing {len(image_urls)} images with Gemini Vision...")

    # Stage 1: Gemini describes each image
    described = describe_images(image_urls, product_name)
    
    if not described:
        logger.warning(f"[Product {product_id}]   Image description failed, keeping all images")
        return image_urls

    # Log descriptions
    for d in described:
        short_url = d['url'].split('/')[-1][:40]
        logger.info(f"[Product {product_id}]     {short_url}: {d['description']}")

    append_log(product_id, {
        "timestamp": datetime.now().isoformat(),
        "phase": "extract", "step": "image_describe", "status": "success",
        "details": f"Gemini described {len(described)} images",
        "credits_used": {"gemini_flash": len(described)}
    })

    # Stage 2: Claude filters based on descriptions + product context
    update_step(product_id, "extracting", "🧹 Filtering non-product images...")

    brand = classification.brand if hasattr(classification, 'brand') else (classification.get('brand') if isinstance(classification, dict) else '')
    product_type = classification.product_type if hasattr(classification, 'product_type') else (classification.get('product_type') if isinstance(classification, dict) else '')

    image_list_text = "\n".join([
        f"{i+1}. URL: {d['url']}\n   Description: {d['description']}"
        for i, d in enumerate(described)
    ])

    system_prompt = """You are filtering images for a product data enrichment pipeline.

Given a list of images with their AI-generated descriptions, determine which are ACTUAL PRODUCT IMAGES and which are NOT.

KEEP only images that are:
- Photos of the actual product (the tool, machine, accessory, container, etc.)
- Product packaging showing the product
- Close-ups of product features or parts
- Product in use / action shots

REMOVE images that are:
- Website UI elements (icons, arrows, buttons, close buttons, hamburger menus)
- Company/brand logos
- Country flags
- Rating stars or review graphics  
- Social media icons
- Payment method logos
- Banners, ads, or promotional graphics
- User avatars or profile pictures
- Generic stock photos not showing the specific product
- Thumbnails that are too small to be useful (described as tiny/icon-sized)
- Navigation elements

Return a JSON object with:
{
  "kept": [1, 3, 5],  // indices (1-based) of images to KEEP
  "reasoning": "brief explanation"
}"""

    user_prompt = f"""Product: {brand} — {product_name}
Product type: {product_type}

Images to evaluate:
{image_list_text}

Which images are actual product images? Return the indices to KEEP."""

    try:
        from pydantic import BaseModel, Field

        class ImageFilterResult(BaseModel):
            kept: List[int] = Field(description="1-based indices of images to keep")
            reasoning: str = Field(description="Brief explanation of filtering decisions")

        result = classify_with_schema(
            prompt=user_prompt,
            system=system_prompt,
            schema=ImageFilterResult,
            model="haiku"
        )

        # Map indices back to URLs
        kept_urls = []
        for idx in result.kept:
            if 1 <= idx <= len(described):
                kept_urls.append(described[idx - 1]["url"])

        removed_count = len(described) - len(kept_urls)
        logger.info(f"[Product {product_id}]   ✓ Image cleaning: kept {len(kept_urls)}, removed {removed_count}")
        logger.info(f"[Product {product_id}]     Reasoning: {result.reasoning}")

        append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "extract", "step": "image_filter", "status": "success",
            "details": f"Kept {len(kept_urls)}/{len(described)} images. Removed {removed_count} non-product images. {result.reasoning}",
            "credits_used": {"claude_tokens": 500}
        })

        return kept_urls

    except Exception as e:
        logger.warning(f"[Product {product_id}]   Image filtering failed: {e}, keeping all")
        append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "extract", "step": "image_filter", "status": "warning",
            "details": f"Image filtering failed ({e}), keeping all images"
        })
        return image_urls


def _fill_color_gemini(image_url: str, product_id: int) -> EnrichedField | None:
    """Use Gemini 3.0 Flash Vision to detect product color from image."""
    try:
        from utils.gemini_vision import detect_color_from_image

        result = detect_color_from_image(image_url)

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


async def _fill_country_of_origin(brand: str | None, ean: str, product_id: int) -> EnrichedField | None:
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

        snippets = "\n".join([
            f"- {r['title']}: {r.get('content', '')[:300]}"
            for r in response.get('results', [])
        ])

        if not snippets:
            return None

        system_prompt = """Determine the country of origin (manufacturing country) for this product.
If you find it, set confidence to "third_party" if from a reliable source, "inferred" if guessing from brand info.
If you cannot determine, return value as null."""

        result = classify_with_schema(
            prompt=f"Brand: {brand}\nEAN: {ean}\n\nSearch results:\n{snippets}",
            system=system_prompt,
            schema=EnrichedField,
            model="haiku"
        )

        append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "extract", "step": "country_of_origin_search", "status": "success",
            "details": f"COO: {result.value} ({result.confidence})",
            "credits_used": {"tavily": 1, "claude_tokens": 400}
        })

        return result if result.value else None

    except Exception as e:
        append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "extract", "step": "country_of_origin_search", "status": "error",
            "details": str(e)
        })
        return None


def _merge_extractions(extractions: List[BaseModel], schema_cls: Type[BaseModel]) -> BaseModel:
    """
    Survivorship logic:
    1. Prefer 'official' confidence
    2. If multiple third_party agree, note it
    3. Prefer first non-null if no official
    """
    if not extractions:
        return schema_cls()

    merged = schema_cls()
    fields = merged.model_fields.keys()

    for field_name in fields:
        if field_name == 'image_urls':
            continue  # handled separately

        best_field = EnrichedField()

        candidates = []
        for ext in extractions:
            val = getattr(ext, field_name, None)
            if isinstance(val, EnrichedField) and val.value is not None:
                candidates.append(val)

        official = [c for c in candidates if c.confidence == 'official']
        third_party = [c for c in candidates if c.confidence == 'third_party']
        inferred = [c for c in candidates if c.confidence == 'inferred']

        if official:
            best_field = official[0]
        elif third_party:
            if len(third_party) > 1:
                values = set(str(c.value) for c in third_party)
                if len(values) == 1:
                    best_field = third_party[0]
                    best_field.notes = f"Confirmed by {len(third_party)} sources"
                else:
                    best_field = third_party[0]
                    best_field.notes = f"Sources disagree: {', '.join(values)}"
            else:
                best_field = third_party[0]
        elif inferred:
            best_field = inferred[0]

        setattr(merged, field_name, best_field)

    return merged
