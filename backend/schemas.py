from pydantic import BaseModel
from typing import Literal, Optional, List, Dict, Any

# --- Phase 1: Classification & Enriched Schemas ---

class EnrichedField(BaseModel):
    """Every enriched data point carries this metadata."""
    value: str | float | int | None = None
    unit: str | None = None  # "cm", "kg", "L", "mm", etc.
    source_url: str | None = None
    confidence: Literal["official", "third_party", "inferred", "not_found"] = "not_found"
    dimension_type: Literal["product", "packaging", "na"] = "na"
    notes: str | None = None

class ProductClassification(BaseModel):
    product_type: Literal["standard_product", "accessory", "liquid", "soft_good", "electronics", "other"]
    brand: str | None = None
    brand_confidence: Literal["certain", "likely", "unknown"] = "unknown"
    model_number: str | None = None
    parsed_color: str | None = None
    parsed_size: str | None = None
    reasoning: str

# Product Specific Schemas

class StandardProduct(BaseModel):
    height: EnrichedField = EnrichedField()
    length: EnrichedField = EnrichedField()
    width: EnrichedField = EnrichedField()
    weight: EnrichedField = EnrichedField()
    color: EnrichedField = EnrichedField()
    country_of_origin: EnrichedField = EnrichedField()
    image_url: EnrichedField = EnrichedField()
    image_urls: List[str] = []  # All discovered product images

class AccessoryProduct(BaseModel):
    diameter: EnrichedField = EnrichedField()
    arbor_size: EnrichedField | None = None
    thickness: EnrichedField | None = None
    weight: EnrichedField = EnrichedField()
    color: EnrichedField = EnrichedField()
    country_of_origin: EnrichedField = EnrichedField()
    image_url: EnrichedField = EnrichedField()
    image_urls: List[str] = []  # All discovered product images

class LiquidProduct(BaseModel):
    volume: EnrichedField = EnrichedField()
    container_height: EnrichedField | None = None
    container_width: EnrichedField | None = None
    container_depth: EnrichedField | None = None
    weight: EnrichedField = EnrichedField()
    color: EnrichedField = EnrichedField()
    country_of_origin: EnrichedField = EnrichedField()
    image_url: EnrichedField = EnrichedField()
    image_urls: List[str] = []  # All discovered product images

# Search Phase Schemas

class SearchResultURL(BaseModel):
    url: str
    title: str
    source_type: Literal["manufacturer", "authorized_distributor", "third_party", "irrelevant"]
    reasoning: str

class SearchResultList(BaseModel):
    results: List[SearchResultURL]

# Validation Phase Schemas

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

# EAN Lookup

class BarcodeLookupResult(BaseModel):
    brand: Optional[str] = None
    product_name: Optional[str] = None
    category: Optional[str] = None

# API Response Models

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
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True
