
import os
import json
from datetime import datetime
from tavily import TavilyClient
from pydantic import BaseModel
from typing import List, Literal
from db import get_db_connection
from utils.llm import classify_with_schema
from utils.ean_lookup import lookup_ean

# Models for Search
class SearchResultURL(BaseModel):
    url: str
    title: str
    source_type: Literal["manufacturer", "authorized_distributor", "third_party", "irrelevant"]
    reasoning: str

class SearchResultList(BaseModel):
    results: List[SearchResultURL]


def _append_log(product_id: int, entry: dict):
    """Append a log entry to the product's enrichment_log."""
    conn = get_db_connection()
    product = conn.execute("SELECT enrichment_log FROM products WHERE id = ?", (product_id,)).fetchone()
    existing = json.loads(product['enrichment_log']) if product and product['enrichment_log'] else []
    existing.append(entry)
    conn.execute("UPDATE products SET enrichment_log = ? WHERE id = ?", (json.dumps(existing), product_id))
    conn.commit()
    conn.close()


async def search_product(product_id: int):
    """
    Phase 2: Search
    Finds product pages via Tavily, classifies them via Claude.
    """
    # Set granular status
    conn = get_db_connection()
    conn.execute("UPDATE products SET status = 'searching', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (product_id,))
    conn.commit()

    # 1. Load Product & Classification
    product_row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    
    if not product_row:
        conn.close()
        raise ValueError(f"Product {product_id} not found")
        
    product = dict(product_row)
    classification = json.loads(product['classification_result']) if product['classification_result'] else None
    
    if not classification:
        conn.close()
        raise ValueError("Product must be classified before searching")

    # 2. Check Brand (Fallback to EAN Lookup)
    brand = classification.get('brand')
    ean = product['ean']
    
    if not brand or classification.get('brand_confidence') == "unknown":
        print(f"Brand unknown for {product_id}, attempting EAN lookup...")
        ean_data = await lookup_ean(ean)
        
        if ean_data and ean_data.get('brand'):
            brand = ean_data['brand']
            classification['brand'] = brand
            classification['brand_confidence'] = 'likely'
            classification['reasoning'] += f" [Updated via EAN Lookup: {brand}]"
            
            conn.execute("UPDATE products SET classification_result = ? WHERE id = ?", 
                         (json.dumps(classification), product_id))
            conn.commit()
            
            _append_log(product_id, {
                "timestamp": datetime.now().isoformat(),
                "phase": "triage", "step": "ean_lookup", "status": "success",
                "details": f"Found brand '{brand}' via barcodelookup.com",
                "credits_used": {"firecrawl": 1, "claude_tokens": 500}
            })
        else:
            _append_log(product_id, {
                "timestamp": datetime.now().isoformat(),
                "phase": "triage", "step": "ean_lookup", "status": "warning",
                "details": "EAN lookup returned no brand"
            })

    # 3. Construct Search Queries
    model = classification.get('model_number', '')
    product_type = classification.get('product_type', '')
    
    queries = []
    if brand and model:
        queries.append(f"{brand} {model} specifications")
        queries.append(f"{brand} {model} {ean}")
    elif brand:
        queries.append(f"{brand} {product['product_name']} specifications")
    else:
        queries.append(f"{product['product_name']} {ean}")
    # Fallback: EAN only
    queries.append(f"{ean}")
        
    tavily_key = os.getenv("TAVILY_API_KEY")
    if not tavily_key:
        conn.close()
        raise ValueError("TAVILY_API_KEY not found")
        
    client = TavilyClient(api_key=tavily_key)
    
    all_results = []
    tavily_credits = 0
    
    # Run searches (stop when we have enough)
    for q in queries[:3]:
        print(f"Searching: {q}")
        try:
            response = client.search(query=q, max_results=7)
            num_results = len(response.get('results', []))
            all_results.extend(response.get('results', []))
            tavily_credits += 1
            
            _append_log(product_id, {
                "timestamp": datetime.now().isoformat(),
                "phase": "search", "step": "tavily_search", "status": "success",
                "details": f"Query '{q}' returned {num_results} results",
                "credits_used": {"tavily": 1}
            })
            
            if num_results >= 3:
                break
        except Exception as e:
            print(f"Search failed for {q}: {e}")
            _append_log(product_id, {
                "timestamp": datetime.now().isoformat(),
                "phase": "search", "step": "tavily_search", "status": "error",
                "details": f"Query '{q}' failed: {str(e)}"
            })

    # Deduplicate by URL
    seen_urls = set()
    unique_results = []
    for r in all_results:
        if r['url'] not in seen_urls:
            seen_urls.add(r['url'])
            unique_results.append(r)
    
    if not unique_results:
        _append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "search", "step": "no_results", "status": "warning",
            "details": "No search results found from any query"
        })
        conn.execute("UPDATE products SET search_result = ?, status = 'enriching', updated_at = CURRENT_TIMESTAMP WHERE id = ?", 
                     (json.dumps({"results": []}), product_id))
        conn.commit()
        conn.close()
        return []
            
    # 4. Classify URLs
    system_prompt = """You are classifying web search results for a product data enrichment pipeline.

For each URL, determine the source_type:
- "manufacturer": This is the brand's own website (e.g., texas-garden.com for Texas brand, makita.com for Makita). Official product pages.
- "authorized_distributor": Large, reputable tool/product distributors. Examples: agrieuro.com, toolnation.com, amazon.com (if sold by brand).
- "third_party": Smaller retailers, comparison sites, forums. Lower trust.
- "irrelevant": Not related to the product, wrong product, spam.

CRITICAL: If you're not sure whether a site is the manufacturer, check if the domain name relates to the brand name.

Return a JSON array of objects: [{url, title, source_type, reasoning}]
Sort by priority: manufacturer first, then authorized_distributor, then third_party. Exclude irrelevant.
Limit to top 5 URLs."""

    user_prompt = f"""Product: {brand} {model} (EAN: {ean})
Product type: {product_type}

Search results to classify:
"""
    for r in unique_results[:10]:
        user_prompt += f"- {r['url']} | {r['title']}\n"

    classified_list = classify_with_schema(
        prompt=user_prompt, 
        system=system_prompt, 
        schema=SearchResultList
    )

    # Count by type for logging
    type_counts = {}
    for r in classified_list.results:
        type_counts[r.source_type] = type_counts.get(r.source_type, 0) + 1
    
    _append_log(product_id, {
        "timestamp": datetime.now().isoformat(),
        "phase": "search", "step": "url_classification", "status": "success",
        "details": f"Classified {len(classified_list.results)} URLs: {', '.join(f'{v} {k}' for k, v in type_counts.items())}",
        "credits_used": {"claude_tokens": 600}
    })

    # 5. Store Results
    result_json = classified_list.model_dump_json()
    
    conn.execute("UPDATE products SET search_result = ?, status = 'enriching', updated_at = CURRENT_TIMESTAMP WHERE id = ?", 
                 (result_json, product_id))
    conn.commit()
    conn.close()
    
    return classified_list.results
