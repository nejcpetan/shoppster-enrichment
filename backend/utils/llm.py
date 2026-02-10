
import os
import json
from typing import Type, TypeVar
from pydantic import BaseModel
from dotenv import load_dotenv
from anthropic import AnthropicVertex

load_dotenv()

T = TypeVar('T', bound=BaseModel)

# Claude Sonnet 4.5 on Vertex AI
VERTEX_MODEL = "claude-sonnet-4-5@20250929"

def get_client():
    # Support both naming conventions: VERTEX_* (used in .env) and GOOGLE_CLOUD_* (SDK convention)
    project_id = os.getenv("VERTEX_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
    region = os.getenv("VERTEX_LOCATION") or os.getenv("GOOGLE_CLOUD_REGION")
    
    if not project_id or not region:
        raise ValueError(
            "Project ID and region must be set. "
            "Set VERTEX_PROJECT_ID + VERTEX_LOCATION (or GOOGLE_CLOUD_PROJECT + GOOGLE_CLOUD_REGION) in .env"
        )
        
    return AnthropicVertex(region=region, project_id=project_id)

def classify_with_schema(prompt: str, system: str, schema: Type[T]) -> T:
    """
    Calls Claude Sonnet 4.5 on Vertex AI to classify/extract data matching a Pydantic schema.
    Returns a validated Pydantic model instance.
    """
    client = get_client()

    # Get the JSON schema from the Pydantic model
    json_schema = schema.model_json_schema()
    
    # Force the model to output JSON
    messages = [
        {"role": "user", "content": prompt}
    ]

    # Append schema instruction to system prompt
    full_system_prompt = f"""{system}

Respond with ONLY valid JSON matching this schema:
{json.dumps(json_schema, indent=2)}
"""

    try:
        response = client.messages.create(
            model=VERTEX_MODEL, 
            max_tokens=1024,
            system=full_system_prompt,
            messages=messages
        )

        content = response.content[0].text
        
        # Simple cleanup if Claude adds markdown fences
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        # Parse and validate
        return schema.model_validate_json(content)

    except Exception as e:
        print(f"Vertex AI LLM Error (model={VERTEX_MODEL}): {e}")
        raise e
