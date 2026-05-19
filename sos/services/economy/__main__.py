from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.getenv("SOS_ECONOMY_HOST", "0.0.0.0")
    port = int(os.getenv("SOS_ECONOMY_PORT", "8002"))
    uvicorn.run(
        "sos.services.economy.app:app",
        host=host,
        port=port,
        log_level=os.getenv("SOS_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()

