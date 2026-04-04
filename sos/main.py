
"""
SOS | Sovereign Operating System - Main Entry Point.
"""

import sys
import argparse
import asyncio
import logging
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from sos.kernel import Config
from sos.observability.logging import get_logger

log = get_logger("sos_main")

def main():
    parser = argparse.ArgumentParser(description="Sovereign OS (SOS) CLI")
    
    # Modes
    parser.add_argument("--telegram", action="store_true", help="Start the Telegram Adapter")
    parser.add_argument("--discord", action="store_true", help="Start the Discord Adapter")
    parser.add_argument("--engine", action="store_true", help="Start the Engine Service")
    parser.add_argument("--deck", action="store_true", help="Open the Command Deck (Local)")
    parser.add_argument("--doctor", action="store_true", help="Run SOS doctor checks")
    
    args = parser.parse_args()

    if args.telegram:
        from sos.adapters.telegram import start_telegram_adapter
        asyncio.run(start_telegram_adapter())
    
    elif args.discord:
        from sos.adapters.discord import start_discord_adapter
        asyncio.run(start_discord_adapter())
    
    elif args.engine:
        # This would start the FastAPI service
        import uvicorn
        from sos.services.engine.app import app
        uvicorn.run(app, host="0.0.0.0", port=8000)
        
    elif args.deck:
        print("🚀 Opening the Command Deck: https://mumega.com/deck")
        # In a desktop env, we would use: webbrowser.open("https://mumega.com/deck")

    elif args.doctor:
        from sos.observability.doctor import run_doctor
        raise SystemExit(run_doctor())
    
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
