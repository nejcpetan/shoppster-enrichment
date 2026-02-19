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
    cost_tracker = state.get("cost_tracker")

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
    manufacturer_domain = classification.get('manufacturer_domain')
    ean = product['ean']

    # Build general search queries
    queries = []
    if brand and model:
        queries.append(f"{brand} {model} specifications")
        queries.append(f"{brand} {model} {ean}")
    elif brand:
        queries.append(f"{brand} {product['product_name']} specifications")
    else:
        queries.append(f"{product['product_name']} {ean}")
    queries.append(f"{ean}")  # EAN-only fallback

    # Manufacturer base query (used in Phase 1)
    mfr_query = f"{brand} {model}".strip() if (brand and model) else (brand or product['product_name'])

    # Determine search provider
    search_provider = os.getenv("SEARCH_PROVIDER", "tavily").lower()

    all_results = []

    # ─── Provider: Tavily ─────────────────────────────────────────────────────
    if search_provider == "tavily":
        tavily_key = os.getenv("TAVILY_API_KEY")
        if not tavily_key:
            return {"error": "TAVILY_API_KEY not found"}

        client = TavilyClient(api_key=tavily_key)

        # ── Phase 1: Manufacturer-targeted search ──────────────────────────
        if manufacturer_domain:
            update_step(product_id, "searching", f"Searching manufacturer site: {manufacturer_domain}...")
            try:
                logger.info(f"[Product {product_id}]   Phase 1 (manufacturer): '{mfr_query}' on {manufacturer_domain}")
                mfr_response = client.search(
                    query=mfr_query,
                    max_results=5,
                    include_domains=[manufacturer_domain]
                )
                mfr_results = mfr_response.get('results', [])
                all_results.extend(mfr_results)

                if cost_tracker:
                    cost_tracker.add_api_call("tavily", credits=1, phase="search_manufacturer")

                append_log(product_id, {
                    "timestamp": datetime.now().isoformat(),
                    "phase": "search", "step": "tavily_manufacturer", "status": "success",
                    "details": f"Manufacturer search on {manufacturer_domain} → {len(mfr_results)} results",
                    "credits_used": {"tavily": 1}
                })
                logger.info(f"[Product {product_id}]   → {len(mfr_results)} manufacturer results")
            except Exception as e:
                logger.warning(f"[Product {product_id}]   Manufacturer search failed ({manufacturer_domain}): {e}")
                append_log(product_id, {
                    "timestamp": datetime.now().isoformat(),
                    "phase": "search", "step": "tavily_manufacturer", "status": "warning",
                    "details": f"Manufacturer search failed: {str(e)}"
                })

        # ── Phase 2: General search ────────────────────────────────────────
        # Reduce general queries if Phase 1 already found results
        max_general_queries = 2 if (manufacturer_domain and all_results) else 3
        for q in queries[:max_general_queries]:
            update_step(product_id, "searching", f"Searching (Tavily): {q[:50]}...")
            try:
                logger.info(f"[Product {product_id}]   Phase 2 (general): '{q}'")
                response = client.search(query=q, max_results=7)
                num_results = len(response.get('results', []))
                logger.info(f"[Product {product_id}]   → {num_results} results")
                all_results.extend(response.get('results', []))

                if cost_tracker:
                    cost_tracker.add_api_call("tavily", credits=1, phase="search")

                append_log(product_id, {
                    "timestamp": datetime.now().isoformat(),
                    "phase": "search", "step": "tavily_search", "status": "success",
                    "details": f"Query '{q}' → {num_results} results",
                    "credits_used": {"tavily": 1}
                })

                if len(all_results) >= 6:
                    break
            except Exception as e:
                logger.warning(f"[Product {product_id}]   Search failed for '{q}': {e}")
                append_log(product_id, {
                    "timestamp": datetime.now().isoformat(),
                    "phase": "search", "step": "tavily_search", "status": "error",
                    "details": f"Query '{q}' failed: {str(e)}"
                })

    # ─── Provider: Firecrawl ──────────────────────────────────────────────────
    elif search_provider == "firecrawl":
        fc_api_key = os.getenv("FIRECRAWL_API_KEY")
        if not fc_api_key:
            return {"error": "FIRECRAWL_API_KEY not found"}

        from firecrawl import FirecrawlApp
        app = FirecrawlApp(api_key=fc_api_key)

        def _parse_firecrawl_results(response) -> list:
            """Normalize Firecrawl search response to list of {url, title, content}."""
            def get_val(obj, key, default=None):
                if isinstance(obj, dict):
                    return obj.get(key, default)
                return getattr(obj, key, default)

            raw_items = []
            if hasattr(response, 'web'):
                raw_items = response.web
            elif isinstance(response, dict):
                if 'data' in response and isinstance(response['data'], dict):
                    raw_items = response['data'].get('web', [])
                else:
                    raw_items = response.get('web', [])
            if not raw_items and hasattr(response, 'data'):
                data_obj = response.data
                if hasattr(data_obj, 'web'):
                    raw_items = data_obj.web

            results = []
            for item in raw_items:
                url = get_val(item, 'url')
                title = get_val(item, 'title', 'No title')
                desc = get_val(item, 'description') or get_val(item, 'markdown') or ''
                results.append({'url': url, 'title': title, 'content': desc[:200]})
            return results

        # ── Phase 1: Manufacturer-targeted search ──────────────────────────
        if manufacturer_domain:
            update_step(product_id, "searching", f"Searching manufacturer site: {manufacturer_domain}...")
            try:
                site_query = f"site:{manufacturer_domain} {mfr_query}"
                logger.info(f"[Product {product_id}]   Phase 1 (manufacturer): '{site_query}'")
                mfr_response = app.search(site_query, limit=5)
                mfr_results = _parse_firecrawl_results(mfr_response)
                all_results.extend(mfr_results)

                if cost_tracker:
                    cost_tracker.add_api_call("firecrawl", credits=2, phase="search_manufacturer")

                append_log(product_id, {
                    "timestamp": datetime.now().isoformat(),
                    "phase": "search", "step": "firecrawl_manufacturer", "status": "success",
                    "details": f"Manufacturer search on {manufacturer_domain} → {len(mfr_results)} results",
                    "credits_used": {"firecrawl": 2}
                })
                logger.info(f"[Product {product_id}]   → {len(mfr_results)} manufacturer results")
            except Exception as e:
                logger.warning(f"[Product {product_id}]   Manufacturer search failed ({manufacturer_domain}): {e}")
                append_log(product_id, {
                    "timestamp": datetime.now().isoformat(),
                    "phase": "search", "step": "firecrawl_manufacturer", "status": "warning",
                    "details": f"Manufacturer search failed: {str(e)}"
                })

        # ── Phase 2: General search ────────────────────────────────────────
        max_general_queries = 2 if (manufacturer_domain and all_results) else 3
        for q in queries[:max_general_queries]:
            update_step(product_id, "searching", f"Searching (Firecrawl): {q[:50]}...")
            try:
                logger.info(f"[Product {product_id}]   Phase 2 (general): '{q}'")
                response = app.search(q, limit=7)
                results_list = _parse_firecrawl_results(response)
                num_results = len(results_list)
                logger.info(f"[Product {product_id}]   → {num_results} results")
                all_results.extend(results_list)

                if cost_tracker:
                    cost_tracker.add_api_call("firecrawl", credits=2, phase="search_query")

                append_log(product_id, {
                    "timestamp": datetime.now().isoformat(),
                    "phase": "search", "step": "firecrawl_search", "status": "success",
                    "details": f"Query '{q}' → {num_results} results",
                    "credits_used": {"firecrawl": 2}
                })

                if len(all_results) >= 6:
                    break

            except Exception as e:
                logger.warning(f"[Product {product_id}]   Firecrawl search failed for '{q}': {e}")
                append_log(product_id, {
                    "timestamp": datetime.now().isoformat(),
                    "phase": "search", "step": "firecrawl_search", "status": "error",
                    "details": f"Query '{q}' failed: {str(e)}"
                })

    else:
        return {"error": f"Unknown SEARCH_PROVIDER: {search_provider}"}

    # Deduplicate
    seen_urls = set()
    unique_results = []
    for r in all_results:
        # Firecrawl results might be missing 'url' if error, so safe get
        u = r.get('url')
        if u and u not in seen_urls:
            seen_urls.add(u)
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

    mfr_hint = f"\nKnown manufacturer domain: {manufacturer_domain}" if manufacturer_domain else ""
    user_prompt = f"""Product: {brand} {model} (EAN: {ean})
Product type: {product_type}{mfr_hint}

Search results to classify:
"""
    for r in unique_results[:10]:
        user_prompt += f"- {r['url']} | {r['title']}\n"

    try:
        classified_list, usage = classify_with_schema(
            prompt=user_prompt,
            system=system_prompt,
            schema=SearchResultList,
            model="haiku",
            return_usage=True
        )

        # Track Claude cost (with cache metrics)
        if cost_tracker:
            cost_tracker.add_llm_call(
                usage["model"], usage["input_tokens"], usage["output_tokens"],
                phase="search",
                cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0),
                cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
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
            "credits_used": {"claude_in": usage["input_tokens"], "claude_out": usage["output_tokens"]}
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
