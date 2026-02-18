# Self-Improving Pipeline & Advanced Cost Reduction Research

> **Date:** 2026-02-18
> **Goal:** Find approaches to make the enrichment pipeline learn from its own data, improve quality over time, and further reduce costs

---

## 1. DSPy (Stanford NLP) — Automatic Prompt Optimization

This is probably the closest to a "self-improving" pipeline. [DSPy](https://dspy.ai) compiles "declarative language model calls into self-improving pipelines."

**How it works for your case:**
- You define your extraction task as a DSPy "signature" (input → output) instead of a handwritten prompt
- You provide ~30-300 examples of good extractions (you already have these in your DB from completed products)
- DSPy's optimizers (`BootstrapFewShot`, `MIPROv2`) automatically:
  - Generate optimal few-shot examples from your data
  - Rewrite your instruction prompts to maximize quality on your metric
  - Find the minimal prompt that achieves target quality (reducing tokens = reducing cost)
- A typical optimization run costs ~$2 and takes ~20 minutes

**DSPy Optimizer Types:**
- **BootstrapFewShot**: Generates effective example demonstrations for modules
- **MIPROv2**: Proposes and intelligently explores better natural-language instructions for every prompt
- **GEPA**: Advanced instruction refinement
- **BootstrapFinetune**: Builds datasets to finetune model weights within the system

**Data Requirements (from DSPy docs):**
- Training set: 20% of your data (ideally 300+ examples, minimum 30)
- Validation set: 80% of your data for stable evaluation
- This reverse allocation (vs traditional ML) prevents overfitting to small training sets

**Practical fit for your pipeline:** Your validation phase already produces a quality score. That score could be your DSPy metric. DSPy would then optimize the extraction prompts to maximize that score while minimizing token usage. Each time you accumulate more validated products, you can re-optimize.

---

## 2. Few-Shot Learning From Your Own Successful Extractions

This is simpler than DSPy and doesn't require a framework. The idea:

- You already have hundreds of completed products with `extraction_result` and `validation_result` in your DB
- Select the top 3-5 highest-quality extractions (based on validation score)
- Inject them as examples into your extraction prompts: "Here's an example of a good extraction from a similar product page..."
- The LLM learns from your real outputs what format, detail level, and accuracy you expect

**Cost impact:** Few-shot examples add input tokens, but they dramatically improve first-pass accuracy — meaning fewer retries, fewer gap-fill calls, and potentially allowing you to drop from 3 URLs to 1-2 because the extraction is better on the first try.

**Self-improving loop:** As your DB grows, you can periodically re-select the best examples, so the system naturally improves as it processes more products.

---

## 3. Anthropic Batch API — 50% Off Everything

This one isn't "learning" but it's a massive cost lever. The [Message Batches API](https://platform.claude.com/docs/en/docs/build-with-claude/batch-processing) gives you **50% off all token pricing**:

| Model | Standard Input | Batch Input | Standard Output | Batch Output |
|---|---|---|---|---|
| Claude Haiku 4.5 | $1.00/MTok | **$0.50/MTok** | $5.00/MTok | **$2.50/MTok** |

**How it fits:** Your pipeline already processes products in batches (max 50). Instead of calling Claude sequentially per product, you could:
1. Collect all triage prompts for a batch → submit as one Batch API request
2. Wait for results (usually < 1 hour, often minutes)
3. Collect all search classification prompts → submit as batch
4. And so on per phase

This requires restructuring from sync sequential to async batch-oriented processing, but it cuts your entire Claude bill in half.

**From the Anthropic docs:**
- Batch limit: 100,000 requests or 256 MB per batch
- Most batches finish in < 1 hour
- Results available for 29 days
- All features supported (vision, tool use, system messages, etc.)
- Prompt caching + Batch API discounts stack

---

## 4. Prompt Caching — 90% Off System Prompts

From the [Anthropic docs](https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching): cache hits on Haiku 4.5 cost **$0.10/MTok** vs $1.00/MTok base — a 90% reduction on cached portions.

Your system prompts + JSON schemas are identical across all products. With `cache_control: {"type": "ephemeral"}` on the system message, after the first call in a batch run, all subsequent calls pay 10% for the cached system prompt portion.

**Prompt Caching Pricing (Haiku 4.5):**

| Token Type | Price |
|---|---|
| Base input | $1.00/MTok |
| 5m cache write | $1.25/MTok |
| 1h cache write | $2.00/MTok |
| Cache read (hit) | **$0.10/MTok** |
| Output | $5.00/MTok |

**Implementation in your code** — change `llm.py:86-91` from:

```python
response = client.messages.create(
    model=model_id,
    max_tokens=2048,
    system=full_system,
    messages=[{"role": "user", "content": prompt}]
)
```

to:

```python
response = client.messages.create(
    model=model_id,
    max_tokens=2048,
    system=[{
        "type": "text",
        "text": full_system,
        "cache_control": {"type": "ephemeral"}
    }],
    messages=[{"role": "user", "content": prompt}]
)
```

**How prompt caching works:**
1. System checks if a prompt prefix (up to a cache breakpoint) is already cached
2. If found, it uses the cached version (90% cheaper)
3. Otherwise, it processes the full prompt and caches the prefix
4. Default cache TTL: 5 minutes (refreshed on each hit); 1-hour TTL available at higher cost
5. Up to 4 cache breakpoints per request
6. Cache is organization-isolated (workspace-isolated starting Feb 2026)

**Caveat:** Minimum cacheable size on Haiku 4.5 is 4,096 tokens. Your extraction system prompts (with the JSON schema) likely hit this. Triage/search prompts might be too short to cache.

**Tracking cache performance — response includes:**
- `cache_creation_input_tokens`: tokens written to cache (new entry)
- `cache_read_input_tokens`: tokens read from cache (hit)
- `input_tokens`: tokens after the last cache breakpoint (not cached)

---

## 5. Semantic Caching — Skip LLM Calls for Similar Products

This is the "learn from past data" approach applied to caching:

- Embed each product's input (name + EAN + brand) using a cheap embedding model
- Before calling Claude, check if a very similar product was already extracted
- If cosine similarity > threshold, reuse the previous extraction's template (dimensions, specs structure) and only call the LLM for product-specific values

For example, if you've already enriched "Makita DHP482Z" and now get "Makita DHP483Z" (same product line, different model), the dimensions, features structure, and warranty are likely nearly identical.

---

## 6. Distillation — Train a Smaller/Cheaper Model on Your Data

Once you have enough successful extractions (500+), you can:
- Export your (prompt, response) pairs from the cost tracker logs
- Fine-tune a smaller model (or use a cheaper model like Haiku 3 at $0.25/MTok input) on your specific extraction task
- The fine-tuned model learns your exact output format and domain, potentially performing as well as Haiku 4.5 on your specific task at a fraction of the cost

---

## Recommended Priority For This Pipeline

In order of bang-for-buck:

| Priority | Approach | Effort | Impact |
|---|---|---|---|
| 1 | Prompt caching | 5 lines of code | 10-20% off immediately |
| 2 | Merge Pass 1+2 (from cost optimization doc) | Medium refactor | 35-45% off |
| 3 | Few-shot from your own DB | Add example selection logic | Better quality → fewer retries |
| 4 | Batch API | Architecture change | 50% off all Claude calls |
| 5 | DSPy optimization | New dependency, learning curve | Optimized prompts + quality |
| 6 | Semantic caching | Embedding infrastructure | Skip calls entirely for similar products |

The combination of prompt caching (#1), merging passes (#2), and the Batch API (#4) would get you roughly **70-80% cost reduction** with no quality loss. Adding few-shot learning (#3) or DSPy (#5) on top would then improve quality while potentially reducing tokens further.

---

## Sources

- [DSPy Framework](https://dspy.ai) — Stanford NLP
- [DSPy GitHub](https://github.com/stanfordnlp/dspy)
- [DSPy Optimization Overview](https://dspy.ai/learn/optimization/overview/)
- [Anthropic Prompt Caching Docs](https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching)
- [Anthropic Batch Processing Docs](https://platform.claude.com/docs/en/docs/build-with-claude/batch-processing)
- [Anthropic Prompt Caching Cookbook](https://platform.claude.com/cookbook/misc-prompt-caching)
