
import os
import json
from firecrawl import FirecrawlApp
from pydantic import BaseModel
from typing import Optional
from utils.llm import classify_with_schema

class BarcodeLookupResult(BaseModel):
    brand: Optional[str] = None
    product_name: Optional[str] = None
    category: Optional[str] = None

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
        
        # Scrape the page
        url = f"https://www.barcodelookup.com/{ean}"
        print(f"Scraping {url} for EAN lookup...")
        
        scrape_result = app.scrape_url(url, params={"formats": ["markdown"]})
        
        if not scrape_result or 'markdown' not in scrape_result:
            print("Firecrawl returned no markdown.")
            return None
            
        markdown = scrape_result['markdown']
        
        # Use LLM to parsing
        system_prompt = """Extract product information from this barcodelookup.com page content.
Return ONLY valid JSON with these fields:
- brand: the manufacturer/brand name
- product_name: the full product name
- category: the product category
If the information is not found, set the field to null."""

        user_prompt = f"Extract product info from this content:\n\n{markdown[:15000]}" # Limit context if needed

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
