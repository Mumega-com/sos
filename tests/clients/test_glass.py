"""Unit tests for sos.clients.glass.GlassClient (sync).

4 test cases using httpx.MockTransport:
1. upsert_tile — happy path sends Idempotency-Key header
2. list_tiles — returns list from response JSON
3. delete_tile — 204 returns True, 404 returns False
4. get_payload — returns parsed dict
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from sos.clients.glass import GlassClient


# ---------------------------------------------------------------------------
# Transport helper
# ---------------------------------------------------------------------------


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# 1. upsert_tile — sends Idempotency-Key
# ---------------------------------------------------------------------------


def test_upsert_tile_sends_idempotency_key() -> None:
    captured_headers: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured_headers.update(dict(req.headers))
        tile = {
            "id": "health-light",
            "title": "Health",
            "query": {"kind": "http", "service": "registry", "path": "/health"},
            "template": "status_light",
            "refresh_interval_s": 60,
            "tenant": "acme",
        }
        return httpx.Response(200, json=tile)

    client = GlassClient(base_url="http://localhost:8092", transport=_transport(handler), token="tk_system")

    tile_body = {
        "id": "health-light",
        "title": "Health",
        "query": {"kind": "http", "service": "registry", "path": "/health"},
        "template": "status_light",
        "refresh_interval_s": 60,
    }
    result = client.upsert_tile("acme", tile_body, idempotency_key="idem-abc")
    assert result["id"] == "health-light"
    assert "idem-abc" in captured_headers.get("idempotency-key", "")


def test_upsert_tile_autogenerates_idempotency_key() -> None:
    captured_headers: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured_headers.update(dict(req.headers))
        tile = {
            "id": "health-light",
            "title": "Health",
            "query": {"kind": "http", "service": "registry", "path": "/health"},
            "template": "status_light",
            "refresh_interval_s": 60,
            "tenant": "acme",
        }
        return httpx.Response(200, json=tile)

    client = GlassClient(base_url="http://localhost:8092", transport=_transport(handler), token="tk_system")

    tile_body = {
        "id": "health-light",
        "title": "Health",
        "query": {"kind": "http", "service": "registry", "path": "/health"},
        "template": "status_light",
    }
    client.upsert_tile("acme", tile_body)  # no idempotency_key
    assert "idempotency-key" in captured_headers


# ---------------------------------------------------------------------------
# 2. list_tiles
# ---------------------------------------------------------------------------


def test_list_tiles() -> None:
    tiles = [
        {
            "id": "health-light",
            "title": "Health",
            "query": {"kind": "http", "service": "registry", "path": "/health"},
            "template": "status_light",
            "refresh_interval_s": 60,
            "tenant": "acme",
        }
    ]

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"tiles": tiles, "count": 1})

    client = GlassClient(base_url="http://localhost:8092", transport=_transport(handler), token="tk_system")
    result = client.list_tiles("acme")
    assert len(result) == 1
    assert result[0]["id"] == "health-light"


# ---------------------------------------------------------------------------
# 3. delete_tile — 204 True / 404 False
# ---------------------------------------------------------------------------


def test_delete_tile_returns_true_on_204() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    client = GlassClient(base_url="http://localhost:8092", transport=_transport(handler), token="tk_system")
    result = client.delete_tile("acme", "health-light")
    assert result is True


def test_delete_tile_returns_false_on_404() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "not found"})

    client = GlassClient(base_url="http://localhost:8092", transport=_transport(handler), token="tk_system")
    result = client.delete_tile("acme", "nonexistent")
    assert result is False


# ---------------------------------------------------------------------------
# 4. get_payload
# ---------------------------------------------------------------------------


def test_get_payload() -> None:
    payload = {
        "tile_id": "health-light",
        "rendered_at": datetime.now(timezone.utc).isoformat(),
        "data": {"kind": "http", "status": 200, "body": {"status": "healthy"}},
        "cache_ttl_s": 60,
    }

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = GlassClient(base_url="http://localhost:8092", transport=_transport(handler), token="tk_system")
    result = client.get_payload("acme", "health-light")
    assert result["tile_id"] == "health-light"
    assert result["cache_ttl_s"] == 60
    assert result["data"]["kind"] == "http"
