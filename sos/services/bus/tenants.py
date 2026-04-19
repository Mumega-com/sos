"""Tenant registry — maps tenant names to Redis DB numbers.

Stored at ~/.sos/tenants.json. Manages DB allocation.
Max 16 tenants (Redis DBs 0-15). DB 0 = mumega (core).
"""

from __future__ import annotations

import json
from pathlib import Path

TENANTS_PATH = Path.home() / ".sos" / "tenants.json"
MAX_DBS = 16


def _load() -> dict[str, int | str]:
    """Load tenant registry from disk."""
    if TENANTS_PATH.exists():
        return json.loads(TENANTS_PATH.read_text())
    return {"mumega": 0, "_next": 1}


def _save(data: dict[str, int | str]) -> None:
    """Persist tenant registry to disk."""
    TENANTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TENANTS_PATH.write_text(json.dumps(data, indent=2) + "\n")


def get_tenant_db(tenant_name: str) -> int:
    """Return DB number for tenant. Allocates a new one if first time."""
    data = _load()
    if tenant_name in data:
        return int(data[tenant_name])
    return register_tenant(tenant_name)


def list_tenants() -> dict[str, int]:
    """Return all tenant -> DB mappings (excludes internal keys like _next)."""
    data = _load()
    return {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, int)}


def register_tenant(name: str) -> int:
    """Allocate next available DB for a new tenant. Returns DB number."""
    data = _load()

    # Already registered
    if name in data and not name.startswith("_"):
        return int(data[name])

    next_db = int(data.get("_next", 1))
    if next_db >= MAX_DBS:
        raise ValueError(f"No available Redis DBs. Max {MAX_DBS} tenants reached.")

    data[name] = next_db
    data["_next"] = next_db + 1
    _save(data)
    return next_db


def get_redis_url(tenant_name: str) -> str:
    """Return full Redis URL with correct DB number for tenant."""
    db = get_tenant_db(tenant_name)
    return f"redis://localhost:6379/{db}"
