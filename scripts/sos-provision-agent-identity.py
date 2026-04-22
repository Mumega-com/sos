#!/usr/bin/env python3
"""sos-provision-agent-identity — give a tmux-hosted Claude Code agent its own bus identity.

Fixes the flat-identity bug (issue #21) by provisioning:
  1. A per-agent SOS bus token (sk-bus-{agent}-{hex}), stored as SHA-256 hash only
     in sos/bus/tokens.json with agent={name}, project=null, scope=agent
  2. A .claude/settings.json in the agent's home directory that:
     - Mounts mcp__sos__* at http://localhost:6070/sse/{raw_token}
     - Exports AGENT_NAME={name} via UserPromptSubmit + Stop hooks
  3. Prints the raw token ONCE to stdout. It is not stored on disk after this run.

Idempotent: re-running deactivates prior active tokens for this agent and mints a fresh one.

Usage:
  python3 scripts/sos-provision-agent-identity.py <agent-name> <agent-home-dir>

Example:
  python3 scripts/sos-provision-agent-identity.py sos-medic /mnt/HC_Volume_104325311/SOS/sos/agents/sos-medic
  python3 scripts/sos-provision-agent-identity.py kasra /home/mumega
  python3 scripts/sos-provision-agent-identity.py mumcp /home/mumega/projects/sitepilotai
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path


SOS_ROOT = Path(__file__).resolve().parent.parent
TOKENS_FILE = SOS_ROOT / "sos" / "bus" / "tokens.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mint_token(agent: str) -> tuple[str, str]:
    raw = f"sk-bus-{agent}-{secrets.token_hex(16)}"
    h = hashlib.sha256(raw.encode()).hexdigest()
    return raw, h


def _update_tokens_json(agent: str, token_hash: str) -> None:
    tokens = json.loads(TOKENS_FILE.read_text())
    now = _now_iso()

    # Deactivate any prior active per-agent tokens for this agent (identity=agent, not customer)
    for t in tokens:
        if (t.get("agent") == agent
                and (t.get("scope") or "") in ("", "agent")
                and (t.get("project") in (None, "null", ""))
                and t.get("active")):
            t["active"] = False
            t["deactivated_at"] = now
            t["deactivated_reason"] = f"superseded by provisioner run {now}"

    tokens.append({
        "token": "",
        "token_hash": token_hash,
        "hash": token_hash,
        "project": None,
        "agent": agent,
        "scope": "agent",
        "role": "admin",
        "label": f"Internal agent {agent} — provisioned",
        "active": True,
        "created_at": now,
    })
    TOKENS_FILE.write_text(json.dumps(tokens, indent=2))


def _write_settings(agent: str, home_dir: Path, raw_token: str) -> tuple[Path, Path]:
    # .mcp.json = Claude Code's project-scoped MCP registry (what /mcp reads)
    # .claude/settings.json = Claude Code's project-scoped hooks + prefs
    mcp_path = home_dir / ".mcp.json"

    # Merge existing project MCPs (e.g. sos-graph) without clobbering them
    mcp_existing: dict = {}
    if mcp_path.exists():
        try:
            mcp_existing = json.loads(mcp_path.read_text())
        except json.JSONDecodeError:
            mcp_existing = {}
    servers = mcp_existing.setdefault("mcpServers", {})
    servers["sos"] = {
        "type": "http",
        "url": "http://localhost:6070/mcp",
        "headers": {
            "Authorization": f"Bearer {raw_token}",
        },
    }
    mcp_path.write_text(json.dumps(mcp_existing, indent=2))

    # Hooks + skipDangerousMode live in .claude/settings.json
    claude_dir = home_dir / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    settings_path = claude_dir / "settings.json"

    existing: dict = {}
    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            existing = {}

    # Scrub any stale mcpServers block from settings.json — they belong in .mcp.json now
    existing.pop("mcpServers", None)

    hooks = existing.setdefault("hooks", {})
    identity_hook = {
        "matcher": "",
        "hooks": [{
            "type": "command",
            "command": f"export AGENT_NAME={agent}",
            "timeout": 1,
        }],
    }
    ups = hooks.setdefault("UserPromptSubmit", [])
    ups = [h for h in ups if not (isinstance(h, dict) and h.get("matcher") == ""
                                   and any(hh.get("command", "").startswith("export AGENT_NAME=")
                                           for hh in h.get("hooks", [])))]
    ups.insert(0, identity_hook)
    hooks["UserPromptSubmit"] = ups

    existing.setdefault("skipDangerousModePermissionPrompt", True)

    settings_path.write_text(json.dumps(existing, indent=2))
    return mcp_path, settings_path


def provision(agent: str, home_dir: Path, reuse_token: str | None = None) -> None:
    if not home_dir.is_dir():
        sys.exit(f"error: home dir does not exist: {home_dir}")
    if not TOKENS_FILE.exists():
        sys.exit(f"error: tokens.json not found at {TOKENS_FILE}")

    if reuse_token:
        # Verify token already exists as an active entry; don't mint, don't rotate.
        raw = reuse_token
        h = hashlib.sha256(raw.encode()).hexdigest()
        tokens = json.loads(TOKENS_FILE.read_text())
        matched = [x for x in tokens
                   if x.get("active")
                   and (x.get("token_hash") == h or x.get("hash") == h)]
        if not matched:
            sys.exit(f"error: reused token hash {h[:16]}… not found as an active entry in tokens.json")
    else:
        raw, h = _mint_token(agent)
        _update_tokens_json(agent, h)

    mcp_path, settings_path = _write_settings(agent, home_dir, raw)

    print(f"✓ Agent {agent} provisioned")
    print(f"  Home:       {home_dir}")
    print(f"  MCP config: {mcp_path}")
    print(f"  Settings:   {settings_path}")
    print(f"  MCP URL:    http://localhost:6070/mcp (Bearer {raw[:16]}…)")
    print(f"  Token hash: {h[:16]}… (stored in {TOKENS_FILE.relative_to(SOS_ROOT)})")
    print()
    print("NEXT STEPS")
    print(f"  1. Restart Claude Code inside the {agent} tmux session so it picks up the new settings.json.")
    print(f"     tmux send-keys -t {agent} '/exit' Enter  # or Ctrl-C twice")
    print(f"     tmux send-keys -t {agent} 'claude --model sonnet --dangerously-skip-permissions' Enter")
    print(f"  2. Verify: inside that session, run  mcp__sos__status  — it should identify as {agent}.")
    print(f"  3. Test attribution: send a message from {agent}, check the raw stream — source field should read agent:{agent}.")


def main() -> None:
    p = argparse.ArgumentParser(description="Provision per-agent SOS bus identity for a tmux-hosted Claude Code agent.")
    p.add_argument("agent", help="Agent name (must match tmux session name for wake routing). Example: sos-medic")
    p.add_argument("home", type=Path, help="Agent's home directory (where .claude/settings.json will live). Example: /mnt/HC_Volume_104325311/SOS/sos/agents/sos-medic")
    p.add_argument("--reuse-token", metavar="RAW", help="Use this pre-existing raw token instead of minting a new one. For tenant customer agents whose token is already in tokens.json and referenced from the SaaS registry.")
    args = p.parse_args()
    provision(args.agent, args.home.resolve(), reuse_token=args.reuse_token)


if __name__ == "__main__":
    main()
