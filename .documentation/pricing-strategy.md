# PRODUCT DATA ENRICHMENT ENGINE — PRICING STRATEGY
## Final Pricing Menu & Reasoning

**Client:** Shoppster d.o.o.
**Provider:** WebFast (Nathan)
**Date:** February 2026
**Context:** Project-based engagement (not SaaS). Shoppster is a general e-commerce marketplace operating in Slovenia and Serbia, processing 10,000+ new products monthly. This system replaces ~80 hours/month of manual product data entry.

---

## 1. Final Pricing Menu

### Core Package — €4,800

| Feature | Description |
|---------|-------------|
| Enrichment pipeline | 5-phase AI-powered pipeline: triage, search, extract, gap fill, validate. Handles standard products, accessories, liquids, electronics, soft goods. |
| REST API | Full CRUD endpoints, batch processing, real-time SSE progress monitoring. Complete API documentation included. |
| Web dashboard | Upload CSV/XLSX, product table with status tracking, stats cards, cost tracking display. |
| XLSX export | Export enriched data with all attributes, source URLs, confidence tiers, and review flags. |
| Concurrent processing | Parallelized pipeline — processes multiple products simultaneously. Handles 10,000+ products/month at production volume. |
| Reliability | Retry logic with exponential backoff, API call timeouts, stuck product recovery, input validation, cost guardrails (daily limits, batch limits). |
| Production configuration | Environment-based config, structured logging, API key authentication. |
| Deployment & onboarding | Infrastructure setup, deployment, team onboarding, methodology documentation. |

### Add-on: Review & Operations Dashboard — €1,200

| Feature | Description |
|---------|-------------|
| Human review workflow | Approve, edit & approve, or reject & re-run products flagged for review (~27% of products). |
| Filtering & search | Filter products by status (pending, done, error, needs review). Search by product name, EAN, brand. |
| Pagination | Handle large catalogs without browser performance issues. |
| Product deletion | Single and batch delete with safety checks (cannot delete while processing). |
| Batch scheduling | Configurable scheduled enrichment runs (daily/weekly) via settings UI. |
| Webhook callbacks | Notify downstream automations on batch completion. |
| Structured logs | Queryable enrichment history — search errors across all products, view per-phase success rates. |

### Add-on: SAP Integration — €1,250 per country

| Feature | Description |
|---------|-------------|
| Two-way integration | Pull new/updated products from SAP, push enriched data back to SAP. |
| Field mapping | Map enrichment output fields to SAP's data model and field naming conventions. |
| Format handling | Character encoding, field length constraints, SAP-specific data formats. |
| Testing | End-to-end testing against client's live SAP environment. |
| Per-country pricing | Each country requires separate SAP endpoint configuration and field mapping. |
| Self-service alternative | Client can use the included REST API at no extra cost and build their own integration. |

### Support

| Tier | Rate | Details |
|------|------|---------|
| Standard | €75/hour | Bug fixes, maintenance, prompt tuning, edge case fixes. Response within 2 business days. |
| Priority | €120/hour | Same-day response, priority queue. For production-critical issues. |

---

## 2. Example Packages

| Package | Components | Total |
|---------|------------|-------|
| **Recommended (initial)** | Core + Review Dashboard | **€6,000** |
| **With SAP (Slovenia)** | Core + Review Dashboard + SAP Integration (1 country) | **€7,250** |
| **With SAP (Slovenia + Serbia)** | Core + Review Dashboard + SAP Integration (2 countries, Serbia at 30% off) | **€8,125** |

---

## 3. Reasoning Behind the Structure

### 3.1 Why the Core is €4,800 (not €3,200 or €4,000)

**The REST API must be in core.** An enrichment engine without programmatic access is a toy. It's already built, and charging separately for it would feel like nickle-and-diming. Including it makes the core feel complete.

**Parallelization must be in core.** At 10,000+ products/month, sequential processing (~80 seconds/product = 222+ hours for a monthly batch) physically cannot complete within operational timelines. If the core doesn't include concurrent processing, it doesn't work at Shoppster's scale. Offering it as an add-on would invite the objection "so the thing you're selling me doesn't actually handle my volume?" — which kills the deal.

**Reliability and production config must be in core.** Retry logic, timeouts, stuck product recovery, cost guardrails, environment-based configuration, logging, and API authentication are not features — they're the difference between a prototype and a production system. Charging extra for "it doesn't crash" is not credible.

**The €4,800 price point** reflects the value delivered: replacing 80+ hours/month of manual work (at even €20/hour, that's €1,600/month saved). The system pays for itself within 3 months. This is a strong value proposition that makes the price easy to justify internally at Shoppster.

### 3.2 Why the Review Dashboard is an Add-on (not Core)

**The core system works without it.** Products go in, enriched data comes out, exports are available. The pipeline processes, validates, and flags products for review. The core is functionally complete.

**The gap it fills is operational comfort, not functionality.** Without the Review Dashboard, the ~27% of products flagged "needs_review" pile up with no structured way to handle them. The content team would need to inspect raw data or XLSX exports to review flagged products. That's painful but possible.

**This is the "almost irresistible" add-on.** The buyer at Shoppster will immediately understand: "Our team needs to review flagged products every day, and they can't do that efficiently without this." The ROI is obvious. At €1,200, it's an easy internal approval — cheaper than one week of a content team member's salary.

**Batch scheduling belongs here, not in core.** Scheduling is an operational convenience that pairs naturally with the review workflow. Together, they turn the core engine into a daily operational tool: products flow in automatically, get processed, and the team reviews flagged items through a structured workflow.

### 3.3 Why SAP Integration is Priced Per Country at €1,250

**The API is free — the integration work is not.** Shoppster can integrate with the system themselves using the REST API at no extra cost. The SAP Integration add-on is for WebFast to do that work for them: understand their SAP setup, build the connector, map fields, handle edge cases, test against their live environment.

**Per-country pricing is honest.** Each country means a different SAP endpoint/instance, different field mapping, different data format requirements, and separate testing. Charging per country reflects the actual work involved.

**The self-service alternative keeps it fair.** Positioning the API as "included, integrate yourself for free" makes the per-country SAP price feel like a convenience premium, not a lock-in. Shoppster can choose to save money by using their own developers, or save time by having WebFast handle it.

**Serbia discount (30% off) incentivizes multi-country.** Once the first country's integration is built, subsequent countries share some infrastructure and patterns. The discount reflects the reduced marginal effort while still compensating for the real work of a second country's configuration, mapping, and testing.

### 3.4 Why Support is Hourly (not Monthly Retainer)

**Operational constraint.** WebFast cannot reliably invoice a flat monthly retainer at this stage. Hourly billing is straightforward, transparent, and requires no commitment from either side.

**Hourly aligns incentives.** The client pays for work actually performed. No "paying for nothing" months, no "doing too much for too little" months. At 10,000 products/month, issues will come up — prompt tuning for new product categories, edge cases in extraction, occasional pipeline adjustments. Hourly billing scales naturally with actual need.

**Two tiers create urgency pricing.** Standard (€75/hour, 2 business day response) vs. Priority (€120/hour, same-day response) lets Shoppster choose based on urgency. Most issues will be Standard. When something is blocking their monthly product update, they'll gladly pay the Priority rate for same-day resolution.

### 3.5 What Was Removed and Why

| Originally proposed | Why removed |
|---|---|
| SEO product descriptions | Shoppster has separate automations that consume the enriched data to generate descriptions. This add-on would duplicate existing capability. |
| Multi-language support | The pipeline returns factual data (dimensions, weight, specs) which is largely language-independent. Shoppster handles translation on their side after SAP field mapping. |
| Category expansion (per category) | Shoppster is a general marketplace — they already sell all categories. There is no "expansion" to sell. The core system handles all product types. |
| Serbia as standalone expansion (€1,500) | The pipeline is market-agnostic. It searches autonomously in English, doesn't hardcode sources, and doesn't require country-specific prompt engineering. Serbia-specific work is limited to SAP integration (field mapping, endpoint config), which is covered by the per-country SAP Integration pricing. |
| Monthly support retainer | Replaced with hourly billing due to operational constraints. More transparent for both parties. |
| Image processing & hosting | Shoppster has existing infrastructure for image management. |
| Data quality monitoring (standalone) | Already covered by the analytics built into the frontend dashboard in the core package. |
| Auto-import from supplier feeds (standalone) | Folded into the Review Dashboard add-on as batch scheduling functionality. |

### 3.6 The Sales Path

**Expected purchase sequence:**

1. **Initial sale: €6,000** — Core (€4,800) + Review Dashboard (€1,200). This is the minimum viable purchase that gives Shoppster a fully operational enrichment system.

2. **Month 1-2: SAP Integration (Slovenia) +€1,250** — Once the team is comfortable with the system and has validated enrichment quality, they'll want to eliminate the manual CSV upload/download cycle and connect directly to SAP.

3. **When ready: SAP Integration (Serbia) +€875** — When Shoppster expands enrichment to their Serbian product catalog, they'll need the SAP connector configured for the Serbian instance.

4. **Ongoing: Support hours** — At €75-120/hour, billed as needed. Prompt tuning for edge cases, pipeline adjustments for new product patterns, maintenance.

**Total potential lifetime value:** €8,125 initial + ongoing support hours.

---

## 4. Competitive Positioning

The total initial price of €6,000 for a system that replaces 80+ hours/month of manual work is positioned as high-value, reasonable-cost. Key points for the proposal:

- **Payback period:** At even modest labor costs, the system pays for itself within 3-4 months.
- **Per-product cost:** The AI enrichment costs ~$0.02-0.06 per product in API fees. At 10,000 products/month, that's $200-600/month in operating costs — a fraction of manual labor costs.
- **Not a black box:** Every enriched data point carries source attribution (URL) and confidence scoring. Shoppster maintains full visibility into data provenance.
- **No vendor lock-in:** Complete REST API included. Shoppster can build their own integrations, switch to internal tooling, or extend the system without WebFast's involvement.
- **Market-agnostic:** Works for any product catalog regardless of country or language. No additional fees for processing products from different markets (SAP integration for each country's system is the only per-country cost).
