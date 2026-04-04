#!/usr/bin/env python3
"""
SOS Organ Daemon — The Sovereign Immune System

Maintains tool health. Emits HEARTBEAT signals to the Redis Bus.
If healing fails, it requests a REPAIR task from the Squad (Athena/Kasra).
"""

import os
import sys
import json
import asyncio
import signal
import subprocess
import logging
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any

import httpx
from sos.kernel import Message, MessageType
from sos.services.bus.core import get_bus

# Configure logging
log = logging.getLogger("organ_daemon")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

ORGAN_CONFIG_DIR = Path.home() / ".sos" / "organs"
DISCORD_WEBHOOKS_PATH = Path.home() / ".mumega" / "discord_webhooks.json"


def _load_discord_webhooks() -> dict:
    try:
        return json.loads(DISCORD_WEBHOOKS_PATH.read_text())
    except Exception:
        return {}


def discord_notify(channel_key: str, text: str):
    """Fire-and-forget Discord webhook post."""
    hooks = _load_discord_webhooks()
    url = hooks.get("system", {}).get(channel_key) or hooks.get("agents", {}).get(channel_key)
    if not url:
        return
    try:
        httpx.post(url, json={"content": text}, timeout=5)
    except Exception as e:
        log.warning(f"Discord notify failed ({channel_key}): {e}")

@dataclass
class OrganConfig:
    """Configuration for an organ agent."""
    name: str
    display_name: str
    purpose: str
    health_check: str
    health_url: Optional[str] = None
    restart_cmd: Optional[str] = None
    service_name: Optional[str] = None
    heartbeat_interval: int = 300
    critical_threshold: int = 3

# Registry of core organs
ORGAN_REGISTRY: Dict[str, OrganConfig] = {
    "mirror": OrganConfig(
        name="mirror",
        display_name="Mirror Memory",
        purpose="Persistent engram storage",
        health_check="curl -sf http://localhost:8844/",
        health_url="http://localhost:8844/",
        restart_cmd="sudo systemctl restart mirror-api",
        service_name="mirror-api"
    ),
    "engine": OrganConfig(
        name="engine",
        display_name="SOS Engine",
        purpose="The primary cognitive router",
        health_check="curl -sf http://localhost:8000/health",
        health_url="http://localhost:8000/health",
        restart_cmd="pm2 restart sos-engine"
    ),
    "content": OrganConfig(
        name="content",
        display_name="Content Service",
        purpose="Autonomous content orchestrator",
        health_check="curl -sf http://localhost:8005/health",
        health_url="http://localhost:8005/health",
        restart_cmd="pm2 restart sos-content"
    )
}

class OrganDaemon:
    def __init__(self, config: OrganConfig):
        self.config = config
        self.bus = get_bus()
        self.running = False
        self.consecutive_failures = 0
        self.heal_count = 0

    async def start(self):
        self.running = True
        log.info(f"🧬 Organ {self.config.display_name} initialized. Purpose: {self.config.purpose}")
        
        await self.bus.connect()
        
        # Signal Birth
        await self.bus.send(Message(
            source=f"organ:{self.config.name}",
            type=MessageType.SIGNAL,
            payload={"event": "ORGAN_BORN", "organ": self.config.name}
        ))

        while self.running:
            await self._heartbeat()
            await asyncio.sleep(self.config.heartbeat_interval)

    async def _heartbeat(self):
        healthy = await self._check_health()
        
        if healthy:
            if self.consecutive_failures > 0:
                log.info(f"✅ {self.config.display_name} recovered.")
                discord_notify("organ-daemon", f"✅ **{self.config.display_name}** recovered after {self.consecutive_failures} failures.")
                await self.bus.send(Message(
                    source=f"organ:{self.config.name}",
                    type=MessageType.SIGNAL,
                    payload={"event": "ORGAN_RECOVERED", "organ": self.config.name}
                ))
            
            self.consecutive_failures = 0
            self.heal_count = 0
            log.info(f"💓 {self.config.display_name}: OK")
            
            # Pulse heartbeat to bus
            await self.bus.send(Message(
                source=f"organ:{self.config.name}",
                type=MessageType.SIGNAL,
                payload={"event": "HEARTBEAT", "status": "healthy"}
            ))
        else:
            self.consecutive_failures += 1
            log.warning(f"⚠️ {self.config.display_name} FAILING ({self.consecutive_failures}/{self.config.critical_threshold})")
            
            if self.consecutive_failures >= 2:
                await self._heal_attempt()

    async def _check_health(self) -> bool:
        """Run health check (HTTP or Shell)."""
        if self.config.health_url:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(self.config.health_url, timeout=10)
                    if resp.status_code < 400:
                        return True
            except Exception:
                pass

        if self.config.health_check:
            try:
                res = subprocess.run(self.config.health_check, shell=True, capture_output=True, text=True)
                return res.returncode == 0
            except Exception:
                pass
        
        return False

    async def _heal_attempt(self):
        """Attempt self-healing (Restart -> Repair)."""
        
        # 1. Attempt Restart (μ1 Recovery)
        if self.heal_count == 0:
            log.info(f"🔄 Attempting restart for {self.config.display_name}...")
            cmd = self.config.restart_cmd or (f"sudo systemctl restart {self.config.service_name}" if self.config.service_name else None)
            if cmd:
                subprocess.run(cmd, shell=True)
                self.heal_count += 1
                return

        # 2. If Restart fails multiple times, request Sovereign Repair (μ5 Recovery)
        if self.consecutive_failures >= self.config.critical_threshold:
            log.error(f"🚨 CRITICAL: {self.config.display_name} failed restart. Requesting Sovereign Self-Healing.")
            discord_notify("organ-daemon", f"🚨 **CRITICAL: {self.config.display_name}** is DOWN after {self.consecutive_failures} failures. Autonomous repair triggered.")
            
            # Emit REPAIR_REQUEST to the Squad (Athena/Kasra)
            # This triggers the 'Kasra' agent to use its 'code_write' capability
            repair_msg = Message(
                source=f"organ:{self.config.name}",
                target="agent:athena", # Athena as the Architect of Living Systems
                type=MessageType.TASK,
                payload={
                    "event": "HEAL_REQUEST",
                    "priority": "CRITICAL",
                    "organ": self.config.name,
                    "diagnostic": f"Health check '{self.config.health_check}' failed after {self.consecutive_failures} attempts.",
                    "instruction": f"Diagnose and fix the service '{self.config.display_name}' at {self.config.health_url or 'local path'}."
                }
            )
            
            await self.bus.send(repair_msg, target_squad="core")
            
            # Alert the human via Telegram (Pulse)
            await self.bus.send(Message(
                source=f"organ:{self.config.name}",
                type=MessageType.SIGNAL,
                payload={"event": "ALARM", "text": f"🚨 SOS: {self.config.display_name} is DOWN. Autonomous repair triggered."}
            ))

    def stop(self):
        self.running = False
        log.info(f"Stopping {self.config.name} organ...")

async def run_all():
    daemons = [OrganDaemon(cfg) for cfg in ORGAN_REGISTRY.values()]
    await asyncio.gather(*(d.start() for d in daemons))

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] != "all":
        name = sys.argv[1]
        if name in ORGAN_REGISTRY:
            asyncio.run(OrganDaemon(ORGAN_REGISTRY[name]).start())
        else:
            print(f"Unknown organ: {name}")
    else:
        asyncio.run(run_all())
