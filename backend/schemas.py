"""
Product Enrichment Schemas — v2

Unified schema architecture with clearly separated output segments:
- Dimensions: net (product) vs packaged (logistics)
- Descriptions: short, marketing, features
- Technical Data: key-value specification pairs
- Warranty: separate segment
- Documents: PDF links (manuals, datasheets, certificates)
"""

from pydantic import BaseModel, Field
from typing import Literal, Optional, List, Dict, Any


# ─── Core Building Block ──────────────────────────────────────────────────────

class EnrichedField(BaseModel):
    """Every enriched data point carries provenance metadata."""
    value: str | float | int | None = None
    unit: str | None = None  # "cm", "kg", "L", "mm", etc.
    source_url: str | None = None
    confidence: Literal["official", "authorized", "third_party", "inferred", "not_found"] = "not_found"
    notes: str | None = None


# ─── Phase 1: Classification ──────────────────────────────────────────────────

class ProductClassification(BaseModel):
    product_type: Literal["standard_product", "accessory", "liquid", "soft_good", "electronics", "other"]
    brand: str | None = None
    brand_confidence: Literal["certain", "likely", "unknown"] = "unknown"
    model_number: str | None = None
    parsed_color: str | None = None
    parsed_size: str | None = None
    manufacturer_domain: str | None = None  # e.g., "makita.com", "bosch.com"
    reasoning: str


# ─── Dimensions: Net vs Packaged ──────────────────────────────────────────────

class DimensionSet(BaseModel):
    """A complete set of physical measurements (either net or packaged)."""
    height: EnrichedField = EnrichedField()
    length: EnrichedField = EnrichedField()
    width: EnrichedField = EnrichedField()
    depth: EnrichedField = EnrichedField()
    weight: EnrichedField = EnrichedField()
    diameter: EnrichedField = EnrichedField()   # accessories (brushes, blades)
    volume: EnrichedField = EnrichedField()     # liquids


class ProductDimensions(BaseModel):
    """Net (product itself) and packaged (logistics/shipping) as separate segments."""
    net: DimensionSet = DimensionSet()
    packaged: DimensionSet = DimensionSet()


# ─── Descriptions ─────────────────────────────────────────────────────────────

class ProductDescriptions(BaseModel):
    """All textual descriptions scraped from product pages."""
    short_description: EnrichedField = EnrichedField()        # 1-2 sentence summary
    marketing_description: EnrichedField = EnrichedField()    # longer marketing copy
    features: List[str] = Field(default_factory=list)         # bullet-point feature list


# ─── Technical Specifications ─────────────────────────────────────────────────

class TechnicalSpec(BaseModel):
    """A single key-value technical specification."""
    name: str                   # e.g., "Voltage", "RPM", "Motor Power"
    value: str                  # e.g., "230V", "3000", "1800W"
    unit: Optional[str] = None  # e.g., "V", "rpm", "W"
    source_url: Optional[str] = None
    confidence: Literal["official", "authorized", "third_party", "inferred", "not_found"] = "not_found"


class TechnicalData(BaseModel):
    """All technical specifications as a separate segment."""
    specs: List[TechnicalSpec] = Field(default_factory=list)


# ─── Warranty ─────────────────────────────────────────────────────────────────

class WarrantyInfo(BaseModel):
    """Warranty information as a separate segment."""
    duration: EnrichedField = EnrichedField()    # e.g., "2 years", "24 months"
    type: Optional[str] = None                   # "manufacturer", "retailer", "extended"
    conditions: Optional[str] = None             # key conditions/limitations
    source_url: Optional[str] = None
    confidence: Literal["official", "authorized", "third_party", "inferred", "not_found"] = "not_found"


# ─── Documents / PDFs ─────────────────────────────────────────────────────────

class ProductDocument(BaseModel):
    """A downloadable document found on a product page."""
    title: str
    url: str
    doc_type: Literal["manual", "datasheet", "certificate", "warranty", "safety", "brochure", "other"]
    language: Optional[str] = None  # "en", "sl", "de", etc.
    source_page: str = ""           # page where the link was found


class ProductDocuments(BaseModel):
    """All discovered PDF/document links."""
    documents: List[ProductDocument] = Field(default_factory=list)


# ─── Unified Enriched Product ─────────────────────────────────────────────────

class EnrichedProduct(BaseModel):
    """
    The unified enriched product output — all segments.
    Replaces the old per-type schemas (StandardProduct, AccessoryProduct, LiquidProduct).
    """
    # Physical data
    dimensions: ProductDimensions = ProductDimensions()

    # Descriptive content
    descriptions: ProductDescriptions = ProductDescriptions()

    # Technical specifications
    technical_data: TechnicalData = TechnicalData()

    # Warranty
    warranty: WarrantyInfo = WarrantyInfo()

    # Documents
    documents: ProductDocuments = ProductDocuments()

    # Other enriched fields
    color: EnrichedField = EnrichedField()
    country_of_origin: EnrichedField = EnrichedField()
    image_url: EnrichedField = EnrichedField()
    image_urls: List[str] = Field(default_factory=list)


# ─── LLM Extraction Sub-Schemas ──────────────────────────────────────────────
# These schemas are used for individual LLM extraction calls.
# They mirror parts of EnrichedProduct but are simpler for the LLM to fill.

class DimensionsExtraction(BaseModel):
    """Schema sent to LLM for dimension extraction from a single page."""
    net_height: EnrichedField = EnrichedField()
    net_length: EnrichedField = EnrichedField()
    net_width: EnrichedField = EnrichedField()
    net_depth: EnrichedField = EnrichedField()
    net_weight: EnrichedField = EnrichedField()
    net_diameter: EnrichedField = EnrichedField()
    net_volume: EnrichedField = EnrichedField()
    packaged_height: EnrichedField = EnrichedField()
    packaged_length: EnrichedField = EnrichedField()
    packaged_width: EnrichedField = EnrichedField()
    packaged_depth: EnrichedField = EnrichedField()
    packaged_weight: EnrichedField = EnrichedField()
    color: EnrichedField = EnrichedField()
    country_of_origin: EnrichedField = EnrichedField()
    image_url: EnrichedField = EnrichedField()
    image_urls: List[str] = Field(default_factory=list)


class ContentExtraction(BaseModel):
    """Schema sent to LLM for content/description extraction from a single page.

    Descriptions use start/end markers (~50 chars each) instead of full text.
    The full text is resolved deterministically from the cached scraped markdown,
    saving output tokens and avoiding truncation.
    """
    short_description_start: str = ""   # First ~50 chars of the short description
    short_description_end: str = ""     # Last ~50 chars of the short description
    marketing_description_start: str = ""  # First ~50 chars of the marketing description
    marketing_description_end: str = ""    # Last ~50 chars of the marketing description
    features: List[str] = Field(default_factory=list)
    technical_specs: List[TechnicalSpec] = Field(default_factory=list)
    warranty_duration: str = ""
    warranty_type: str = ""
    warranty_conditions: str = ""


class GapFillExtraction(BaseModel):
    """Targeted schema for gap-fill — only critical missing fields."""
    net_weight: EnrichedField = EnrichedField()
    packaged_weight: EnrichedField = EnrichedField()
    packaged_height: EnrichedField = EnrichedField()
    packaged_length: EnrichedField = EnrichedField()
    packaged_width: EnrichedField = EnrichedField()
    warranty_duration: str = ""
    warranty_type: str = ""
    warranty_conditions: str = ""
    short_description: str = ""


# ─── Search Phase Schemas ─────────────────────────────────────────────────────

class SearchResultURL(BaseModel):
    url: str
    title: str
    source_type: Literal["manufacturer", "authorized_distributor", "third_party", "irrelevant"]
    reasoning: str


class SearchResultList(BaseModel):
    results: List[SearchResultURL]


# ─── Validation Phase Schemas ─────────────────────────────────────────────────

class ValidationIssue(BaseModel):
    field: str
    issue: str
    severity: Literal["warning", "error"]


class ValidationReport(BaseModel):
    overall_quality: Literal["good", "acceptable", "needs_review"]
    issues: List[ValidationIssue]
    review_reason: Optional[str] = None


class ValidatedProductData(BaseModel):
    normalized_data: dict
    report: ValidationReport


# ─── EAN Lookup ───────────────────────────────────────────────────────────────

class BarcodeLookupResult(BaseModel):
    brand: Optional[str] = None
    product_name: Optional[str] = None
    category: Optional[str] = None


# ─── API Response Models ──────────────────────────────────────────────────────

class ProductBase(BaseModel):
    ean: str
    product_name: str
    brand: Optional[str] = None
    weight: Optional[str] = None
    original_data: Optional[str] = None


class ProductResponse(ProductBase):
    id: int
    status: str
    product_type: Optional[str] = None
    current_step: Optional[str] = None
    classification_result: Optional[str] = None
    search_result: Optional[str] = None
    extraction_result: Optional[str] = None
    validation_result: Optional[str] = None
    enrichment_log: Optional[str] = None
    cost_data: Optional[str] = None
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True
