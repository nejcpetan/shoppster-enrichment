"""
Cost Tracker & Guardrails — Per-Product API Cost Accounting

Tracks token usage and API calls across the entire enrichment pipeline.
Calculates costs using current service pricing.
Enforces configurable daily/batch limits (via env vars or runtime config).

Usage:
    tracker = CostTracker(product_id)
    tracker.add_llm_call("claude_haiku", input_tokens=500, output_tokens=300)
    tracker.add_api_call("firecrawl", credits=1)
    summary = tracker.get_summary()
"""

import os
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Dict, List, Optional

logger = logging.getLogger("pipeline.cost_tracker")


# ─── Pricing Constants ────────────────────────────────────────────────────────
# Update these when prices change. All prices in USD.

PRICING = {
    "claude_haiku": {
        "input_per_million": 1.00,   # $1.00/M input tokens
        "output_per_million": 5.00,  # $5.00/M output tokens
    },
    "claude_sonnet": {
        "input_per_million": 3.00,   # $3.00/M input tokens
        "output_per_million": 15.00, # $15.00/M output tokens
    },
    "gemini_flash": {
        "input_per_million": 0.50,   # $0.50/M input tokens
        "output_per_million": 3.00,  # $3.00/M output tokens
    },
    "firecrawl": {
        "cost_per_credit": 0.00083,  # Standard plan: ~$0.83/1000 credits
    },
    "tavily": {
        "cost_per_credit": 0.008,    # Pay-as-you-go: $0.008/credit
    },
}


# ─── Configurable Guardrail Limits ────────────────────────────────────────────
# These are module-level so they can be updated at runtime via API.
# Defaults come from env vars, falling back to sensible values.

_limits = {
    "daily_product_limit": int(os.getenv("DAILY_PRODUCT_LIMIT", "200")),
    "max_batch_size": int(os.getenv("MAX_BATCH_SIZE", "50")),
    "max_daily_cost_usd": float(os.getenv("MAX_DAILY_COST_USD", "50.0")),
}


def get_limits() -> dict:
    """Return current guardrail limits."""
    return dict(_limits)


def set_limits(new_limits: dict) -> dict:
    """
    Update guardrail limits at runtime.
    Only updates keys that are present in new_limits and valid.
    Returns the updated limits.
    """
    for key in ("daily_product_limit", "max_batch_size", "max_daily_cost_usd"):
        if key in new_limits and new_limits[key] is not None:
            val = new_limits[key]
            if key == "max_daily_cost_usd":
                _limits[key] = max(0.0, float(val))
            else:
                _limits[key] = max(1, int(val))
    logger.info(f"Guardrail limits updated: {_limits}")
    return dict(_limits)


# ─── Daily Stats & Guardrail Checks ──────────────────────────────────────────

def get_daily_stats() -> dict:
    """
    Get today's processing stats from the database.
    Returns counts, cost totals, and limit info.
    """
    from db import get_db_connection

    conn = get_db_connection()
    today = date.today().isoformat()

    # Products processed today
    processed_today = conn.execute(
        "SELECT COUNT(*) as c FROM products WHERE date(updated_at) = ? AND status IN ('done', 'needs_review', 'error')",
        (today,)
    ).fetchone()['c']

    # Currently processing
    currently_processing = conn.execute(
        "SELECT COUNT(*) as c FROM products WHERE status IN ('enriching', 'classifying', 'searching', 'extracting', 'validating')"
    ).fetchone()['c']

    # Total products
    total_products = conn.execute("SELECT COUNT(*) as c FROM products").fetchone()['c']

    # Aggregate cost data from today's completed products
    cost_rows = conn.execute(
        "SELECT cost_data FROM products WHERE date(updated_at) = ? AND cost_data IS NOT NULL",
        (today,)
    ).fetchall()

    total_cost_today = 0.0
    total_input_tokens_today = 0
    total_output_tokens_today = 0
    service_costs_today: Dict[str, float] = {}

    for row in cost_rows:
        try:
            cd = json.loads(row['cost_data'])
            total_cost_today += cd.get('total_cost_usd', 0)
            total_input_tokens_today += cd.get('total_input_tokens', 0)
            total_output_tokens_today += cd.get('total_output_tokens', 0)
            for svc, cost in cd.get('cost_by_service', {}).items():
                service_costs_today[svc] = service_costs_today.get(svc, 0) + cost
        except (json.JSONDecodeError, TypeError):
            pass

    # All-time aggregate cost data
    all_cost_rows = conn.execute(
        "SELECT cost_data FROM products WHERE cost_data IS NOT NULL"
    ).fetchall()

    total_cost_all_time = 0.0
    total_products_with_cost = len(all_cost_rows)
    cost_per_product_list = []

    for row in all_cost_rows:
        try:
            cd = json.loads(row['cost_data'])
            cost = cd.get('total_cost_usd', 0)
            total_cost_all_time += cost
            cost_per_product_list.append(cost)
        except (json.JSONDecodeError, TypeError):
            pass

    avg_cost_per_product = (total_cost_all_time / total_products_with_cost) if total_products_with_cost > 0 else 0

    conn.close()

    limits = get_limits()

    return {
        # Today's stats
        "processed_today": processed_today,
        "currently_processing": currently_processing,
        "total_cost_today_usd": round(total_cost_today, 4),
        "total_input_tokens_today": total_input_tokens_today,
        "total_output_tokens_today": total_output_tokens_today,
        "service_costs_today": {k: round(v, 4) for k, v in service_costs_today.items()},

        # All-time stats
        "total_products": total_products,
        "total_products_with_cost": total_products_with_cost,
        "total_cost_all_time_usd": round(total_cost_all_time, 4),
        "avg_cost_per_product_usd": round(avg_cost_per_product, 4),

        # Limits
        "daily_product_limit": limits["daily_product_limit"],
        "max_batch_size": limits["max_batch_size"],
        "max_daily_cost_usd": limits["max_daily_cost_usd"],
        "remaining_products": max(0, limits["daily_product_limit"] - processed_today),
        "remaining_budget_usd": round(max(0, limits["max_daily_cost_usd"] - total_cost_today), 4),
    }


def check_can_process(requested_count: int) -> tuple:
    """
    Check if we can process the requested number of products.
    Returns (allowed: bool, reason: str).
    """
    limits = get_limits()
    stats = get_daily_stats()

    if requested_count > limits["max_batch_size"]:
        return False, (
            f"Batch size {requested_count} exceeds maximum of {limits['max_batch_size']}. "
            f"Process in smaller batches."
        )

    if stats["remaining_products"] < requested_count:
        return False, (
            f"Daily limit would be exceeded. "
            f"Processed today: {stats['processed_today']}/{limits['daily_product_limit']}. "
            f"Requested: {requested_count}. Remaining: {stats['remaining_products']}."
        )

    if stats["remaining_budget_usd"] <= 0:
        return False, (
            f"Daily cost budget exhausted. "
            f"Spent today: ${stats['total_cost_today_usd']:.2f} / ${limits['max_daily_cost_usd']:.2f}."
        )

    return True, "OK"


# ─── CostTracker Class ───────────────────────────────────────────────────────


@dataclass
class LLMCall:
    """Record of a single LLM API call."""
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    phase: str
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "phase": self.phase,
            "timestamp": self.timestamp,
        }


@dataclass
class APICall:
    """Record of a non-LLM API call (Firecrawl, Tavily)."""
    service: str
    credits: int
    cost_usd: float
    phase: str
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "service": self.service,
            "credits": self.credits,
            "cost_usd": round(self.cost_usd, 6),
            "phase": self.phase,
            "timestamp": self.timestamp,
        }


class CostTracker:
    """
    Accumulates all API costs for a single product's enrichment run.
    Thread-safe for sequential pipeline execution (not concurrent).
    """

    def __init__(self, product_id: int):
        self.product_id = product_id
        self.llm_calls: List[LLMCall] = []
        self.api_calls: List[APICall] = []
        self.started_at = datetime.now().isoformat()

    def add_llm_call(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        phase: str = "unknown"
    ) -> float:
        """
        Record an LLM call and return its cost in USD.
        model: "claude_haiku", "claude_sonnet", "gemini_flash"
        """
        pricing = PRICING.get(model, PRICING["claude_haiku"])
        cost = (
            (input_tokens / 1_000_000) * pricing["input_per_million"]
            + (output_tokens / 1_000_000) * pricing["output_per_million"]
        )

        call = LLMCall(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            phase=phase,
            timestamp=datetime.now().isoformat(),
        )
        self.llm_calls.append(call)

        logger.debug(
            f"[Product {self.product_id}] 💰 {model} | "
            f"{input_tokens}→{output_tokens} tokens | ${cost:.5f} ({phase})"
        )
        return cost

    def add_api_call(
        self,
        service: str,
        credits: int = 1,
        phase: str = "unknown"
    ) -> float:
        """
        Record a non-LLM API call (Firecrawl scrape, Tavily search).
        Returns cost in USD.
        """
        pricing = PRICING.get(service, {})
        cost_per = pricing.get("cost_per_credit", 0.0)
        cost = credits * cost_per

        call = APICall(
            service=service,
            credits=credits,
            cost_usd=cost,
            phase=phase,
            timestamp=datetime.now().isoformat(),
        )
        self.api_calls.append(call)

        logger.debug(
            f"[Product {self.product_id}] 💰 {service} | "
            f"{credits} credit(s) | ${cost:.5f} ({phase})"
        )
        return cost

    @property
    def total_cost(self) -> float:
        return sum(c.cost_usd for c in self.llm_calls) + sum(c.cost_usd for c in self.api_calls)

    @property
    def total_input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.llm_calls)

    @property
    def total_output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.llm_calls)

    @property
    def total_api_credits(self) -> Dict[str, int]:
        credits: Dict[str, int] = {}
        for c in self.api_calls:
            credits[c.service] = credits.get(c.service, 0) + c.credits
        return credits

    def get_cost_by_phase(self) -> Dict[str, float]:
        by_phase: Dict[str, float] = {}
        for c in self.llm_calls:
            by_phase[c.phase] = by_phase.get(c.phase, 0) + c.cost_usd
        for c in self.api_calls:
            by_phase[c.phase] = by_phase.get(c.phase, 0) + c.cost_usd
        return {k: round(v, 6) for k, v in by_phase.items()}

    def get_cost_by_service(self) -> Dict[str, float]:
        by_svc: Dict[str, float] = {}
        for c in self.llm_calls:
            by_svc[c.model] = by_svc.get(c.model, 0) + c.cost_usd
        for c in self.api_calls:
            by_svc[c.service] = by_svc.get(c.service, 0) + c.cost_usd
        return {k: round(v, 6) for k, v in by_svc.items()}

    def get_summary(self) -> dict:
        """Full cost summary for storage in the database."""
        return {
            "product_id": self.product_id,
            "total_cost_usd": round(self.total_cost, 6),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_api_credits": self.total_api_credits,
            "cost_by_phase": self.get_cost_by_phase(),
            "cost_by_service": self.get_cost_by_service(),
            "llm_calls_count": len(self.llm_calls),
            "api_calls_count": len(self.api_calls),
            "started_at": self.started_at,
            "completed_at": datetime.now().isoformat(),
            "llm_calls": [c.to_dict() for c in self.llm_calls],
            "api_calls": [c.to_dict() for c in self.api_calls],
        }

    def to_json(self) -> str:
        return json.dumps(self.get_summary(), indent=2)

    def __repr__(self) -> str:
        return (
            f"CostTracker(product={self.product_id}, "
            f"total=${self.total_cost:.4f}, "
            f"llm_calls={len(self.llm_calls)}, "
            f"api_calls={len(self.api_calls)})"
        )
