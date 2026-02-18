import sys
import os
import logging
from dotenv import load_dotenv

# Setup logging to stdout
logging.basicConfig(level=logging.INFO, stream=sys.stdout)

# Add backend to path so imports work
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.search import search_node

load_dotenv()

# Check env
print(f"SEARCH_PROVIDER: {os.getenv('SEARCH_PROVIDER')}")
print(f"FIRECRAWL_API_KEY: {os.getenv('FIRECRAWL_API_KEY')[:10]}...")

# Mock state
# Assuming product 21 allows us to search (has classification)
state = {
    "product_id": 21,
    "cost_tracker": None
}

print("Running search_node...")
import asyncio
try:
    result = asyncio.run(search_node(state))
    print("Result:", result)
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
