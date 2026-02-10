# PRODUCT DATA ENRICHMENT ENGINE
## Project Document

**Client:** Shoppster d.o.o.
**Provider:** WebFast (Nathan)
**Version:** 1.0 // February 2026
**Status:** Validation Phase

---

## 1. Executive Summary

Shoppster operates a large product catalog where critical product attributes are systematically missing or incomplete. Fields such as physical dimensions (height, length, width), weight, color, country of origin, customs tariff codes, and product images are either empty or inconsistently filled across the catalog. This data gap creates downstream problems: poor product listings, inaccurate shipping cost calculations, compliance issues with customs declarations, and degraded customer experience.

WebFast proposes to build and operate a **Product Data Enrichment Engine** that takes Shoppster's incomplete product data as input and returns enriched, validated, source-attributed data as output. The system combines AI-powered web research with structured data extraction to fill missing product attributes from authoritative sources — manufacturer websites, authorized distributors, and official product documentation.

**This is not a one-time manual data entry project.** WebFast is building a repeatable, semi-automated pipeline that can process products at scale. The enrichment logic, source prioritization rules, validation criteria, and product-type-specific schemas are designed to work reliably across different product categories — from power tools to automotive chemicals to accessories.

---

## 2. Problem Statement

### 2.1 Current State of Product Data

Based on the sample dataset of 14 products provided by Shoppster, the following data completeness issues were identified:

| Field | Completeness |
|-------|-------------|
| Color | 0% filled |
| Size | 0% filled |
| Dimensions (H/L/W) | 0% filled |
| Country of Origin | 0% filled |
| Customs Tariff Number | 0% filled |
| Weight | 86% filled (but inconsistent units) |
| Brand | Mostly present but not always explicit |
| Images | Not evaluated in sample |

The sample includes products across categories: garden power tools (Texas HTZ5800), power tool accessories (Makita wire brushes), automotive chemicals (Valvoline motor oil), and others. Each category presents different enrichment challenges.

### 2.2 Why This Is Hard

Product data enrichment is not a simple lookup problem. Through manual testing of three products from the sample, WebFast identified five structural challenges that make naive approaches (searching Google and copying data) unreliable:

**Brand identification is a prerequisite, not a given.** Some products in the dataset lack explicit brand attribution. The Makita D-39914 wire brush had no brand in the provided data. Without knowing the brand, you cannot construct an effective search query to find manufacturer specifications.

**Product type determines which attributes are relevant.** Standard H/L/W dimensions apply to a hedge trimmer, but a wire brush is defined by diameter and arbor size. Motor oil is defined by volume, not physical dimensions. A one-size-fits-all attribute schema wastes effort searching for data that does not exist for that product type.

**Product vs. packaging dimensions are routinely confused.** The Texas HTZ5800 hedge trimmer's manufacturer page lists dimensions of 118×22×24 cm — these are the shipping box dimensions, not the product itself. Distributors and marketplaces frequently conflate the two. Without explicit flagging, enriched data may be dimensionally wrong.

**Data sources vary dramatically by product.** Major brand products (Texas, Valvoline) have manufacturer websites with complete specification tables. Accessories (Makita wire brush) may not appear on the manufacturer site at all, requiring fallback to authorized distributors. Country of origin is almost never listed on product pages and requires specialized search strategies.

**Confidence varies and must be tracked.** Data from a manufacturer's official product page is highly reliable. Data from a single third-party distributor is less so. Color inferred from a product image is speculative. Without confidence attribution, the enriched data appears uniformly reliable when it is not.

---

## 3. Solution Overview

The Product Data Enrichment Engine is a structured, multi-phase pipeline that processes each product through a sequence of research, extraction, validation, and output steps. It is designed to handle the challenges identified above through the following principles:

**Classify before you search.** Every product is classified by type before enrichment begins, determining which attribute schema applies and which search strategies to use.

**Prioritize authoritative sources.** Manufacturer official websites first, authorized distributors second, third-party sources only as fallback. Source URL is recorded with every data point.

**Score confidence explicitly.** Every enriched attribute carries a confidence tier: Official (manufacturer source), Third-party (distributor/retailer), or Inferred (visual analysis, pattern matching). Shoppster can then decide their own threshold for acceptance.

**Flag rather than guess.** When data is ambiguous, contradictory, or unavailable, the system flags the product for human review rather than recording speculative data as fact.

**Normalize for consistency.** All dimensions converted to centimeters, all weights to kilograms, all volumes to liters. Unit standardization applied after extraction, before output.

### 3.1 The Five-Phase Pipeline

**Phase 1: Triage.** Parse existing product data to extract any embedded information (color from product name, model numbers, size indicators). Classify the product type to determine the relevant attribute schema. Identify or look up the brand if not provided.

**Phase 2: Search.** Construct targeted search queries using brand + model/part number + EAN. Find manufacturer product pages, specification tables, product documentation, and authorized distributor listings.

**Phase 3: Extract.** Scrape identified pages and extract structured product data. Use AI to parse specification tables, distinguish product from packaging dimensions, and pull relevant attributes into the product-type-specific schema.

**Phase 4: Validate.** Normalize units. Apply survivorship rules when multiple sources provide conflicting data. Cross-reference extracted data against the original product record for sanity. Assign confidence scores. Flag exceptions.

**Phase 5: Output.** Generate enriched dataset with original data + new attributes + metadata (source URLs, confidence tiers, dimension type flags, review flags). Format for Shoppster's SAP import requirements.

### 3.2 Non-Linear Processing

The pipeline is not strictly sequential. Easy products (major brand, manufacturer page exists, all data available) may complete in Phases 1–3 and skip directly to output. Hard products (missing brand, no manufacturer page, accessory without standard specs) will branch into fallback search strategies, loop through multiple distributor sources, or ultimately flag for manual review. The system is designed to handle this branching gracefully rather than forcing every product through every step.

---

## 4. Scope and Deliverables

### 4.1 In Scope

Enrichment of the following product attributes where data can be found from reliable sources:

| Attribute | Details |
|-----------|---------|
| Dimensions | Height, Length, Width (or product-type equivalent: diameter, volume, etc.) with explicit product vs. packaging flag |
| Weight | Net product weight in kg, standardized from any source unit |
| Color | Primary product color, determined from specs, product name, or image analysis |
| Country of Origin | Manufacturing country (lower confidence, flagged accordingly) |
| Customs Tariff Number | HS/CN code where identifiable from product documentation |
| Product Images | URLs to highest-quality available images (manufacturer preferred) |
| Brand | Confirmed brand attribution where missing or ambiguous |

### 4.2 Out of Scope

Data cleaning or deduplication within Shoppster's existing catalog. Product description writing or SEO optimization. Translation of product data between languages. SAP integration or import automation (Shoppster handles their own import). Ongoing real-time data monitoring or price tracking.

### 4.3 Deliverables

**Enriched dataset:** CSV/XLSX file matching Shoppster's input format plus additional columns for each enriched attribute, source URL, confidence tier, dimension type flag, and review flag.

**Enrichment report:** Summary of products processed, fill rates achieved per attribute, confidence distribution, and list of products flagged for manual review with reasons.

**Methodology documentation:** Description of the enrichment workflow, source prioritization rules, confidence scoring criteria, and product-type schemas used.

---

## 5. Methodology

### 5.1 Research Validation

Before building any automation, WebFast manually tested the enrichment workflow on three products from the sample dataset, representing different difficulty levels:

| Product | Difficulty & Key Finding |
|---------|------------------------|
| Texas HTZ5800 (hedge trimmer) | Easy. All data on manufacturer site. CRITICAL: Listed dimensions were packaging (box), not product. Color inferred from images. |
| Makita D-39914 (wire brush) | Hard. Brand missing from data. Accessory not on manufacturer site. Standard H/L/W does not apply — defined by diameter (50mm). Data scattered across distributors. |
| Valvoline ProFleet 10W40 20L | Medium. Size is volume (20L), not physical dimensions. Packaging dimensions on distributor site, not manufacturer. Country of origin required specialized search. |

This manual testing identified the five structural challenges described in Section 2.2 and directly shaped the design of the automated pipeline.

### 5.2 Industry Validation

The enrichment workflow was validated against enterprise best practices from Amazon, Walmart, Salsify, Akeneo, and GS1. Key alignments: multi-source collection with source prioritization matches Amazon's enrichment approach; confidence scoring with source attribution matches enterprise PIM standards; category-specific attribute schemas match Google Shopping and Amazon marketplace requirements; product vs. packaging dimension distinction is an industry-standard requirement for ecommerce platforms.

Four specific enhancements were incorporated from this research: formalized category-specific attribute schemas, unit normalization as a dedicated pipeline step, explicit survivorship rules for conflicting data, and mandatory product vs. packaging dimension flagging.

---

## 6. Project Phases

| Phase | Description |
|-------|-------------|
| Validation (Current) | Automated enrichment of 3 test products to prove reliability. Compare automated results against manual research. Refine prompts and schemas. Deliverable: validation report with accuracy metrics. |
| Proposal & Agreement | Present validation results to Shoppster. Agree on scope (which products, which attributes, pricing). Deliverable: signed agreement. |
| Build | Construct the production pipeline using LangGraph, Firecrawl, Tavily, and Claude/Gemini APIs. Implement product-type schemas, survivorship rules, normalization layer. Deliverable: working pipeline. |
| Initial Batch | Process first batch of products (100–500 from Shoppster's catalog). Manual QA on a sample of results. Refine based on edge cases found. Deliverable: enriched dataset + report. |
| Ongoing Processing | Process remaining catalog in batches. Continuous improvement of prompts, schemas, and source strategies based on results. Deliverable: enriched datasets per batch. |

---

## 7. Competitive Defensibility

The value WebFast delivers is not in any single tool or API. The tools (Firecrawl, Tavily, Claude, Gemini) are available to anyone. The defensibility lies in the orchestration layer:

**Product-type classification logic** that determines which attributes to search for and which to skip, preventing wasted effort and wrong-schema results.

**Source prioritization and survivorship rules** that codify which data source wins when sources conflict, ensuring consistent quality across thousands of products.

**Prompt engineering** refined through iterative testing against real Slovenian product data, handling edge cases like Slovenian product names, EU-market brands, and mixed-language specifications.

**Edge case handling** for accessories, liquids, soft goods, and other non-standard product types that break naive enrichment approaches.

**Confidence scoring methodology** that gives Shoppster actionable quality indicators rather than opaque data of unknown reliability.

This combination of domain knowledge, refined automation logic, and tested methodology is what makes the service valuable and not trivially replaceable by a junior employee with ChatGPT.
