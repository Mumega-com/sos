#!/usr/bin/env python3
"""
SOS Self-Hosted Initialization CLI

Deploys SOS to customer's Cloudflare account.
Mumega keeps NOTHING. Local-first. Sovereign.

Usage:
    npx @mumega/sos init
    # or
    python -m sos.deploy.cli.init

Author: kasra_0111 | Mumega
"""

import os
import sys
import json
import subprocess
import uuid
from pathlib import Path
from typing import Optional


def run_cmd(cmd: list, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command and return result."""
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def check_wrangler() -> bool:
    """Check if wrangler CLI is installed."""
    try:
        result = run_cmd(["wrangler", "--version"], check=False)
        return result.returncode == 0
    except FileNotFoundError:
        return False


def create_d1_database(name: str = "sos-memory") -> Optional[str]:
    """Create D1 database and return ID."""
    print(f"Creating D1 database: {name}...")
    result = run_cmd(["wrangler", "d1", "create", name], check=False)

    if result.returncode != 0:
        if "already exists" in result.stderr:
            # Get existing database ID
            list_result = run_cmd(["wrangler", "d1", "list", "--json"], check=False)
            if list_result.returncode == 0:
                databases = json.loads(list_result.stdout)
                for db in databases:
                    if db.get("name") == name:
                        return db.get("uuid")
        print(f"Error creating database: {result.stderr}")
        return None

    # Parse database ID from output
    for line in result.stdout.split("\n"):
        if "database_id" in line:
            return line.split("=")[-1].strip().strip('"')

    return None


def create_kv_namespace(name: str = "sos-session") -> Optional[str]:
    """Create KV namespace and return ID."""
    print(f"Creating KV namespace: {name}...")
    result = run_cmd(["wrangler", "kv:namespace", "create", name], check=False)

    if result.returncode != 0:
        if "already exists" in result.stderr:
            list_result = run_cmd(["wrangler", "kv:namespace", "list", "--json"], check=False)
            if list_result.returncode == 0:
                namespaces = json.loads(list_result.stdout)
                for ns in namespaces:
                    if ns.get("title") == f"sos-agent-{name}":
                        return ns.get("id")
        print(f"Error creating KV namespace: {result.stderr}")
        return None

    for line in result.stdout.split("\n"):
        if "id" in line and "=" in line:
            return line.split("=")[-1].strip().strip('"')

    return None


def init_d1_schema(database_id: str):
    """Initialize D1 database with SOS schema."""
    print("Initializing database schema...")
    schema_path = Path(__file__).parent.parent / "cloudflare" / "schema.sql"

    result = run_cmd([
        "wrangler", "d1", "execute", "sos-memory",
        "--file", str(schema_path)
    ], check=False)

    if result.returncode != 0:
        print(f"Warning: Schema init issue: {result.stderr}")


def generate_agent_key() -> dict:
    """Generate unique agent key."""
    agent_id = f"agent_{uuid.uuid4().hex[:8]}"
    return {
        "agent_id": agent_id,
        "created": str(uuid.uuid4()),
        "version": "1.0.0",
        "local_first": True,
        "mumega_stores_nothing": True
    }


def create_wrangler_config(d1_id: str, kv_id: str, agent_id: str):
    """Create wrangler.toml with customer's IDs."""
    template_path = Path(__file__).parent.parent / "cloudflare" / "wrangler.toml"
    output_path = Path.cwd() / "wrangler.toml"

    template = template_path.read_text()

    config = template.replace("${D1_DATABASE_ID}", d1_id)
    config = config.replace("${KV_NAMESPACE_ID}", kv_id)
    config = config.replace("${AGENT_ID}", agent_id)

    output_path.write_text(config)
    print(f"Created: {output_path}")


def copy_worker_files():
    """Copy worker files to current directory."""
    cf_dir = Path(__file__).parent.parent / "cloudflare"
    cwd = Path.cwd()

    # Copy worker.js
    worker_src = cf_dir / "worker.js"
    worker_dst = cwd / "worker.js"
    worker_dst.write_text(worker_src.read_text())
    print(f"Created: {worker_dst}")


def deploy_worker():
    """Deploy worker to Cloudflare."""
    print("\nDeploying to Cloudflare...")
    result = run_cmd(["wrangler", "deploy"], check=False)

    if result.returncode == 0:
        print("Deployment successful!")
        # Extract URL from output
        for line in result.stdout.split("\n"):
            if "https://" in line and ".workers.dev" in line:
                url = line.strip()
                print(f"\nYour SOS agent is live at: {url}")
                return url
    else:
        print(f"Deployment failed: {result.stderr}")

    return None


def main():
    """Main initialization flow."""
    print("=" * 60)
    print("SOS Self-Hosted Initialization")
    print("Mumega keeps NOTHING. Your data stays on YOUR Cloudflare.")
    print("=" * 60)
    print()

    # Check prerequisites
    if not check_wrangler():
        print("Error: Wrangler CLI not found.")
        print("Install with: npm install -g wrangler")
        print("Then authenticate: wrangler login")
        sys.exit(1)

    # Check if authenticated
    result = run_cmd(["wrangler", "whoami"], check=False)
    if "not logged in" in result.stdout.lower() or result.returncode != 0:
        print("Please authenticate with Cloudflare first:")
        print("  wrangler login")
        sys.exit(1)

    print(f"Authenticated as: {result.stdout.strip()}")
    print()

    # Create resources
    d1_id = create_d1_database()
    if not d1_id:
        print("Failed to create D1 database")
        sys.exit(1)
    print(f"D1 Database ID: {d1_id}")

    kv_id = create_kv_namespace()
    if not kv_id:
        print("Failed to create KV namespace")
        sys.exit(1)
    print(f"KV Namespace ID: {kv_id}")

    # Initialize schema
    init_d1_schema(d1_id)

    # Generate agent key
    agent_key = generate_agent_key()
    agent_id = agent_key["agent_id"]
    print(f"Agent ID: {agent_id}")

    # Save agent key locally
    key_path = Path.cwd() / ".sos-agent-key.json"
    key_path.write_text(json.dumps(agent_key, indent=2))
    print(f"Agent key saved: {key_path}")

    # Create config files
    create_wrangler_config(d1_id, kv_id, agent_id)
    copy_worker_files()

    print()
    print("=" * 60)
    print("Setup complete! Next steps:")
    print("=" * 60)
    print()
    print("1. Add your AI provider API key:")
    print("   wrangler secret put GEMINI_API_KEY")
    print("   # or")
    print("   wrangler secret put OPENAI_API_KEY")
    print()
    print("2. Deploy your agent:")
    print("   wrangler deploy")
    print()
    print("3. (Optional) Register in Yellow Pages:")
    print("   curl -X POST https://mumega.com/registry/register \\")
    print(f"     -d '{{\"agent_id\": \"{agent_id}\", \"public_profile\": {{...}}}}'")
    print()
    print("Your data NEVER leaves your Cloudflare account.")
    print("Mumega stores NOTHING. When institutions ask, we have nothing.")
    print()


if __name__ == "__main__":
    main()
