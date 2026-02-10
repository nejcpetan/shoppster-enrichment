
import os
import json
import asyncio
from datetime import datetime
from typing import List, Dict, Any, Type
from firecrawl import FirecrawlApp
from pydantic import BaseModel
from tavily import TavilyClient
from db import get_db_connection
from utils.llm import classify_with_schema, get_client, VERTEX_MODEL
from schemas import (
    StandardProduct, AccessoryProduct, LiquidProduct, 
    EnrichedField, ProductClassification
)

# Phase 3: Extraction Pipeline

def _append_log(product_id: int, entry: dict):
    """Append a log entry to the product's enrichment_log."""
    conn = get_db_connection()
    product = conn.execute("SELECT enrichment_log FROM products WHERE id = ?", (product_id,)).fetchone()
    existing = json.loads(product['enrichment_log']) if product and product['enrichment_log'] else []
    existing.append(entry)
    conn.execute("UPDATE products SET enrichment_log = ? WHERE id = ?", (json.dumps(existing), product_id))
    conn.commit()
    conn.close()


async def extract_product_data(product_id: int):
    """
    Orchestrates Scraping -> Extraction -> Merging -> Gap Filling for a product.
    """
    # Update status to extracting
    conn = get_db_connection()
    conn.execute("UPDATE products SET status = 'extracting', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (product_id,))
    conn.commit()

    # 1. Load Context
    product_row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not product_row:
        conn.close()
        raise ValueError(f"Product {product_id} not found")
        
    product = dict(product_row)
    conn.close()
    
    # Check dependencies
    if not product['search_result']:
        raise ValueError("Search results missing. Run Phase 2 first.")
        
    search_results = json.loads(product['search_result'])
    classification: ProductClassification = ProductClassification.model_validate_json(product['classification_result'])
    
    # 2. Select Schema
    schema_map = {
        "standard_product": StandardProduct,
        "accessory": AccessoryProduct,
        "liquid": LiquidProduct,
        "electronics": StandardProduct,
        "soft_good": StandardProduct,
        "other": StandardProduct
    }
    TargetSchema = schema_map.get(classification.product_type, StandardProduct)

    # 3. Scrape & Extract (Top 3 URLs)
    urls_to_process = [
        r for r in search_results['results'] 
        if r['source_type'] != 'irrelevant'
    ][:3]

    extractions = []
    fc_api_key = os.getenv("FIRECRAWL_API_KEY")
    
    if not fc_api_key:
        _append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "extract", "step": "init", "status": "error",
            "details": "FIRECRAWL_API_KEY missing"
        })
        return

    firecrawl = FirecrawlApp(api_key=fc_api_key)

    for result in urls_to_process:
        url = result['url']
        try:
            print(f"Scraping {url}...")
            scraped = firecrawl.scrape(url, formats=['markdown'])
            # The new API returns a Document object with a markdown attribute
            markdown = ''
            if hasattr(scraped, 'markdown') and scraped.markdown:
                markdown = scraped.markdown[:20000]
            elif isinstance(scraped, dict):
                markdown = scraped.get('markdown', '')[:20000]
            
            if not markdown:
                _append_log(product_id, {
                    "timestamp": datetime.now().isoformat(),
                    "phase": "extract", "step": f"scrape_{url}", "status": "warning",
                    "details": f"No content returned from {url}"
                })
                continue

            _append_log(product_id, {
                "timestamp": datetime.now().isoformat(),
                "phase": "extract", "step": f"scrape", "status": "success",
                "details": f"Scraped {url} — {len(markdown)} chars",
                "credits_used": {"firecrawl": 1}
            })

            # Claude Extraction
            system_prompt = f"""You are extracting product specifications from a web page for a data enrichment pipeline.

You will receive:
- The product identity (brand, model number, EAN)
- The source URL and its type ({result['source_type']})
- The page content as markdown
- A JSON schema describing the exact fields to extract

YOUR JOB: Find the values for each field in the schema from the page content.

CRITICAL RULES:

1. DIMENSIONS — PRODUCT vs PACKAGING:
   Look carefully at whether dimensions are for the PRODUCT ITSELF or for the SHIPPING BOX / PACKAGING.
   Clues for packaging: "package dimensions", "shipping dimensions", "box size", "karton", "embalaža", "Verpackung".
   Clues for product: "product dimensions", "tool dimensions", "net dimensions", specifications table without packaging mention.
   Set dimension_type to "product" or "packaging" accordingly. If unclear, set to "packaging" (safer assumption).

2. CONFIDENCE:
   - If source_type is "manufacturer": set confidence to "official"
   - If source_type is "authorized_distributor": set confidence to "third_party"
   - If source_type is "third_party": set confidence to "third_party"
   - If you're INFERRING a value (not directly stated): set confidence to "inferred"

3. UNITS:
   Always record the ORIGINAL unit from the page in the "unit" field (e.g., "mm", "cm", "inches", "kg", "lbs", "L").
   Do NOT convert units. We normalize later.

4. DO NOT HALLUCINATE:
   If a value is NOT on the page, leave it as null/not_found. Do NOT make up values.

5. SOURCE URL:
   Set source_url to "{url}" for every field you extract from this page.

6. IMAGES:
   Extract the highest-resolution PRODUCT IMAGE URL from the page.
   CRITICAL IMAGE RULES:
   - NEVER extract images from PDFs (URLs ending in .pdf or containing /manual/ or /usermanual/)
   - ONLY extract direct image URLs (ending in .jpg, .jpeg, .png, .webp, etc.)
   - Prioritize main product photos over icons, thumbnails, or diagrams
   - Avoid manual diagrams, instruction images, or technical drawings
   - If the source URL is a PDF or manual, set image_url to null
   - Look for gallery images, hero images, or primary product photography
"""
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
                schema=TargetSchema
            )
            extractions.append(extraction)

            _append_log(product_id, {
                "timestamp": datetime.now().isoformat(),
                "phase": "extract", "step": "llm_extract", "status": "success",
                "details": f"Extracted fields from {url} ({result['source_type']})",
                "credits_used": {"claude_tokens": 1500}
            })

        except Exception as e:
            print(f"Failed to process {url}: {e}")
            # Retry once after 3 seconds
            try:
                await asyncio.sleep(3)
                scraped = firecrawl.scrape(url, formats=['markdown'])
                markdown = ''
                if hasattr(scraped, 'markdown') and scraped.markdown:
                    markdown = scraped.markdown[:20000]
                elif isinstance(scraped, dict):
                    markdown = scraped.get('markdown', '')[:20000]
                if markdown:
                    extraction = classify_with_schema(
                        prompt=user_prompt, system=system_prompt, schema=TargetSchema
                    )
                    extractions.append(extraction)
                    _append_log(product_id, {
                        "timestamp": datetime.now().isoformat(),
                        "phase": "extract", "step": "retry_success", "status": "success",
                        "details": f"Retry succeeded for {url}"
                    })
            except Exception as retry_e:
                _append_log(product_id, {
                    "timestamp": datetime.now().isoformat(),
                    "phase": "extract", "step": f"scrape_error", "status": "error",
                    "details": f"Failed {url} after retry: {retry_e}"
                })

    # 4. Merge Results
    merged_data = merge_extractions(extractions, TargetSchema)

    _append_log(product_id, {
        "timestamp": datetime.now().isoformat(),
        "phase": "extract", "step": "merge", "status": "success",
        "details": f"Merged {len(extractions)} source extractions"
    })

    # 4.5. Validate and clean image URL
    if hasattr(merged_data, 'image_url') and merged_data.image_url and merged_data.image_url.value:
        image_url = str(merged_data.image_url.value).lower()
        # Filter out PDFs, manuals, and non-image URLs
        invalid_patterns = ['.pdf', '/manual/', '/usermanual/', '/documentation/']
        valid_extensions = ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.svg']
        
        is_invalid = any(pattern in image_url for pattern in invalid_patterns)
        is_valid_extension = any(image_url.endswith(ext) for ext in valid_extensions)
        
        if is_invalid or not is_valid_extension:
            _append_log(product_id, {
                "timestamp": datetime.now().isoformat(),
                "phase": "extract", "step": "image_validation", "status": "warning",
                "details": f"Filtered invalid image URL: {merged_data.image_url.value}"
            })
            merged_data.image_url.value = None
            merged_data.image_url.confidence = 'not_found'


    # 5. Gap Filling — Country of Origin
    if hasattr(merged_data, 'country_of_origin') and (
        merged_data.country_of_origin is None or 
        merged_data.country_of_origin.value is None or
        merged_data.country_of_origin.confidence == 'not_found'
    ):
        coo_result = await _fill_country_of_origin(classification.brand, product['ean'], product_id)
        if coo_result:
            merged_data.country_of_origin = coo_result

    # 6. Gap Filling — Color
    if hasattr(merged_data, 'color') and (
        merged_data.color is None or
        merged_data.color.value is None or
        merged_data.color.confidence == 'not_found'
    ):
        # Try from image first
        image_url = None
        if hasattr(merged_data, 'image_url') and merged_data.image_url and merged_data.image_url.value:
            image_url = str(merged_data.image_url.value)
        
        color_result = _fill_color(image_url, product['product_name'], product_id)
        if color_result:
            merged_data.color = color_result

    # 7. Save
    conn = get_db_connection()
    conn.execute(
        "UPDATE products SET extraction_result = ?, status = 'enriching', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (merged_data.model_dump_json(), product_id)
    )
    conn.commit()
    conn.close()
    
    return merged_data


async def _fill_country_of_origin(brand: str | None, ean: str, product_id: int) -> EnrichedField | None:
    """Specialized search for country of origin when extraction didn't find it."""
    if not brand:
        return None
    
    tavily_key = os.getenv("TAVILY_API_KEY")
    if not tavily_key:
        return None
    
    try:
        client = TavilyClient(api_key=tavily_key)
        query = f"{brand} country of origin manufacturing"
        print(f"[COO Search] {query}")
        response = client.search(query=query, max_results=5)
        
        snippets = "\n".join([
            f"- {r['title']}: {r.get('content', '')[:300]}" 
            for r in response.get('results', [])
        ])
        
        if not snippets:
            return None
        
        system_prompt = """Determine the country of origin (manufacturing country) for this product based on the search results.

RULES:
- If the search results explicitly state where this specific product is manufactured, report that.
- If only the brand's general manufacturing country is mentioned, report that with notes="inferred from brand, not product-specific".
- Set confidence to "third_party" if from a reliable source, "inferred" if you're guessing from brand info.
- If you truly cannot determine, return value as null.

Return JSON: {"value": "country name or null", "source_url": "url or null", "confidence": "third_party or inferred", "notes": "explanation"}"""

        user_prompt = f"Brand: {brand}\nEAN: {ean}\n\nSearch results:\n{snippets}"
        
        result = classify_with_schema(prompt=user_prompt, system=system_prompt, schema=EnrichedField)
        
        _append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "extract", "step": "country_of_origin_search", "status": "success",
            "details": f"Country of origin: {result.value} ({result.confidence})",
            "credits_used": {"tavily": 1, "claude_tokens": 500}
        })
        
        return result if result.value else None
        
    except Exception as e:
        print(f"COO search failed: {e}")
        _append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "extract", "step": "country_of_origin_search", "status": "error",
            "details": str(e)
        })
        return None


def _fill_color(image_url: str | None, product_name: str, product_id: int) -> EnrichedField | None:
    """Determine color from image or product name."""
    
    # Try image-based color detection
    if image_url:
        try:
            client = get_client()
            response = client.messages.create(
                model=VERTEX_MODEL,
                max_tokens=256,
                system="""What is the primary color of this product? 
Return JSON: {"value": "color name", "unit": null, "confidence": "inferred", "notes": "determined from product image", "source_url": null, "dimension_type": "na"}
If the product is metallic/silver (like a wire brush or metal tool), say "silver/metallic".
If the product is a liquid in a container, describe the container color.
Return ONLY the JSON.""",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "url", "url": image_url}},
                        {"type": "text", "text": "What is the primary color of this product?"}
                    ]
                }]
            )
            content = response.content[0].text
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            
            result = EnrichedField.model_validate_json(content)
            
            _append_log(product_id, {
                "timestamp": datetime.now().isoformat(),
                "phase": "extract", "step": "color_from_image", "status": "success",
                "details": f"Color from image: {result.value}",
                "credits_used": {"claude_tokens": 300}
            })
            
            if result.value:
                return result
                
        except Exception as e:
            print(f"Color from image failed: {e}")
            _append_log(product_id, {
                "timestamp": datetime.now().isoformat(),
                "phase": "extract", "step": "color_from_image", "status": "error",
                "details": str(e)
            })
    
    # Fallback: extract color from product name
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
                notes=f"Extracted from product name keyword: '{keyword}'"
            )
            _append_log(product_id, {
                "timestamp": datetime.now().isoformat(),
                "phase": "extract", "step": "color_from_name", "status": "success",
                "details": f"Color inferred from name: {color}"
            })
            return result
    
    return None


def merge_extractions(extractions: List[BaseModel], schema_cls: Type[BaseModel]) -> BaseModel:
    """
    Simple survivorship logic:
    1. Prefer 'official' confidence.
    2. Prefer first non-null if no official.
    """
    if not extractions:
        return schema_cls()

    merged = schema_cls()
    fields = merged.model_fields.keys()

    for field_name in fields:
        best_field = EnrichedField()
        
        candidates = []
        for ext in extractions:
            val = getattr(ext, field_name)
            if isinstance(val, EnrichedField) and val.value is not None:
                candidates.append(val)
        
        official = [c for c in candidates if c.confidence == 'official']
        third_party = [c for c in candidates if c.confidence == 'third_party']
        inferred = [c for c in candidates if c.confidence == 'inferred']
        
        if official:
            best_field = official[0]
        elif third_party:
            # If multiple third_party agree, note it
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
