"""
Squad shared state backed by Mirror API with local JSON fallback.

Convention:
  POST /store with:
    {
      "text": JSON.stringify(state),
      "agent": "squad:{squad_id}",
      "context_id": "{squad_id}:{key}"
    }

Reads are served from Mirror when available and mirrored locally under:
  sovereign/.squads/{squad_id}/{key}.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

try:
    from kernel.config import MIRROR_TOKEN, MIRROR_URL, SOVEREIGN_SQUADS_DIR
except ModuleNotFoundError:
    try:
        from sovereign.config import MIRROR_TOKEN, MIRROR_URL
        SOVEREIGN_SQUADS_DIR = "/home/mumega/SOS/sovereign/.squads"
    except ModuleNotFoundError:
        from config import MIRROR_TOKEN, MIRROR_URL
        SOVEREIGN_SQUADS_DIR = "/home/mumega/SOS/sovereign/.squads"


SQUADS_DIR = Path(SOVEREIGN_SQUADS_DIR)
MIRROR_HEADERS = {
    "Authorization": f"Bearer {MIRROR_TOKEN}",
    "Content-Type": "application/json",
}


def _agent_name(squad_id: str) -> str:
    return f"squad:{squad_id}"


def _context_id(squad_id: str, key: str) -> str:
    return f"{squad_id}:{key}"


def _state_dir(squad_id: str) -> Path:
    path = SQUADS_DIR / squad_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_path(squad_id: str, key: str) -> Path:
    return _state_dir(squad_id) / f"{key}.json"


def _write_local_state(squad_id: str, key: str, data: dict[str, Any]) -> Path:
    path = _state_path(squad_id, key)
    path.write_text(json.dumps(data, indent=2, sort_keys=True))
    return path


def _read_local_state(squad_id: str, key: str) -> dict[str, Any]:
    path = _state_path(squad_id, key)
    if not path.exists():
        raise FileNotFoundError(f"No local squad state for {squad_id}:{key}")
    return json.loads(path.read_text())


def _extract_state_payload(engram: dict[str, Any]) -> dict[str, Any]:
    raw_data = engram.get("raw_data") or {}
    text = raw_data.get("text")
    if not isinstance(text, str):
        raise ValueError("Engram does not contain JSON text state")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Squad state payload must decode to a dict")
    return data


async def save_state(squad_id: str, key: str, data: dict[str, Any]) -> dict[str, Any]:
    """
    Save squad state to Mirror and always persist a local JSON copy.
    Returns a small status payload including where it was saved.
    """
    if not isinstance(data, dict):
        raise TypeError("data must be a dict")

    local_path = _write_local_state(squad_id, key, data)
    body = {
        "text": json.dumps(data, sort_keys=True),
        "agent": _agent_name(squad_id),
        "context_id": _context_id(squad_id, key),
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(f"{MIRROR_URL}/store", headers=MIRROR_HEADERS, json=body)
            response.raise_for_status()
            payload = response.json()
        return {
            "saved": "mirror+local",
            "local_path": str(local_path),
            "mirror": payload,
        }
    except httpx.HTTPError:
        return {
            "saved": "local",
            "local_path": str(local_path),
        }


async def load_state(squad_id: str, key: str) -> dict[str, Any]:
    """
    Load the latest squad state for a specific key.
    Prefer Mirror; fall back to local JSON if Mirror is unavailable or missing.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{MIRROR_URL}/recent/{_agent_name(squad_id)}",
                headers=MIRROR_HEADERS,
                params={"limit": 200},
            )
            response.raise_for_status()
            engrams = response.json().get("engrams", [])
        target_context = _context_id(squad_id, key)
        for engram in engrams:
            if engram.get("context_id") == target_context:
                data = _extract_state_payload(engram)
                _write_local_state(squad_id, key, data)
                return data
    except httpx.HTTPError:
        pass

    return _read_local_state(squad_id, key)


async def list_state(squad_id: str) -> list[str]:
    """
    List known keys for a squad.
    Prefer Mirror, fall back to local files if Mirror is unavailable.
    """
    prefix = f"{squad_id}:"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{MIRROR_URL}/recent/{_agent_name(squad_id)}",
                headers=MIRROR_HEADERS,
                params={"limit": 200},
            )
            response.raise_for_status()
            engrams = response.json().get("engrams", [])
        keys = {
            context_id[len(prefix):]
            for engram in engrams
            if isinstance((context_id := engram.get("context_id")), str) and context_id.startswith(prefix)
        }
        if keys:
            return sorted(keys)
    except httpx.HTTPError:
        pass

    squad_dir = _state_dir(squad_id)
    return sorted(path.stem for path in squad_dir.glob("*.json"))


async def _main() -> int:
    report_path = Path("/home/mumega/SOS/sovereign/.reports/dnu_linkmap_20260405.json")
    report = json.loads(report_path.read_text())

    save_result = await save_state("seo-dnu", "audit", report)
    loaded = await load_state("seo-dnu", "audit")
    keys = await list_state("seo-dnu")

    print(json.dumps({
        "save_result": save_result,
        "loaded_matches": loaded == report,
        "keys": keys,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(__import__("asyncio").run(_main()))
