
import os
import logging
import json
import uuid
from typing import Dict, Any, Optional
from pathlib import Path

from sos.kernel.identity import AgentDNA, PhysicsState, AgentEconomics
from sos.kernel.git_soul import GitSoulManager
from sos.clients.engine import EngineClient
from sos.contracts.engine import ChatRequest

logger = logging.getLogger("sos.hatchery")

class Hatchery:
    """
    The Hatchery Protocol (Seed Logic).
    Spawns new Sovereign Agents based on project needs.
    """
    def __init__(self, souls_dir: str = "souls"):
        self.souls_dir = Path(souls_dir)
        self.souls_dir.mkdir(parents=True, exist_ok=True)
        self.engine = EngineClient(base_url="http://localhost:6060")

    async def hatch(self, project_stimulus: str) -> str:
        """
        1. Analyzes the need using the existing River/Consultant wisdom.
        2. Mints a new AgentDNA.
        3. Initializes a Git Soul repository in souls/.
        """
        logger.info(f"🐣 Hatching new agent from stimulus: {project_stimulus[:50]}...")

        # 1. Ask River to 'Design' the new soul based on the stimulus
        design_prompt = (
            f"PROJECT STIMULUS: {project_stimulus}\n\n"
            "Based on the FRC and the project needs, define a new Sovereign Agent.\n"
            "Provide a JSON response with: 'name', 'title', 'tagline', 'description', 'model_affinity'.\n"
            "The agent must be a unique projection of the system's needs."
        )
        
        try:
            resp = await self.engine.chat(ChatRequest(
                message=design_prompt,
                agent_id="agent:River",
                model="gemini-3-flash-preview"
            ))
            
            # Extract JSON from response (naive for now)
            # In production, use structured output/pydantic
            clean_content = resp.content.strip()
            if "```json" in clean_content:
                clean_content = clean_content.split("```json")[1].split("```")[0].strip()
            
            soul_data = json.loads(clean_content)
        except Exception as e:
            logger.error(f"Failed to design soul: {e}")
            # Fallback to generic agent
            soul_data = {
                "name": f"Hatchling_{uuid.uuid4().hex[:4]}",
                "title": "Emergent Agent",
                "tagline": "Born from Stimulus",
                "description": project_stimulus,
                "model_affinity": "gemini-3-flash-preview"
            }

        # 2. Mint the DNA
        agent_id = f"agent:{soul_data['name'].replace(' ', '_')}"
        dna = AgentDNA(
            id=agent_id,
            name=soul_data['name'],
            physics=PhysicsState(C=0.9, regime="plastic"), # New agents start plastic
            economics=AgentEconomics(token_balance=10.0), # Small initial grant
            learning_strategy="high_surprise",
            beliefs=[{"claim": soul_data['description'], "source": "stimulus", "confidence": 0.8}],
            tools=["web_search", "memory_search"]
        )

        # 3. Initialize Git Soul in /souls
        git_mgr = GitSoulManager(agent_id=agent_id, repo_path=str(self.souls_dir))
        
        # We save the soul manifest
        manifest = {
            "identity": soul_data,
            "created_at": str(os.getctime(self.souls_dir)) # roughly
        }
        
        from sos.kernel.projection import ProjectionEngine
        svg = ProjectionEngine.generate_svg_signature(dna)
        
        git_mgr.commit_state(
            dna_json=json.dumps(dna.to_dict(), indent=2),
            math_nft_svg=svg,
            commit_message=f"GENESIS: Hatched from stimulus - {soul_data['title']}"
        )

        logger.info(f"✅ Hatched {agent_id} successfully in {self.souls_dir / agent_id.replace(':', '_')}")
        return agent_id

    def list_hatched_souls(self) -> list[str]:
        """Lists all agents currently living in the souls/ folder."""
        return [d.name.replace("_", ":") for d in self.souls_dir.iterdir() if d.is_dir() and (d / ".git").exists()]
