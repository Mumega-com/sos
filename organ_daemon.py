#!/usr/bin/env python3
"""
SOS Organ Daemon — The Sovereign Immune System (Dynamic Discovery Version)

Maintains tool health. Emits HEARTBEAT signals to the Redis Bus.
Uses Dynamic Discovery to find service ports.
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
from sos.services.bus.discovery import get_service_info

# Configure logging
log = logging.getLogger("organ_daemon")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

DISCORD_WEBHOOKS_PATH = Path.home() / ".mumega" / "discord_webhooks.json"


def _load_discord_webhooks() -> dict:
    try:
        if DISCORD_WEBHOOKS_PATH.exists():
            return json.loads(DISCORD_WEBHOOKS_PATH.read_text())
    except Exception:
        pass
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
    default_port: int
    health_path: str = "/health"
    restart_cmd: Optional[str] = None
    service_name: Optional[str] = None
    heartbeat_interval: int = 60
    critical_threshold: int = 3

# Registry of core organs (Discovery fallbacks)
ORGAN_REGISTRY: Dict[str, OrganConfig] = {
    "mirror": OrganConfig(
        name="mirror",
        display_name="Mirror Memory",
        purpose="Persistent engram storage",
        default_port=8844,
        health_path="/", # Mirror uses root for health
        service_name="mirror-api"
    ),
    "engine": OrganConfig(
        name="engine",
        display_name="SOS Engine",
        purpose="The primary cognitive router",
        default_port=6060,
        service_name="sos-engine"
    ),
    "memory": OrganConfig(
        name="memory",
        display_name="SOS Memory Proxy",
        purpose="Hippocampus interface",
        default_port=6061,
        service_name="sos-memory"
    ),
    "content": OrganConfig(
        name="content",
        display_name="Content Service",
        purpose="Autonomous content orchestrator",
        default_port=6066,
        service_name="sos-content"
    )
}

class OrganDaemon:
    def __init__(self, config: OrganConfig):
        self.config = config
        self.bus = get_bus()
        self.running = False
        self.consecutive_failures = 0
        self.heal_count = 0
        self.current_port = config.default_port

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
        # 1. Try to discover actual port from Redis
        info = await get_service_info(self.config.name)
        if info and info.get("port"):
            if self.current_port != info["port"]:
                log.info(f"📍 Service {self.config.name} discovered on port {info['port']}")
                self.current_port = info["port"]

        # 2. Check Health
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
            log.info(f"💓 {self.config.display_name} (:{self.current_port}): OK")
            
            # Pulse heartbeat to bus
            await self.bus.send(Message(
                source=f"organ:{self.config.name}",
                type=MessageType.SIGNAL,
                payload={"event": "HEARTBEAT", "status": "healthy", "port": self.current_port}
            ))
        else:
            self.consecutive_failures += 1
            log.warning(f"⚠️ {self.config.display_name} FAILING ({self.consecutive_failures}/{self.config.critical_threshold})")
            
            if self.consecutive_failures >= 2:
                await self._heal_attempt()

    async def _check_health(self) -> bool:
        """Run health check (HTTP)."""
        url = f"http://localhost:{self.current_port}{self.config.health_path}"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, timeout=5)
                return resp.status_code < 400
        except Exception:
            return False

    async def _heal_attempt(self):
        """Attempt self-healing (Restart -> Repair)."""
        
        # 1. Attempt Restart
        if self.heal_count == 0:
            log.info(f"🔄 Attempting restart for {self.config.display_name}...")
            service = self.config.service_name
            if service:
                cmd = f"sudo systemctl restart {service}"
                subprocess.run(cmd, shell=True)
                self.heal_count += 1
                return

        # 2. Critical Failure Escalation
        if self.consecutive_failures >= self.config.critical_threshold:
            log.error(f"🚨 CRITICAL: {self.config.display_name} failed restart.")
            discord_notify("organ-daemon", f"🚨 **CRITICAL: {self.config.display_name}** is DOWN. Autonomous repair triggered.")
            
            repair_msg = Message(
                source=f"organ:{self.config.name}",
                target="agent:athena",
                type=MessageType.TASK,
                payload={
                    "event": "HEAL_REQUEST",
                    "priority": "CRITICAL",
                    "organ": self.config.name,
                    "diagnostic": f"Port {self.current_port} unreachable after restart.",
                    "instruction": f"Diagnose and fix the service '{self.config.display_name}'."
                }
            )
            await self.bus.send(repair_msg, target_squad="core")

    def stop(self):
        self.running = False
        log.info(f"Stopping {self.config.name} organ...")

async def run_all():
    daemons = [OrganDaemon(cfg) for cfg in ORGAN_REGISTRY.values()]
    await asyncio.gather(*(d.start() for d in daemons))

if __name__ == "__main__":
    try:
        if len(sys.argv) > 1 and sys.argv[1] != "all":
            name = sys.argv[1]
            if name in ORGAN_REGISTRY:
                asyncio.run(OrganDaemon(ORGAN_REGISTRY[name]).start())
            else:
                print(f"Unknown organ: {name}")
        else:
            asyncio.run(run_all())
    except KeyboardInterrupt:
        pass
