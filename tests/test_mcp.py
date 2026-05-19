import asyncio
import sys
from sos.clients.tools import ToolsClient

async def main():
    client = ToolsClient("http://localhost:8003")
    
    print("Checking Health...")
    try:
        health = client.health()
        print(f"Health: {health}")
    except Exception as e:
        print(f"Health Check Failed: {e}")
        return

    print("\nTesting Web Search (Dockerized)...")
    payload = {
        "tool_name": "web_search",
        "arguments": {
            "query": "What is the capital of France?",
            "count": 1,
            "provider": "duckduckgo" # Force DDG to avoid Tavily key need
        }
    }
    
    try:
        # Note: ToolsClient.execute expects an object with tool_name/arguments or dict
        # My implementation handles dict.
        result = await client.execute(payload)
        print(f"Result: {result}")
    except Exception as e:
        print(f"Execution Failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
