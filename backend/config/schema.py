"""
Company configuration schema — defines all per-instance settings.
Every field has a default value matching current Shoppster behavior.
"""

from pydantic import BaseModel, Field
from typing import Optional


class SourceConfig(BaseModel):
    """Controls how the pipeline finds and trusts data sources."""
    strategy: str = "official_first"
    # "official_first" = search manufacturer domain first, fall back to general (current Shoppster behavior)
    # "any_source" = search broadly, no manufacturer domain preference (Merkur)
    # "official_only" = only use manufacturer + authorized distributor, never third-party

    search_provider: str = "tavily"  # "tavily" or "firecrawl"
    max_manufacturer_results: int = 5
    max_general_results: int = 7
    max_general_queries: int = 3
    max_total_results: int = 6  # stop searching after this many total results

    allowed_domains: list[str] = Field(default_factory=list)
    # If non-empty, ONLY search these domains (whitelist mode)

    blocked_domains: list[str] = Field(default_factory=list)
    # Never include results from these domains

    trust_third_party_as_primary: bool = False
    # If True, third-party sources are treated with same weight as authorized (Merkur mode)


class PipelineConfig(BaseModel):
    """Controls pipeline flow and behavior."""
    enable_ean_lookup: bool = True
    enable_gap_fill: bool = True
    enable_gemini_vision: bool = True  # color detection via Gemini

    max_pages_to_scrape: int = 5   # max pages for LLM extraction (manufacturer + authorized)
    max_gap_fill_pages: int = 3    # max third-party pages to cache/check for gap fill

    gap_fill_is_primary: bool = False
    # If True, gap fill runs with higher priority (Merkur: data often only on third-party sites)


class CriticalFieldsConfig(BaseModel):
    """Defines which fields are considered critical (trigger gap-fill if missing)."""
    net_weight: bool = True
    packaged_weight: bool = True
    packaged_dims: bool = True
    warranty: bool = True
    short_description: bool = True
    color: bool = False
    country_of_origin: bool = False


class LanguageConfig(BaseModel):
    """Language and localization settings."""
    primary_languages: list[str] = Field(default_factory=lambda: ["sl", "en"])
    # Languages to look for in product data. First is primary.

    output_language: str = "en"
    # Language for generated descriptions and features

    # Additional color name mappings (merged with platform defaults)
    extra_color_mappings: dict[str, str] = Field(default_factory=dict)
    # e.g., {"türkis": "turquoise"} for German-speaking markets

    # Additional country name mappings (merged with platform defaults)
    extra_country_mappings: dict[str, str] = Field(default_factory=dict)


class CostConfig(BaseModel):
    """Cost guardrails and limits."""
    max_daily_cost_usd: float = 50.0
    daily_product_limit: int = 200
    max_batch_size: int = 50
    market_region: str = ""
    # Appended to search queries for regional relevance (e.g., "Slovenia")


class LLMConfig(BaseModel):
    """LLM model selection per pipeline phase."""
    triage_model: str = "haiku"       # "haiku" or "sonnet"
    search_model: str = "haiku"
    extract_model: str = "haiku"
    gap_fill_model: str = "haiku"
    validate_model: str = "haiku"


class BrandConfig(BaseModel):
    """Brand-related settings."""
    known_brands: list[str] = Field(default_factory=lambda: [
        "Texas", "Makita", "Bosch", "DeWalt", "Valvoline", "Husqvarna",
        "Stihl", "Kärcher", "Metabo", "Milwaukee", "Ryobi", "Black+Decker",
        "Einhell", "Gardena", "Fiskars"
    ])
    # Used in triage system prompt to help Claude identify brands

    # Pre-seeded brand → country of origin mappings
    brand_coo_seeds: dict[str, str] = Field(default_factory=dict)
    # e.g., {"Makita": "Japan", "Bosch": "Germany"}


class ExportConfig(BaseModel):
    """Export format and field selection."""
    include_cost_data: bool = False
    include_enrichment_log: bool = False
    # Custom column names (maps internal name → export column header)
    column_overrides: dict[str, str] = Field(default_factory=dict)
    # e.g., {"short_description": "Kratek opis"} for Slovenian headers


class CompanyConfig(BaseModel):
    """Root config — one per instance. Loaded from config/company.json."""
    company_name: str = "Default"
    company_slug: str = "default"  # used for logging, file paths

    source: SourceConfig = SourceConfig()
    pipeline: PipelineConfig = PipelineConfig()
    critical_fields: CriticalFieldsConfig = CriticalFieldsConfig()
    language: LanguageConfig = LanguageConfig()
    cost: CostConfig = CostConfig()
    llm: LLMConfig = LLMConfig()
    brands: BrandConfig = BrandConfig()
    export: ExportConfig = ExportConfig()
