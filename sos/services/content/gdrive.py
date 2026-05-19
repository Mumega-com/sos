"""
Google Drive CMS Integration

Wraps the gdrive-cms tool for SOS content management.
Syncs Google Docs → MDX/Markdown for the website.
"""

import os
import subprocess
import json
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)

GDRIVE_CMS_PATH = Path(os.environ.get("GDRIVE_CMS_PATH", str(Path.home() / "gdrive-cms")))


@dataclass
class SyncResult:
    """Result of a sync operation"""
    success: bool
    synced_count: int
    errors: List[str]
    files: List[str]


class GDriveCMS:
    """
    Google Drive CMS wrapper for SOS.

    Uses the standalone gdrive-cms tool to sync Google Docs
    to Markdown/MDX files for static site generation.
    """

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or GDRIVE_CMS_PATH / "config.json"
        self.gdrive_path = GDRIVE_CMS_PATH

        if not self.gdrive_path.exists():
            raise FileNotFoundError(f"gdrive-cms not found at {self.gdrive_path}")

    def sync(self, deploy: bool = False) -> SyncResult:
        """
        Sync content from Google Drive.

        Args:
            deploy: If True, run deploy command after sync

        Returns:
            SyncResult with sync details
        """
        cmd = ["python3", "sync.py", str(self.config_path)]
        if deploy:
            cmd.append("--deploy")

        try:
            result = subprocess.run(
                cmd,
                cwd=self.gdrive_path,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )

            if result.returncode == 0:
                # Parse output for synced files
                synced_files = [
                    line.split("→")[0].strip()
                    for line in result.stdout.split("\n")
                    if "→" in line
                ]
                return SyncResult(
                    success=True,
                    synced_count=len(synced_files),
                    errors=[],
                    files=synced_files
                )
            else:
                return SyncResult(
                    success=False,
                    synced_count=0,
                    errors=[result.stderr],
                    files=[]
                )

        except subprocess.TimeoutExpired:
            return SyncResult(
                success=False,
                synced_count=0,
                errors=["Sync timed out after 5 minutes"],
                files=[]
            )
        except Exception as e:
            return SyncResult(
                success=False,
                synced_count=0,
                errors=[str(e)],
                files=[]
            )

    def get_config(self) -> Dict:
        """Load current config"""
        if self.config_path.exists():
            with open(self.config_path) as f:
                return json.load(f)
        return {}

    def update_config(self, **updates) -> Dict:
        """Update config values"""
        config = self.get_config()
        for key, value in updates.items():
            if "." in key:
                # Handle nested keys like "drive.folder_id"
                parts = key.split(".")
                current = config
                for part in parts[:-1]:
                    current = current.setdefault(part, {})
                current[parts[-1]] = value
            else:
                config[key] = value

        with open(self.config_path, "w") as f:
            json.dump(config, f, indent=2)

        return config

    def is_authenticated(self) -> bool:
        """Check if Google auth token exists and is valid"""
        token_path = self.gdrive_path / "token.json"
        if not token_path.exists():
            return False

        try:
            with open(token_path) as f:
                token = json.load(f)
                return "access_token" in token or "token" in token
        except:
            return False

    def authenticate(self) -> bool:
        """
        Run authentication flow.

        Note: This may open a browser for OAuth.
        """
        try:
            result = subprocess.run(
                ["python3", "gdrive_auth.py", str(self.config_path)],
                cwd=self.gdrive_path,
                capture_output=True,
                text=True,
                timeout=120
            )
            return result.returncode == 0
        except:
            return False

    def watch(self, interval: int = 300) -> subprocess.Popen:
        """
        Start watching for changes.

        Args:
            interval: Poll interval in seconds

        Returns:
            Popen process handle
        """
        return subprocess.Popen(
            ["python3", "sync.py", str(self.config_path), "--watch", "--interval", str(interval)],
            cwd=self.gdrive_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )


def get_gdrive_cms(config_path: Optional[Path] = None) -> Optional[GDriveCMS]:
    """Factory function to get GDriveCMS instance"""
    try:
        return GDriveCMS(config_path)
    except FileNotFoundError:
        logger.warning("gdrive-cms not installed")
        return None
