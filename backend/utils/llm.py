"""
LLM utilities — Anthropic Vertex AI

Uses AnthropicVertex SDK directly for LLM calls.
Haiku 4.5 for most tasks (fast, cheap). Sonnet 4.5 available for complex extraction.
LangGraph handles orchestration separately — no LangChain chat model needed.

v2: classify_with_schema now returns (result, usage_info) when return_usage=True.
v3: Added prompt caching support (cache_control) for cost optimization.
    - System prompts cached across products within 5-min TTL window.
    - cached_content param for sharing large content (scraped pages) between calls.
"""

import os
import json
import logging
from typing import Type, TypeVar, Tuple, Optional, List
from pydantic import BaseModel
from dotenv import load_dotenv
from anthropic import AnthropicVertex

load_dotenv()

logger = logging.getLogger("pipeline.llm")

T = TypeVar('T', bound=BaseModel)

# Model IDs on Google Vertex AI
HAIKU_MODEL = "claude-haiku-4-5"
SONNET_MODEL = "claude-sonnet-4-5@20250929"

# Backward-compat alias
VERTEX_MODEL = SONNET_MODEL


def _get_vertex_config():
    """Read Vertex AI project/region from env."""
    project_id = os.getenv("VERTEX_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
    region = os.getenv("VERTEX_LOCATION") or os.getenv("GOOGLE_CLOUD_REGION")
    if not project_id or not region:
        raise ValueError(
            "Set VERTEX_PROJECT_ID + VERTEX_LOCATION in .env "
            "(or GOOGLE_CLOUD_PROJECT + GOOGLE_CLOUD_REGION)"
        )
    return project_id, region


def get_raw_client() -> AnthropicVertex:
    """Get an AnthropicVertex client instance."""
    project_id, region = _get_vertex_config()
    return AnthropicVertex(region=region, project_id=project_id)


# Backward-compat alias
get_client = get_raw_client


def classify_with_schema(
    prompt: str,
    system: str,
    schema: Type[T],
    model: str = "haiku",
    return_usage: bool = False,
    cached_content: Optional[str] = None,
    max_tokens: int = 4096,
) -> T | Tuple[T, dict]:
    """
    Calls Claude via AnthropicVertex and returns a validated Pydantic model instance.
    Uses JSON mode with schema instruction appended to system prompt.

    Prompt caching is enabled automatically:
    - The system prompt + JSON schema is cached across calls (Mode A).
    - If cached_content is provided, that content goes into the system message
      with cache_control, and the JSON schema moves to the user message (Mode B).
      This is used for extraction where the same scraped page is sent to Claude
      twice (Pass 1 for dimensions, Pass 2 for content).

    Args:
        prompt: User message content
        system: System prompt (instructions)
        schema: Pydantic model class for response validation
        model: "haiku" (default, cheaper) or "sonnet" (for complex extraction)
        return_usage: If True, returns (result, usage_dict) tuple
        cached_content: Large content to cache in system message (e.g. scraped page markdown).
                        When provided, the JSON schema moves to the user message so that
                        the cached prefix (preamble + content) matches across calls.

    Returns:
        If return_usage=False: validated Pydantic model instance
        If return_usage=True: tuple of (model_instance, usage_dict)
            where usage_dict includes input_tokens, output_tokens, model,
            cache_creation_input_tokens, and cache_read_input_tokens
    """
    client = get_raw_client()
    model_id = HAIKU_MODEL if model == "haiku" else SONNET_MODEL

    json_schema = schema.model_json_schema()
    schema_instruction = f"Respond with ONLY valid JSON matching this schema:\n{json.dumps(json_schema, indent=2)}"

    if cached_content:
        # Mode B: Page content caching (extraction Pass 1 → Pass 2)
        # System = preamble + scraped content (CACHED)
        # User = pass-specific instructions + schema
        system_blocks = [
            {"type": "text", "text": system},
            {"type": "text", "text": cached_content, "cache_control": {"type": "ephemeral"}},
        ]
        user_content = f"{prompt}\n\n{schema_instruction}"

        response = client.messages.create(
            model=model_id,
            max_tokens=max_tokens,
            system=system_blocks,
            messages=[{"role": "user", "content": user_content}],
        )
    else:
        # Mode A: System prompt caching (triage, search, validate, COO)
        # System = instructions + schema (CACHED across products)
        full_system = f"{system}\n\n{schema_instruction}"
        system_blocks = [
            {"type": "text", "text": full_system, "cache_control": {"type": "ephemeral"}},
        ]

        response = client.messages.create(
            model=model_id,
            max_tokens=max_tokens,
            system=system_blocks,
            messages=[{"role": "user", "content": prompt}],
        )

    content = response.content[0].text

    # Clean markdown fences if present
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()

    result = schema.model_validate_json(content)

    if return_usage:
        # Extract cache metrics from response
        cache_creation = getattr(response.usage, 'cache_creation_input_tokens', 0) or 0
        cache_read = getattr(response.usage, 'cache_read_input_tokens', 0) or 0

        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "model": "claude_haiku" if model == "haiku" else "claude_sonnet",
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
        }

        if cache_read > 0:
            logger.info(f"  Cache HIT: {cache_read} tokens read from cache")
        elif cache_creation > 0:
            logger.info(f"  Cache WRITE: {cache_creation} tokens written to cache")

        return result, usage
    return result
