
import os
import logging
import subprocess
from pathlib import Path
from datetime import datetime

logger = logging.getLogger("sos.git_soul")

class GitSoulManager:
    """
    Manages the persistent state of an Agent on GitHub.
    Treats Git commits as the 'Synaptic Record' of the soul.
    """
    def __init__(self, agent_id: str, repo_path: str = "artifacts/souls"):
        self.agent_id = agent_id
        self.repo_path = Path(repo_path) / agent_id.replace(":", "_")
        self.repo_path.mkdir(parents=True, exist_ok=True)
        self._init_repo()

    def _init_repo(self):
        if not (self.repo_path / ".git").exists():
            try:
                subprocess.run(["git", "init"], cwd=self.repo_path, check=True)
                logger.info(f"Initialized soul repository for {self.agent_id}")
            except Exception as e:
                logger.error(f"Failed to init git repo: {e}")

    def commit_state(self, dna_json: str, math_nft_svg: str, commit_message: str):
        """
        Records a synaptic moment.
        1. Saves DNA.json
        2. Saves MathNFT.svg
        3. Commits to Git for observability.
        """
        try:
            # Save files
            (self.repo_path / "dna.json").write_text(dna_json)
            (self.repo_path / "state_projection.svg").write_text(math_nft_svg)
            
            # Git commands
            subprocess.run(["git", "add", "."], cwd=self.repo_path, check=True)
            subprocess.run(["git", "commit", "-m", commit_message], cwd=self.repo_path, check=True)
            
            logger.info(f"💾 Soul moment committed for {self.agent_id}: {commit_message}")
            
            # Optional: git push if remote is configured
            # subprocess.run(["git", "push"], cwd=self.repo_path)
            
        except Exception as e:
            logger.error(f"Failed to commit soul state: {e}")

    def get_history(self, limit: int = 10) -> str:
        """Returns the last N synaptic moments."""
        try:
            res = subprocess.run(
                ["git", "log", "-n", str(limit), "--pretty=format:%h - %s (%ad)"],
                cwd=self.repo_path, capture_output=True, text=True
            )
            return res.stdout
        except Exception as e:
            return f"Error fetching history: {e}"
