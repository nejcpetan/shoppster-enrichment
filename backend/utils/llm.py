"""
LLM utilities — Anthropic Vertex AI

Uses AnthropicVertex SDK directly for LLM calls.
Haiku 4.5 for most tasks (fast, cheap). Sonnet 4.5 available for complex extraction.
LangGraph handles orchestration separately — no LangChain chat model needed.

v2: classify_with_schema now returns (result, usage_info) when return_usage=True.
"""

import os
import json
from typing import Type, TypeVar, Tuple, Optional
from pydantic import BaseModel
from dotenv import load_dotenv
from anthropic import AnthropicVertex

load_dotenv()

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
    return_usage: bool = False
) -> T | Tuple[T, dict]:
    """
    Calls Claude via AnthropicVertex and returns a validated Pydantic model instance.
    Uses JSON mode with schema instruction appended to system prompt.

    Args:
        prompt: User message content
        system: System prompt
        schema: Pydantic model class for response validation
        model: "haiku" (default, cheaper) or "sonnet" (for complex extraction)
        return_usage: If True, returns (result, usage_dict) tuple

    Returns:
        If return_usage=False: validated Pydantic model instance
        If return_usage=True: tuple of (model_instance, usage_dict)
            where usage_dict = {"input_tokens": int, "output_tokens": int, "model": str}
    """
    client = get_raw_client()
    model_id = HAIKU_MODEL if model == "haiku" else SONNET_MODEL

    json_schema = schema.model_json_schema()

    full_system = f"""{system}

Respond with ONLY valid JSON matching this schema:
{json.dumps(json_schema, indent=2)}"""

    try:
        response = client.messages.create(
            model=model_id,
            max_tokens=2048,
            system=full_system,
            messages=[{"role": "user", "content": prompt}]
        )

        content = response.content[0].text

        # Clean markdown fences if present
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        result = schema.model_validate_json(content)

        if return_usage:
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "model": "claude_haiku" if model == "haiku" else "claude_sonnet",
            }
            return result, usage
        return result

    except Exception as e:
        print(f"LLM Error (model={model_id}): {e}")
        raise e
