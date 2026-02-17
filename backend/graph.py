"""
LangGraph Enrichment Pipeline — v2

State machine: triage → [ean_lookup?] → search → extract → validate → save_costs

Each node reads/writes to the SQLite DB directly and updates `current_step`
for real-time UI feedback. State carries flow-control data + cost tracker.
"""

import json
import logging
from typing import TypedDict, Optional, Literal, Any
from langgraph.graph import StateGraph, START, END
from db import get_db_connection, update_step, append_log, save_cost_data
from datetime import datetime
from utils.cost_tracker import CostTracker

logger = logging.getLogger("pipeline.graph")


# --- State ---

class ProductState(TypedDict):
    product_id: int
    has_brand: bool
    has_search_results: bool
    error: Optional[str]
    cost_tracker: Any  # CostTracker instance, passed through all nodes


# --- Node Imports (lazy to avoid circular imports at module level) ---

async def _triage(state: ProductState) -> dict:
    from pipeline.triage import triage_node
    return await triage_node(state)


async def _ean_lookup(state: ProductState) -> dict:
    """
    Node: EAN Lookup (conditional — only runs if brand is unknown after triage).
    Scrapes barcodelookup.com to identify product brand.
    """
    product_id = state["product_id"]
    cost_tracker: CostTracker = state.get("cost_tracker")

    logger.info(f"[Product {product_id}]   EAN Lookup — brand not found, trying barcode DB...")
    update_step(product_id, "classifying", "Looking up EAN for brand identification...")

    from utils.ean_lookup import lookup_ean

    # Load EAN
    conn = get_db_connection()
    product = conn.execute("SELECT ean, classification_result FROM products WHERE id = ?", (product_id,)).fetchone()
    conn.close()

    if not product:
        return {"error": f"Product {product_id} not found"}

    ean = product['ean']
    result = await lookup_ean(ean)

    # Track costs: 1 Firecrawl scrape + 1 Claude Haiku call
    if cost_tracker:
        cost_tracker.add_api_call("firecrawl", credits=1, phase="ean_lookup")
        cost_tracker.add_llm_call("claude_haiku", input_tokens=500, output_tokens=200, phase="ean_lookup")

    if result and result.get('brand'):
        # Update classification with discovered brand
        cls_data = json.loads(product['classification_result']) if product['classification_result'] else {}
        cls_data['brand'] = result['brand']
        cls_data['brand_confidence'] = 'likely'

        conn = get_db_connection()
        conn.execute(
            "UPDATE products SET classification_result = ?, current_step = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(cls_data), f"Brand identified: {result['brand']}", product_id)
        )
        conn.commit()
        conn.close()

        append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "triage", "step": "ean_lookup", "status": "success",
            "details": f"Brand from EAN lookup: {result['brand']}",
            "credits_used": {"firecrawl": 1, "claude_tokens": 300}
        })

        return {"has_brand": True}
    else:
        append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "triage", "step": "ean_lookup", "status": "warning",
            "details": "Could not identify brand from EAN lookup"
        })
        # Continue anyway — search will try with product name
        return {"has_brand": False}


async def _search(state: ProductState) -> dict:
    if state.get("error"):
        return state
    from pipeline.search import search_node
    return await search_node(state)


async def _extract(state: ProductState) -> dict:
    if state.get("error"):
        return state
    from pipeline.extract import extract_node
    return await extract_node(state)


async def _validate(state: ProductState) -> dict:
    if state.get("error"):
        return state
    from pipeline.validate import validate_node
    return await validate_node(state)


async def _save_costs(state: ProductState) -> dict:
    """Final node: persist cost tracking data to DB."""
    product_id = state["product_id"]
    cost_tracker: CostTracker = state.get("cost_tracker")

    if cost_tracker:
        summary = cost_tracker.get_summary()
        save_cost_data(product_id, summary)
        logger.info(
            f"[Product {product_id}] 💰 Total cost: ${cost_tracker.total_cost:.4f} "
            f"({len(cost_tracker.llm_calls)} LLM calls, {len(cost_tracker.api_calls)} API calls)"
        )
        append_log(product_id, {
            "timestamp": datetime.now().isoformat(),
            "phase": "pipeline", "step": "cost_summary", "status": "success",
            "details": f"Total cost: ${cost_tracker.total_cost:.4f} | "
                       f"Tokens: {cost_tracker.total_input_tokens}→{cost_tracker.total_output_tokens} | "
                       f"API credits: {cost_tracker.total_api_credits}",
            "cost_usd": round(cost_tracker.total_cost, 4)
        })
    return {}


# --- Routing ---

def route_after_triage(state: ProductState) -> Literal["ean_lookup", "search"]:
    """If brand wasn't identified in triage, try EAN lookup first."""
    if state.get("error"):
        return "search"  # Skip lookup, let search handle with what we have
    if not state.get("has_brand", False):
        return "ean_lookup"
    return "search"


# --- Build Graph ---

def build_pipeline() -> StateGraph:
    """Constructs and compiles the enrichment pipeline graph."""
    builder = StateGraph(ProductState)

    builder.add_node("triage", _triage)
    builder.add_node("ean_lookup", _ean_lookup)
    builder.add_node("search", _search)
    builder.add_node("extract", _extract)
    builder.add_node("validate", _validate)
    builder.add_node("save_costs", _save_costs)

    builder.add_edge(START, "triage")
    builder.add_conditional_edges("triage", route_after_triage)
    builder.add_edge("ean_lookup", "search")
    builder.add_edge("search", "extract")
    builder.add_edge("extract", "validate")
    builder.add_edge("validate", "save_costs")
    builder.add_edge("save_costs", END)

    return builder.compile()


# Compiled graph singleton
enrichment_pipeline = build_pipeline()
