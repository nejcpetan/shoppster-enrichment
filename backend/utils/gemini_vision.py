"""
Gemini Vision Agent — Image Analysis

Uses Gemini 3.0 Flash via Vertex AI for:
1. Product color detection from images
2. Batch image description for filtering non-product images
"""

import os
import re
import logging
from typing import List, Dict, Optional
from dotenv import load_dotenv
from schemas import EnrichedField

load_dotenv()

GEMINI_MODEL = "gemini-3-flash-preview"

logger = logging.getLogger("pipeline.gemini_vision")


def _get_model():
    """Initialize and return Gemini model. Uses global endpoint for preview models."""
    import vertexai
    from vertexai.generative_models import GenerativeModel

    project_id = os.getenv("VERTEX_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")

    if not project_id:
        raise ValueError("Missing VERTEX_PROJECT_ID")

    # Gemini 3 Flash Preview requires the global endpoint
    vertexai.init(project=project_id, location="global")
    return GenerativeModel(GEMINI_MODEL)


def detect_color_from_image(image_url: str) -> EnrichedField | None:
    """
    Uses Gemini 3.0 Flash vision to detect the primary product color from an image URL.
    Returns an EnrichedField or None if detection fails.
    """
    from vertexai.generative_models import Part

    try:
        model = _get_model()

        prompt = """Look at this product image and determine the PRIMARY color of the product itself (not the background or packaging).

Rules:
- Return ONLY the color name in English (e.g., "black", "red", "silver/metallic", "blue", "green", "yellow", "orange", "white", "gray")
- For metallic tools or wire products, say "silver/metallic"
- For multi-colored products, list the dominant color first: e.g., "black and red"
- If the product is in a container (oil, chemicals), describe the CONTAINER color
- If you cannot determine the color, say "unknown"

Respond with ONLY the color name, nothing else."""

        response = model.generate_content([
            Part.from_uri(image_url, mime_type=_guess_mime(image_url)),
            prompt
        ])

        color_text = response.text.strip().lower()
        color_text = re.sub(r'[^a-z/\s]', '', color_text).strip()

        if not color_text or color_text == 'unknown':
            return None

        return EnrichedField(
            value=color_text,
            confidence="inferred",
            notes="Color detected by Gemini 3.0 Flash Vision",
            source_url=image_url,
        )

    except Exception as e:
        logger.warning(f"Gemini color detection failed: {e}")
        return None


def describe_images(image_urls: List[str], product_name: str = "") -> List[Dict[str, str]]:
    """
    Uses Gemini 3.0 Flash to describe a batch of images.
    Returns a list of {"url": str, "description": str} dicts.
    
    Processes images individually to avoid failures from one bad URL
    blocking the entire batch.
    """
    from vertexai.generative_models import Part

    if not image_urls:
        return []

    try:
        model = _get_model()
    except Exception as e:
        logger.error(f"Gemini init failed: {e}")
        return [{"url": url, "description": "unknown"} for url in image_urls]

    results = []
    for url in image_urls:
        try:
            prompt = f"""Describe this image in ONE short sentence (max 15 words).

Focus on: What is the main subject? Is it a product photo, icon, logo, UI element, flag, badge, or something else?

Context: We are looking for product images related to: {product_name}

Examples of good descriptions:
- "Product photo of a wire brush attachment for power tools"
- "Close-up of hedge trimmer cutting blades"
- "Website shopping cart icon (SVG)"
- "Country flag of Denmark"
- "Star rating graphic showing 4/5 stars"
- "Company logo for Makita tools"
- "Product packaging box for garden tool"

Respond with ONLY the description, nothing else."""

            response = model.generate_content([
                Part.from_uri(url, mime_type=_guess_mime(url)),
                prompt
            ])
            description = response.text.strip()
            logger.info(f"  Image: {url[-50:]} → {description}")
            results.append({"url": url, "description": description})

        except Exception as e:
            logger.warning(f"  Image describe failed ({url[-40:]}): {e}")
            results.append({"url": url, "description": "failed_to_analyze"})

    return results


def _guess_mime(url: str) -> str:
    """Guess MIME type from URL extension."""
    url_lower = url.lower()
    if '.png' in url_lower: return 'image/png'
    if '.webp' in url_lower: return 'image/webp'
    if '.gif' in url_lower: return 'image/gif'
    return 'image/jpeg'  # default
