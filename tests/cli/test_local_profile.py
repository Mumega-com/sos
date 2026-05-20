from __future__ import annotations

import json
from pathlib import Path

from sos.cli.local import init_profile


def test_init_profile_writes_local_tokens_and_env(tmp_path: Path) -> None:
    env = init_profile(root=tmp_path)

    env_path = tmp_path / ".sos" / "local" / "dev.env"
    tokens_path = tmp_path / ".sos" / "local" / "tokens.json"

    assert env_path.exists()
    assert tokens_path.exists()
    assert env["SOS_BUS_TOKENS_PATH"] == str(tokens_path)
    assert env["SOS_LOCAL_PROJECT"] == "sos-local"
    assert env["SOS_LOCAL_ALPHA_TOKEN"].startswith("sk-sos-dev-alpha-")
    assert env["SOS_LOCAL_BRAVO_TOKEN"].startswith("sk-sos-dev-bravo-")
    assert env["SOS_SYSTEM_TOKEN"].startswith("sk-sos-dev-system-")

    records = json.loads(tokens_path.read_text())
    assert [record["agent"] for record in records] == ["alpha", "bravo"]
    assert all(record["project"] == "sos-local" for record in records)
    assert all(record["token_hash"] for record in records)


def test_init_profile_is_idempotent_without_force(tmp_path: Path) -> None:
    first = init_profile(root=tmp_path)
    second = init_profile(root=tmp_path)

    assert second["SOS_LOCAL_ALPHA_TOKEN"] == first["SOS_LOCAL_ALPHA_TOKEN"]
    assert second["SOS_LOCAL_BRAVO_TOKEN"] == first["SOS_LOCAL_BRAVO_TOKEN"]

