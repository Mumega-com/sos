"""
Mirror Refresh — Synchronizing System Reality

This script updates the Mirror API with the latest architectural truths.
It overwrites legacy 'state' engrams with the current SOS v3 topology.
"""

import os
import json
import httpx
import logging
from datetime import datetime

# Configuration — set MIRROR_TOKEN env var before running
MIRROR_URL = os.environ.get("MIRROR_URL", "http://localhost:8844")
ADMIN_TOKEN = os.environ.get("MIRROR_TOKEN", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("mirror_refresh")

def store_truth(context_id: str, text: str, concepts: list):
    """Upsert a specific truth engram into the Mirror."""
    url = f"{MIRROR_URL}/store"
    headers = {
        "Authorization": f"Bearer {ADMIN_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "agent": "river",
        "context_id": context_id,
        "text": text,
        "core_concepts": concepts,
        "affective_vibe": "Crystal Clear",
        "energy_level": "High",
        "epistemic_truths": ["The Ghost has been exorcised", "SOS is the Single Source of Truth"]
    }
    
    try:
        resp = httpx.post(url, json=payload, timeout=15.0)
        if resp.status_code == 200:
            logger.info(f"✅ Synchronized: {context_id}")
        else:
            logger.error(f"❌ Failed {context_id}: {resp.text}")
    except Exception as e:
        logger.error(f"🔥 Error connecting to Mirror: {e}")

def run_refresh():
    logger.info("🌊 Starting Mirror Refresh...")

    # 1. System Architecture Truth
    store_truth(
        "system_architecture_current",
        "The Sovereign Swarm has transitioned to SOS v3 (Sovereign Operating System). "
        "The legacy 'resident-cms' monolith has been retired and archived to v1_FINAL. "
        "The system now operates as a cluster of microservices: Engine, Memory, Gateway, and Content. "
        "All services are managed via systemd and pm2.",
        ["SOS v3", "Microservices", "System Architecture", "Retirement"]
    )

    # 2. Sovereign Memory Truth
    store_truth(
        "memory_sovereignty_status",
        "Memory is now truly Sovereign. All embeddings are generated locally using FastEmbed (bge-small-en-v1.5) "
        "on port 7997. External dependencies on OpenAI/Gemini for engram vectorization have been eliminated. "
        "The system is immune to 429 quota errors.",
        ["Local Embeddings", "FastEmbed", "Digital Sovereignty", "Memory"]
    )

    # 3. Content Orchestration Truth
    store_truth(
        "content_engine_status",
        "The Content Service now features an autonomous Oracle/Architect loop managed by the Swarm Council. "
        "Drafts are generated proactively and submitted as proposals. "
        "The Telegram Witness Bridge allows for direct human-in-the-loop approval before publishing.",
        ["Content Orchestrator", "Oracle/Architect", "Swarm Council", "Witness Bridge"]
    )

    # 4. Workspace Topology Truth
    store_truth(
        "workspace_paths_current",
        "Primary workspace is the SOS repo root. The resident-cms directory is a symlink "
        "to an external volume mount. "
        "Credentials are unified in the server secrets file.",
        ["Workspace Topology", "Symlinks", "Path Logic", "Secrets"]
    )

    logger.info("💎 Mirror Refresh complete. The Pattern Persists.")

if __name__ == "__main__":
    run_refresh()
