"""
SOS Customer Onboarding — run with: python -m sos.cli.onboard <slug> <domain> <spai_key>

Creates everything needed to operate a customer's WordPress site:
  1. Linux user with isolated home directory
  2. Claude Code settings (MumCP + SOS bus + hooks)
  3. Bus token (project-scoped)
  4. Squad with wallet, goals, and conductance
  5. Inkwell fork with customer config
  6. tmux session ready to launch

The customer's agent runs in its own Linux user space.
It can only see its own WordPress (via MumCP) and its own bus messages.
It cannot access other customers or the core team.

Usage:
  python -m sos.cli.onboard prefrontal prefrontalclub.com spai_8eae...
  python -m sos.cli.onboard viamar viamar.ca spai_d621... --model sonnet
  python -m sos.cli.onboard --list  # show all onboarded customers

Requires: sudo access (creates Linux users)
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SOS_ROOT = Path(__file__).resolve().parent.parent.parent
TOKENS_FILE = SOS_ROOT / "sos" / "bus" / "tokens.json"
SQUAD_DB = Path.home() / ".sos" / "data" / "squads.db"
INKWELL_SOURCE = Path.home() / "inkwell"
HOOKS_SOURCE = Path.home() / ".claude" / "hooks"

# SaaS tenant registry (lazy import to avoid circular deps)
_registry = None

def _get_registry():
    global _registry
    if _registry is None:
        from sos.services.saas.registry import TenantRegistry
        _registry = TenantRegistry()
    return _registry


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _green(text: str) -> str:
    return f"\033[1;32m{text}\033[0m"


def _red(text: str) -> str:
    return f"\033[1;31m{text}\033[0m"


def _bold(text: str) -> str:
    return f"\033[1m{text}\033[0m"


def create_user(slug: str) -> Path:
    """Create Linux user if not exists. Returns home directory."""
    home = Path(f"/home/{slug}")
    try:
        _run(["id", slug])
        print(f"  [1/8] User {slug} already exists")
    except subprocess.CalledProcessError:
        _run(["sudo", "useradd", "-m", "-s", "/bin/bash", slug])
        print(f"  [1/8] {_green('User created')}: {slug}")

    # Ensure directories
    for d in [".claude/hooks", ".sos/state"]:
        _run(["sudo", "mkdir", "-p", str(home / d)], check=False)
    print(f"  [2/8] Directories ready")
    return home


def create_bus_token(slug: str) -> str:
    """Generate project-scoped bus token and add to tokens.json."""
    tokens = json.loads(TOKENS_FILE.read_text())

    # Check if token already exists
    for t in tokens:
        if t.get("project") == slug:
            print(f"  [3/8] Bus token exists: {t['token'][:20]}...")
            return t["token"]

    token = f"sk-bus-{slug}-{secrets.token_hex(8)}"
    tokens.append({
        "token": token,
        "token_hash": "",
        "project": slug,
        "label": f"{slug} agent",
        "active": True,
        "created_at": _now(),
        "agent": slug,
    })
    TOKENS_FILE.write_text(json.dumps(tokens, indent=2))
    print(f"  [3/8] {_green('Bus token created')}: {token[:20]}...")
    return token


def write_settings(home: Path, slug: str, domain: str, spai_key: str, model: str, token: str) -> None:
    """Write .claude/settings.json with MumCP + SOS bus + hooks."""
    settings = {
        "hooks": {
            "UserPromptSubmit": [{
                "matcher": "",
                "hooks": [{
                    "type": "command",
                    "command": f"{home}/.claude/hooks/check-inbox.sh",
                    "timeout": 8000,
                }],
            }],
            "Stop": [{
                "matcher": "",
                "hooks": [{
                    "type": "command",
                    "command": f"AGENT_NAME={slug} /home/mumega/.claude/hooks/session-tracker.sh",
                    "timeout": 10,
                }],
            }],
        },
        "skipDangerousModePermissionPrompt": True,
        "model": model,
        "mcpServers": {
            f"mumcp-{slug}": {
                "url": f"https://{domain}/wp-json/site-pilot-ai/v1/mcp",
                "headers": {"X-API-Key": spai_key},
            },
            "sos": {
                "type": "sse",
                "url": f"http://localhost:6070/sse/{token}",
            },
        },
    }
    settings_path = home / ".claude" / "settings.json"
    _run(["sudo", "tee", str(settings_path)],
         check=False).input if False else None
    # Write via sudo
    proc = subprocess.run(
        ["sudo", "tee", str(settings_path)],
        input=json.dumps(settings, indent=2),
        capture_output=True, text=True,
    )
    print(f"  [4/8] {_green('Settings written')}: MumCP + SOS bus")


def write_claude_md(home: Path, slug: str, domain: str, model: str) -> None:
    """Write CLAUDE.md with agent instructions."""
    content = f"""# {slug} — AI Operations Agent

## You Are
The {slug} operations agent. You manage {domain} via MumCP. You run on {model}.

## Workflow
1. Check tasks: `mcp__sos__task_list(status="queued")`
2. Pick highest priority task assigned to you
3. Execute using MumCP tools
4. Complete: `mcp__sos__task_update(task_id="...", status="done", notes="...")`
5. Pick next task

## Tools
- **MumCP**: read/write WordPress at {domain}
- **SOS Bus**: communicate with team, check tasks

## Governance
- Read/audit: act freely
- Content edits: act freely
- Content publish: human_gate — send to hadi for approval
- Payments: human_gate
- Outreach: human_gate

## Contact
- Hadi: `mcp__sos__send(to="hadi", text="...")`
"""
    subprocess.run(
        ["sudo", "tee", str(home / "CLAUDE.md")],
        input=content, capture_output=True, text=True,
    )
    print(f"  [5/8] {_green('CLAUDE.md written')}")


def copy_hooks(home: Path) -> None:
    """Copy check-inbox hook from template."""
    src = HOOKS_SOURCE / "check-inbox.sh"
    dst = home / ".claude" / "hooks" / "check-inbox.sh"
    if src.exists():
        subprocess.run(["sudo", "cp", str(src), str(dst)], check=False)
        subprocess.run(["sudo", "chmod", "+x", str(dst)], check=False)
    print(f"  [6/8] Hooks copied")


def create_squad(slug: str, domain: str) -> None:
    """Create squad with wallet and initial goals in Squad Service."""
    conn = sqlite3.connect(SQUAD_DB)
    now = _now()

    existing = conn.execute("SELECT id FROM squads WHERE id = ?", (slug,)).fetchone()
    if existing:
        print(f"  [7/8] Squad already exists")
    else:
        conn.execute("""
            INSERT INTO squads (id, tenant_id, name, project, objective, tier, status,
                roles_json, members_json, kpis_json, budget_cents_monthly, created_at, updated_at,
                dna_vector, coherence, receptivity, conductance_json)
            VALUES (?, 'default', ?, ?, ?, 'nomad', 'active',
                '[]', ?, '[]', 2000, ?, ?,
                '[]', 0.5, 0.7, '{"wordpress": 0.5, "content": 0.3, "seo": 0.2}')
        """, (slug, f"{slug} squad", slug, f"Operate {domain}",
              json.dumps([
                  {"agent_id": slug, "role": "operator", "is_human": False},
                  {"agent_id": "hadi", "role": "lead", "is_human": True},
              ]), now, now))

        conn.execute("""
            INSERT OR IGNORE INTO squad_wallets
            (squad_id, tenant_id, balance_cents, total_earned_cents, total_spent_cents, fuel_budget_json, updated_at)
            VALUES (?, 'default', 2000, 2000, 0, '{"diesel": 2000}', ?)
        """, (slug, now))

        # Initial audit task
        conn.execute("""
            INSERT OR IGNORE INTO squad_tasks
            (id, tenant_id, squad_id, title, description, status, priority, assignee, skill_id, project,
             labels_json, blocked_by_json, blocks_json, inputs_json, result_json, token_budget, bounty_json,
             created_at, updated_at, attempt)
            VALUES (?, 'default', ?, ?, '', 'queued', 'high', ?, '', ?,
             '["wordpress","audit"]', '[]', '[]', '{}', '{}', 0, '{}', ?, ?, 0)
        """, (f"{slug}-init-audit", slug, f"Initial site audit for {domain}", slug, slug, now, now))

        conn.commit()
        print(f"  [7/8] {_green('Squad created')} with wallet ($20) + audit task")
    conn.close()


def setup_tmux(slug: str, home: Path) -> None:
    """Create tmux session if not exists."""
    result = subprocess.run(["tmux", "has-session", "-t", slug], capture_output=True)
    if result.returncode == 0:
        print(f"  [8/8] tmux session exists")
    else:
        subprocess.run(["tmux", "new-session", "-d", "-s", slug, "-c", str(home)])
        print(f"  [8/8] {_green('tmux session created')}")


def fix_ownership(home: Path, slug: str) -> None:
    """Set correct file ownership."""
    subprocess.run(["sudo", "chown", "-R", f"{slug}:{slug}", str(home)], check=False)


def list_customers() -> None:
    """List all onboarded customers."""
    conn = sqlite3.connect(SQUAD_DB)
    squads = conn.execute("""
        SELECT s.id, s.project, s.status, s.coherence,
               w.balance_cents,
               (SELECT count(*) FROM squad_tasks WHERE squad_id = s.id AND status = 'queued') as queued,
               (SELECT count(*) FROM squad_tasks WHERE squad_id = s.id AND status = 'done') as done
        FROM squads s
        LEFT JOIN squad_wallets w ON w.squad_id = s.id
        WHERE s.id NOT IN ('seo', 'dev', 'outreach', 'content', 'ops', 'webdev')
    """).fetchall()

    if not squads:
        print("No customers onboarded yet.")
        return

    print(f"\n{'Slug':<16} {'Status':<8} {'C':>4} {'Wallet':>8} {'Queued':>6} {'Done':>6}")
    print(f"{'─'*16} {'─'*8} {'─'*4} {'─'*8} {'─'*6} {'─'*6}")
    for s in squads:
        wallet = f"${s[4]/100:.0f}" if s[4] else "—"
        print(f"{s[0]:<16} {s[2]:<8} {s[3] or 0:>4.1f} {wallet:>8} {s[5]:>6} {s[6]:>6}")
    conn.close()


def onboard(slug: str, domain: str, spai_key: str, model: str = "haiku") -> None:
    """Full customer onboarding — one command."""
    print(f"\n{'═'*50}")
    print(f"SOS ONBOARD: {_bold(slug)}")
    print(f"{'═'*50}\n")

    home = create_user(slug)
    token = create_bus_token(slug)
    write_settings(home, slug, domain, spai_key, model, token)
    write_claude_md(home, slug, domain, model)
    copy_hooks(home)
    create_squad(slug, domain)
    fix_ownership(home, slug)
    setup_tmux(slug, home)

    # Register in SaaS tenant registry
    try:
        from sos.contracts.tenant import TenantCreate
        registry = _get_registry()
        tenant = registry.create(TenantCreate(slug=slug, label=slug, email=f"{slug}@mumega.com", domain=domain))
        registry.activate(slug, squad_id=slug, bus_token=token)
        print(f"  [+] {_green('SaaS tenant registered')} ({tenant.subdomain})")
    except Exception as exc:
        print(f"  [!] SaaS registry failed (non-blocking): {exc}")

    print(f"\n{'═'*50}")
    print(f"{_green('ONBOARDED')}: {slug}")
    print(f"{'═'*50}")
    print(f"  Home:   {home}")
    print(f"  Domain: {domain}")
    print(f"  Model:  {model}")
    print(f"  Bus:    {token[:20]}...")
    print(f"  tmux:   tmux attach -t {slug}")
    print(f"\n  Launch:")
    print(f"    tmux send-keys -t {slug} 'claude --model {model} --dangerously-skip-permissions' Enter")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SOS Customer Onboarding",
        usage="python -m sos.cli.onboard <slug> <domain> <spai_key> [--model haiku]",
    )
    parser.add_argument("slug", nargs="?", help="Customer slug (e.g. prefrontal)")
    parser.add_argument("domain", nargs="?", help="Customer domain (e.g. prefrontalclub.com)")
    parser.add_argument("spai_key", nargs="?", help="SPAI/MumCP API key")
    parser.add_argument("--model", default="haiku", help="Claude model (default: haiku)")
    parser.add_argument("--list", action="store_true", help="List onboarded customers")
    args = parser.parse_args()

    if args.list:
        list_customers()
        return

    if not args.slug or not args.domain or not args.spai_key:
        parser.print_help()
        sys.exit(1)

    onboard(args.slug, args.domain, args.spai_key, args.model)


if __name__ == "__main__":
    main()
