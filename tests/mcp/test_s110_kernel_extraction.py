from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sos.mcp import sos_mcp_sse as sse


class FakeRedis:
    def __init__(self, agents: dict[str, dict] | None = None):
        self.agents = agents or {}

    async def hgetall(self, key: str) -> dict[str, str]:
        if key != "sos:registry:agents":
            return {}
        return {name: json.dumps(config) for name, config in self.agents.items()}


def test_status_returns_empty_agent_list_when_registry_empty():
    assert asyncio.run(sse._get_agent_statuses(FakeRedis())) == []


def test_status_reads_agents_from_redis_registry():
    statuses = asyncio.run(sse._get_agent_statuses(FakeRedis({
        "agent-a": {"type": "remote", "model": "model-a", "role": "role-a"},
    })))

    assert statuses == [{
        "agent": "agent-a",
        "type": "remote",
        "model": "model-a",
        "role": "role-a",
        "status": "unknown",
    }]


def test_kernel_files_do_not_ship_mumega_agent_roster_or_topology():
    repo = Path(__file__).resolve().parents[2]
    checked = [
        repo / "sos" / "services" / "health" / "calcifer.py",
        repo / "breakables.yaml",
    ]
    forbidden = (
        "kasra", "loom", "codex", "mumega", "athena", "sol", "worker",
        "dandan", "gemma", "mizan", "river", "cyrus", "antigravity",
        "localhost:18789",
    )
    for path in checked:
        text = path.read_text()
        for term in forbidden:
            assert term not in text


def test_hosted_product_tool_catalogue_is_not_in_kernel():
    repo = Path(__file__).resolve().parents[2]
    assert not (repo / "sos" / "mcp" / "customer_tools.py").exists()
    assert not (repo / "sos" / "mcp" / "customer_onboard_prompt.py").exists()
    assert sse.CUSTOMER_TOOLS == []
