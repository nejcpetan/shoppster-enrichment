"""
Pipeline Node: Search (Phase 2)
Agent role: Find product pages via web search, classify URLs by source type.
Tools: Tavily Search, Claude Haiku 4.5
"""

import os
import json
import logging
from datetime import datetime
from tavily import TavilyClient
from db import get_db_connection, update_step, append_log
from utils.llm import classify_with_schema
from schemas import SearchResultList, ProductClassification

logger = logging.getLogger("pipeline.search")


async def search_node(state: dict) -> dict:
    """
    LangGraph node: Phase 2 — Search.
    Finds product pages via Tavily, classifies URLs via Claude.
    """
    product_id = state["product_id"]

    logger.info(f"[Product {product_id}] ▶ SEARCH — Finding product pages")
    update_step(product_id, "searching", "Loading product data...")

    # Load product + classification
    conn = get_db_connection()
    product_row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not product_row:
        conn.close()
        return {"error": f"Product {product_id} not found"}

    product = dict(product_row)
    conn.close()

    classification = json.loads(product['classification_result']) if product['classification_result'] else None
    if not classification:
        return {"error": "Product must be classified before searching"}

    brand = classification.get('brand')
    model = classification.get('model_number', '')
    product_type = classification.get('product_type', '')
    ean = product['ean']

    # Build search queries
    queries = []
    if brand and model:
        queries.append(f"{brand} {model} specifications")
        queries.append(f"{brand} {model} {ean}")
    elif brand:
        queries.append(f"{brand} {product['product_name']} specifications")
    else:
        queries.append(f"{product['product_name']} {ean}")
    queries.append(f"{ean}")  # EAN-only fallback

    tavily_key = os.getenv("TAVILY_API_KEY")
    if not tavily_key:
        return {"error": "TAVILY_API_KEY not found"}

    client = TavilyClient(api_key=tavily_key)

    # Run searches
    all_results = []
    for q in queries[:3]:
        update_step(product_id, "searching", f"Searching: {q[:50]}...")
        try:
            logger.info(f"[Product {product_id}]   Tavily search: '{q}'")
            response = client.search(query=q, max_results=7)
            num_results = len(response.get('results', []))
            logger.info(f"[Product {product_id}]   → {num_results} results")
            all_results.extend(response.get('results', []))

            append_log(product_id, {
                "timestamp": datetime.now().isoformat(),
                "phase": "search", "step": "tavily_search", "status": "success",
                "details": f"Query '{q}' → {num_results} results",
                "credits_used": {"tavily": 1}
            })

            if num_results >= 3:
                break
        except Exception as e:
            logger.warning(f"[Product {product_id}]   Search failed for '{q}': {e}")
            append_log(product_id, {
                "timestamp": datetime.now().isoformat(),
                "phase": "search", "step": "tavily_search", "status": "error",
                "details": f"Query '{q}' failed: {str(e)}"
            })

    # Deduplicate
    seen_urls = set()
    unique_results = []
    for r in all_results:
        if r['url'] not in seen_urls:
            seen_urls.add(r['url'])
            unique_results.append(r)

    if not unique_results:
        append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "search", "step": "no_results", "status": "warning",
            "details": "No search results found"
        })
        conn = get_db_connection()
        conn.execute(
            "UPDATE products SET search_result = ?, current_step = 'No results found', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps({"results": []}), product_id)
        )
        conn.commit()
        conn.close()
        return {"has_search_results": False}

    # Classify URLs via Claude
    logger.info(f"[Product {product_id}]   Classifying {len(unique_results)} URLs via Claude...")
    update_step(product_id, "searching", f"Classifying {len(unique_results)} URLs...")

    system_prompt = """You are classifying web search results for a product data enrichment pipeline.

For each URL, determine the source_type:
- "manufacturer": Brand's own website (e.g., texas-garden.com for Texas, makita.com for Makita)
- "authorized_distributor": Large, reputable distributors (agrieuro.com, toolnation.com, amazon.com)
- "third_party": Smaller retailers, comparison sites, forums
- "irrelevant": Not related to the product, wrong product, spam

Return a JSON array. Sort: manufacturer first, then authorized_distributor, then third_party. Exclude irrelevant. Limit to top 5 URLs."""

    user_prompt = f"""Product: {brand} {model} (EAN: {ean})
Product type: {product_type}

Search results to classify:
"""
    for r in unique_results[:10]:
        user_prompt += f"- {r['url']} | {r['title']}\n"

    try:
        classified_list = classify_with_schema(
            prompt=user_prompt,
            system=system_prompt,
            schema=SearchResultList,
            model="haiku"
        )

        type_counts = {}
        for r in classified_list.results:
            type_counts[r.source_type] = type_counts.get(r.source_type, 0) + 1

        summary = ', '.join(f'{v} {k}' for k, v in type_counts.items())
        logger.info(f"[Product {product_id}]   ✓ Classified: {summary}")
        append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "search", "step": "url_classification", "status": "success",
            "details": f"Classified {len(classified_list.results)} URLs: {summary}",
            "credits_used": {"claude_tokens": 500}
        })

        # Save results
        result_json = classified_list.model_dump_json()
        conn = get_db_connection()
        conn.execute(
            "UPDATE products SET search_result = ?, current_step = 'Search complete', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (result_json, product_id)
        )
        conn.commit()
        conn.close()

        return {"has_search_results": len(classified_list.results) > 0}

    except Exception as e:
        logger.error(f"[Product {product_id}]   ✗ URL classification FAILED: {e}")
        append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "search", "step": "url_classification", "status": "error",
            "details": str(e)
        })
        return {"error": str(e)}
