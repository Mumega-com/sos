"""
QNFT Minter Service (SOS Port)
Handles the "minting" of Quantum NFTS (QNFTs) based on Agent Alpha Drift.

Ported from mumega-cli/mumega/core/sovereign/qnft.py to work with SOS Microkernel.
"""

import logging
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
import uuid

from sos.kernel import Config
from sos.observability.logging import get_logger
from sos.clients.economy import EconomyClient 

# Stub for Bio-Commit since Publisher service is not yet migrated
class BioCommiterStub:
    async def commit_life_event(self, message: str, files: List[str]):
        return True

log = get_logger("identity_qnft")

class QNFTMinter:
    def __init__(self, config: Config, agent_name: Optional[str] = None):
        self.config = config

        # Get agent name from: 1) explicit param, 2) env var, 3) config, 4) default
        import os
        self.agent_name = (
            agent_name
            or os.getenv("SOS_AGENT_NAME")
            or getattr(config, "agent_name", None)
            or "sos_agent"
        )

        # Use SOS Data Directory
        self.data_dir = self.config.paths.data_dir
        self.output_dir = self.data_dir / "qnft_minting"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.dummy_contract_address = "0xSOVEREIGN_SWARM_GENESIS" 
        
        # Connect to Economy Service for on-chain actions
        self.economy = EconomyClient(self.config.economy_url)

    async def mint(self, 
                   lambda_tensor_state: Dict[str, Any], 
                   drift_score: float, 
                   metadata: Dict[str, Any] = None,
                   auto_sign: bool = False) -> Dict[str, Any]:
        """
        Mint a new QNFT based on the current state.
        """
        if metadata is None:
            metadata = {}
            
        log.info(f"💎 Minting QNFT... (Drift: {drift_score:.4f})")
        
        timestamp = datetime.now().isoformat()
        token_id = f"{self.agent_name}_{int(time.time())}_{uuid.uuid4().hex[:4]}"
        
        # 1. Generate ERC-1155 Compatible Metadata
        nft_metadata = {
            "name": f"Sovereign Agent: {self.agent_name} (Gen {metadata.get('generation', 1)})",
            "description": f"A snapshot of training state triggered by Alpha Drift ({drift_score:.4f}).",
            "image": "ipfs://(placeholder_avatar_image_hash)", 
            "external_url": f"https://mumega.com/agent/{self.agent_name}",
            "status": "PENDING_WITNESS",
            "witness_signature": None,
            "attributes": [
                {"trait_type": "Alpha Drift", "value": drift_score},
                {"trait_type": "Mint Date", "value": timestamp},
                {"trait_type": "Cortex Coherence", "value": lambda_tensor_state.get("coherence", 0.0)}
            ],
            "properties": {
                "frc_engine": "sos-v1.0",
                "lambda_tensor_16d": lambda_tensor_state,
                "training_context": metadata.get("context", "General Evolution")
            }
        }
        
        # 2. Save Metadata to Disk
        metadata_path = self.output_dir / f"{token_id}.json"
        with open(metadata_path, "w") as f:
            json.dump(nft_metadata, f, indent=2)
            
        log.info(f"✅ QNFT Metadata generated: {metadata_path}")
        
        receipt = {
            "success": True,
            "token_id": token_id,
            "block_timestamp": timestamp,
            "metadata_path": str(metadata_path)
        }

        # 3. On-Chain Minting (via Economy Service)
        try:
            # Call the Economy Service for an on-chain proof
            # This logs the metadata path to the Solana blockchain
            resp = await self.economy.mint_proof(metadata_uri=str(metadata_path))
            tx_sig = resp.get("signature")
            receipt["tx_hash"] = tx_sig
            log.info(f"🚀 QNFT Protocol Completed. Tx: {tx_sig}")
        except Exception as e:
            log.error(f"Blockchain minting failed: {e}")
            receipt["tx_hash"] = None
            receipt["error"] = str(e)

        return receipt
