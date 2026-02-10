"""
EAN Lookup utility — scrapes barcodelookup.com via Firecrawl.
Returns brand, product name, and category.
"""

import os
from firecrawl import FirecrawlApp
from utils.llm import classify_with_schema
from schemas import BarcodeLookupResult


async def lookup_ean(ean: str) -> dict | None:
    """
    Scrapes barcodelookup.com/{ean} via Firecrawl.
    Returns {brand: str, product_name: str, category: str} or None.
    """
    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        print("Warning: FIRECRAWL_API_KEY not found. Skipping EAN lookup.")
        return None

    try:
        app = FirecrawlApp(api_key=api_key)

        url = f"https://www.barcodelookup.com/{ean}"
        print(f"Scraping {url} for EAN lookup...")

        scraped = app.scrape(url, formats=['markdown'])

        # Handle both Document object and dict response
        markdown = ''
        if hasattr(scraped, 'markdown') and scraped.markdown:
            markdown = scraped.markdown
        elif isinstance(scraped, dict):
            markdown = scraped.get('markdown', '')

        if not markdown:
            print("Firecrawl returned no markdown.")
            return None

        system_prompt = """Extract product information from this barcodelookup.com page content.
Return ONLY valid JSON with these fields:
- brand: the manufacturer/brand name
- product_name: the full product name
- category: the product category
If the information is not found, set the field to null."""

        user_prompt = f"Extract product info from this content:\n\n{markdown[:15000]}"

        result = classify_with_schema(
            prompt=user_prompt,
            system=system_prompt,
            schema=BarcodeLookupResult
        )

        if result.brand or result.product_name:
            return result.model_dump()

        return None

    except Exception as e:
        print(f"EAN Lookup failed: {e}")
        return None
