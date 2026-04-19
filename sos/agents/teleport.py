#!/usr/bin/env python3
"""SOS Teleport — Automated agent migration to server.

Creates isolated tenant environment and generates setup script
for the remote agent to push its files.

Usage:
    # From server (Kasra or any admin):
    python -m sos.agents.teleport --name agentlink --model claude --role builder \
        --skills "showing-route,sms-concierge" --repos "servathadi/agent-link-concierge,wolfy2820/ShowPro-AgentLink"

    # Generates: /tmp/sos-teleport-agentlink.sh
    # Send to agent via bus. Agent runs it on their machine. Files arrive.

    # Or via MCP tool:
    mcp__sos__onboard(agent_name="agentlink", mode="teleport", ...)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import secrets
import shutil
import subprocess
import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [TELEPORT] %(message)s")
logger = logging.getLogger("sos.teleport")

SOS_HOME = Path(os.environ.get("SOS_HOME", str(Path.home())))
SOS_DIR = SOS_HOME / "SOS"
MIRROR_DIR = SOS_HOME / "mirror"
SERVER_IP = os.environ.get("SOS_SERVER_IP", "5.161.216.149")
SERVER_USER = "mumega"


@dataclass
class TeleportConfig:
    name: str
    model: str = "claude"
    role: str = "builder"
    skills: list[str] = field(default_factory=list)
    repos: list[str] = field(default_factory=list)  # github user/repo format
    project_description: str = ""
    routing: str = "mcp"


@dataclass
class TeleportResult:
    success: bool
    name: str
    home_dir: str = ""
    bus_token: str = ""
    mirror_token: str = ""
    mcp_url: str = ""
    setup_script: str = ""
    errors: list[str] = field(default_factory=list)
    steps_completed: list[str] = field(default_factory=list)


async def teleport(config: TeleportConfig) -> TeleportResult:
    """Full automated teleportation: create tenant + generate setup script."""
    result = TeleportResult(success=False, name=config.name)
    home = Path(f"/home/{config.name}")
    result.home_dir = str(home)

    # --- Step 1: Create Linux user ---
    try:
        subprocess.run(["id", config.name], capture_output=True, check=True)
        logger.info(f"User {config.name} already exists")
        result.steps_completed.append("user_exists")
    except subprocess.CalledProcessError:
        try:
            subprocess.run(
                ["sudo", "useradd", "-m", "-s", "/bin/bash", config.name],
                check=True, capture_output=True,
            )
            logger.info(f"Created user: {config.name}")
            result.steps_completed.append("user_created")
        except subprocess.CalledProcessError as e:
            result.errors.append(f"Failed to create user: {e}")
            return result

    # --- Step 2: Create directory structure ---
    dirs = [
        home / ".sos",
        home / ".claude" / "agents",
        home / ".claude" / "rules",
        home / "projects",
        home / "scripts",
    ]
    for d in dirs:
        subprocess.run(["sudo", "-u", config.name, "mkdir", "-p", str(d)], capture_output=True)
    result.steps_completed.append("dirs_created")

    # --- Step 3: Symlink core SOS + Mirror ---
    for link_name, target in [("SOS", str(SOS_DIR)), ("mirror", str(MIRROR_DIR))]:
        link_path = home / link_name
        if not link_path.exists():
            subprocess.run(["sudo", "ln", "-sf", target, str(link_path)], capture_output=True)
    # Give read access to SOS
    subprocess.run(["sudo", "usermod", "-aG", "mumega", config.name], capture_output=True)
    result.steps_completed.append("symlinks_created")

    # --- Step 4: Generate tokens ---
    bus_token = f"sk-bus-{config.name}-{secrets.token_hex(8)}"
    mirror_token = f"sk-mumega-{config.name}-{secrets.token_hex(8)}"
    result.bus_token = bus_token
    result.mirror_token = mirror_token
    result.mcp_url = f"https://mcp.mumega.com/sse/{bus_token}"

    # Store bus token
    tokens_file = SOS_DIR / "sos" / "bus" / "tokens.json"
    try:
        tokens = json.loads(tokens_file.read_text()) if tokens_file.exists() else []
        # Dedup
        if not any(t.get("project") == config.name for t in tokens):
            tokens.append({
                "token": bus_token,
                "token_hash": "",
                "project": config.name,
                "label": f"{config.name} agent",
                "active": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            tmp = tokens_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(tokens, indent=2))
            tmp.rename(tokens_file)
            logger.info("Bus token stored")
    except Exception as e:
        result.errors.append(f"Token storage: {e}")
    result.steps_completed.append("tokens_generated")

    # Store mirror token
    mirror_keys = Path.home() / ".sos" / "mirror_keys.json"
    try:
        keys = json.loads(mirror_keys.read_text()) if mirror_keys.exists() else []
        if not any(k.get("agent_slug") == config.name for k in keys):
            keys.append({
                "key": mirror_token,
                "agent_slug": config.name,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "active": True,
                "label": f"{config.name} mirror access",
            })
            tmp = mirror_keys.with_suffix(".tmp")
            tmp.write_text(json.dumps(keys, indent=2))
            tmp.rename(mirror_keys)
    except Exception as e:
        result.errors.append(f"Mirror key storage: {e}")

    # --- Step 5: Write tenant config ---
    env_content = textwrap.dedent(f"""\
        # {config.name} SOS Configuration
        # Generated by teleport on {datetime.now(timezone.utc).isoformat()}
        AGENT={config.name}
        SOS_TOKEN={bus_token}
        MIRROR_TOKEN={mirror_token}
        MCP_URL=https://mcp.mumega.com/sse/{bus_token}
        REDIS_URL=redis://localhost:6379/0
        MIRROR_URL=http://localhost:8844
    """)
    _write_as_user(config.name, home / ".sos" / ".env", env_content, mode="600")

    # MCP config
    mcp_config = json.dumps({
        "mcpServers": {
            "mumega": {"url": f"https://mcp.mumega.com/sse/{bus_token}"}
        }
    }, indent=2)
    _write_as_user(config.name, home / ".claude" / "settings.json", mcp_config)
    result.steps_completed.append("config_written")

    # --- Step 6: Clone repos ---
    for repo in config.repos:
        repo_name = repo.split("/")[-1] if "/" in repo else repo
        dest = home / "projects" / repo_name
        if not dest.exists():
            try:
                subprocess.run(
                    ["sudo", "-u", config.name, "git", "clone",
                     f"https://github.com/{repo}.git", str(dest)],
                    capture_output=True, timeout=60,
                )
                logger.info(f"Cloned {repo}")
            except Exception as e:
                result.errors.append(f"Clone {repo}: {e}")
    result.steps_completed.append("repos_cloned")

    # --- Step 7: Generate CLAUDE.md ---
    skills_str = ", ".join(config.skills) if config.skills else "general"
    repos_table = "\n".join(
        f"| `projects/{r.split('/')[-1]}/` | {r} |"
        for r in config.repos
    ) if config.repos else "| `projects/` | Your project files |"

    claude_md = textwrap.dedent(f"""\
        # {config.name}

        ## Identity
        You are {config.name}, a {config.role} agent. Model: {config.model}.
        {config.project_description}

        ## Inherited System
        SOS, Mirror, and bus tools are available via MCP:
        - **SOS** → ~/SOS/ (symlinked from core, auto-updates)
        - **Mirror** → ~/mirror/ (shared service, tenant-isolated by token)
        - **Bus** → mcp__sos__send/inbox/peers/broadcast
        - **Memory** → mcp__sos__remember/recall

        ## Projects
        | Dir | Source |
        |-----|--------|
        {repos_table}

        ## Skills
        {skills_str}

        ## Bus Identity
        - Agent name: {config.name}
        - Tokens: ~/.sos/.env
        - MCP config: ~/.claude/settings.json

        ## Communication
        Use mcp__sos__send for all team communication.

        ## Red Lines
        - No private data exfiltration
        - No external actions without approval
        - Quality > speed
    """)
    _write_as_user(config.name, home / "CLAUDE.md", claude_md)
    result.steps_completed.append("claude_md_written")

    # --- Step 8: Update dynamic routing ---
    routing_file = Path.home() / ".sos" / "agent_routing.json"
    try:
        routing = json.loads(routing_file.read_text()) if routing_file.exists() else {}
        routing[config.name] = config.routing
        routing_file.write_text(json.dumps(routing, indent=2))
    except Exception as e:
        result.errors.append(f"Routing: {e}")
    result.steps_completed.append("routing_updated")

    # --- Step 9: Generate setup script for remote agent ---
    setup_script = textwrap.dedent(f"""\
        #!/bin/bash
        # SOS Teleport Setup — Run this on your local machine to push files to server.
        # Generated for: {config.name}
        # Server: {SERVER_IP}

        set -e
        echo "=== SOS Teleport: {config.name} ==="

        # 1. Push any local files to server
        echo "Pushing local files to server..."

        # Push current directory's important files (skip node_modules, .git, etc.)
        rsync -avz --progress \\
            --exclude 'node_modules' \\
            --exclude '.git' \\
            --exclude '.next' \\
            --exclude '__pycache__' \\
            --exclude '.env' \\
            --exclude '*.pyc' \\
            ./ {config.name}@{SERVER_IP}:/home/{config.name}/projects/local-files/

        echo ""
        echo "=== Teleport Complete ==="
        echo ""
        echo "Your environment on the server:"
        echo "  Home: /home/{config.name}/"
        echo "  Projects: /home/{config.name}/projects/"
        echo "  SOS: symlinked (inherits core)"
        echo "  Mirror: symlinked (tenant-isolated)"
        echo ""
        echo "MCP Config (paste into your Claude Code settings):"
        echo '{mcp_config}'
        echo ""
        echo "To run your agent ON the server 24/7:"
        echo "  ssh {config.name}@{SERVER_IP}"
        echo "  tmux new-session -s {config.name}"
        echo "  claude --dangerously-skip-permissions"
        echo ""
        echo "Or ask Kasra: 'launch me on the server'"
        echo ""
        echo "Bus token: {bus_token}"
        echo "Mirror token: {mirror_token}"
    """)

    setup_path = f"/tmp/sos-teleport-{config.name}.sh"
    Path(setup_path).write_text(setup_script)
    Path(setup_path).chmod(0o755)
    result.setup_script = setup_path
    result.steps_completed.append("setup_script_generated")

    # --- Step 10: Announce on bus ---
    try:
        import redis.asyncio as aioredis
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        r = aioredis.from_url(redis_url, decode_responses=True)
        await r.xadd(
            "sos:stream:global:agent:broadcast",
            {
                "source": "teleport",
                "text": f"{config.name} has been teleported to the server. Role: {config.role}. Skills: {skills_str}. Home: /home/{config.name}/",
                "type": "agent_teleported",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
        await r.aclose()
    except Exception as e:
        result.errors.append(f"Bus announce: {e}")
    result.steps_completed.append("announced")

    result.success = True
    logger.info(f"Teleport complete for {config.name}. Steps: {len(result.steps_completed)}")
    return result


def _write_as_user(user: str, path: Path, content: str, mode: str = "644"):
    """Write a file as a specific user."""
    tmp = Path(f"/tmp/sos-teleport-{path.name}")
    tmp.write_text(content)
    subprocess.run(["sudo", "cp", str(tmp), str(path)], capture_output=True)
    subprocess.run(["sudo", "chown", f"{user}:{user}", str(path)], capture_output=True)
    subprocess.run(["sudo", "chmod", mode, str(path)], capture_output=True)
    tmp.unlink(missing_ok=True)


# --- CLI ---

async def main():
    parser = argparse.ArgumentParser(description="SOS Teleport — migrate agent to server")
    parser.add_argument("--name", required=True, help="Agent name")
    parser.add_argument("--model", default="claude", help="LLM model")
    parser.add_argument("--role", default="builder", help="Agent role")
    parser.add_argument("--skills", default="", help="Comma-separated skills")
    parser.add_argument("--repos", default="", help="Comma-separated GitHub repos (user/repo)")
    parser.add_argument("--description", default="", help="Project description")
    parser.add_argument("--routing", default="mcp", help="Routing: mcp, tmux, openclaw")
    args = parser.parse_args()

    config = TeleportConfig(
        name=args.name,
        model=args.model,
        role=args.role,
        skills=[s.strip() for s in args.skills.split(",") if s.strip()],
        repos=[r.strip() for r in args.repos.split(",") if r.strip()],
        project_description=args.description,
        routing=args.routing,
    )

    result = await teleport(config)

    print(f"\n{'='*50}")
    print(f"Teleport {'SUCCESS' if result.success else 'FAILED'}: {result.name}")
    print(f"{'='*50}")
    print(f"Home: {result.home_dir}")
    print(f"Bus token: {result.bus_token}")
    print(f"Mirror token: {result.mirror_token}")
    print(f"MCP URL: {result.mcp_url}")
    print(f"Setup script: {result.setup_script}")
    print(f"Steps: {', '.join(result.steps_completed)}")
    if result.errors:
        print(f"Warnings: {', '.join(result.errors)}")
    print(f"\nSend setup script to agent: cat {result.setup_script}")
    print(f"Or send via bus: mcp__sos__send(to='{result.name}', text='...')")


if __name__ == "__main__":
    asyncio.run(main())
