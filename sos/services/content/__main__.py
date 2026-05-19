"""
Run the Content Service.

Usage:
    python -m sos.services.content
    uvicorn sos.services.content.app:app --port 8020
"""

import os
import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("SOS_CONTENT_PORT", "8020"))
    host = os.getenv("SOS_CONTENT_HOST", "127.0.0.1")

    uvicorn.run(
        "sos.services.content.app:app",
        host=host,
        port=port,
        reload=os.getenv("SOS_DEV_MODE", "false").lower() == "true",
    )
