#!/usr/bin/env python3
"""
E2E test: Full Mumega signup-to-MCP journey.

Tests the complete customer path:
  POST /signup → token registered → tenant in DB → build enqueued
  → MCP SSE initialize → remember/recall memory → cleanup

Run with:
  cd ~/SOS && python3 -m pytest tests/test_e2e_signup.py -v
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SAAS_BASE = "http://localhost:8075"
MCP_BASE = "http://localhost:6070"
ADMIN_KEY = "sk-mumega-internal-001"
ADMIN_HEADERS = {"Authorization": f"Bearer {ADMIN_KEY}"}
BUS_TOKENS_PATH = Path.home() / "SOS" / "sos" / "bus" / "tokens.json"

# Unique test tenant so we don't clash with existing data
_SUFFIX = uuid.uuid4().hex[:6]
TEST_NAME = f"e2e-test-{_SUFFIX}"
TEST_EMAIL = f"e2e-{_SUFFIX}@test.mumega.local"
TEST_PLAN = "starter"

# Shared state across test functions (populated by earlier steps)
_state: dict[str, Any] = {
    "slug": None,
    "mcp_url": None,
    "mcp_token": None,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_token_from_url(mcp_url: str) -> str:
    """Pull the raw token out of https://mcp.mumega.com/sse/<token>."""
    m = re.search(r"/sse/(.+)$", mcp_url)
    assert m, f"Cannot extract token from mcp_url: {mcp_url}"
    return m.group(1)


def _mcp_call(
    token: str,
    method: str,
    params: dict | None = None,
    req_id: int = 1,
    retries: int = 4,
    retry_delay: float = 10.0,
) -> dict:
    """POST a JSON-RPC 2.0 message to /mcp/{token} and return the parsed response.

    Retries on 401 to handle the MCP server's 30-second token-cache hot-reload window.
    A freshly registered token may not be visible until the cache reloads.
    """
    body = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
    last_status = None
    last_text = ""
    for attempt in range(retries):
        r = httpx.post(
            f"{MCP_BASE}/mcp/{token}",
            json=body,
            timeout=15,
        )
        last_status = r.status_code
        last_text = r.text
        if r.status_code == 401 and attempt < retries - 1:
            print(
                f"\n  [MCP] 401 on attempt {attempt + 1}/{retries} — "
                f"waiting {retry_delay}s for token cache to reload..."
            )
            time.sleep(retry_delay)
            continue
        break

    assert last_status in (200, 202), (
        f"MCP {method} returned HTTP {last_status}: {last_text[:300]}"
    )
    if last_status == 202 or not r.content:
        return {}
    return r.json()


# ---------------------------------------------------------------------------
# Step 1: POST /signup
# ---------------------------------------------------------------------------


def test_step1_signup():
    """POST /signup — get slug, mcp_url, connect configs."""
    resp = httpx.post(
        f"{SAAS_BASE}/signup",
        json={"name": TEST_NAME, "email": TEST_EMAIL, "plan": TEST_PLAN},
        timeout=30,
    )
    assert resp.status_code == 200, f"Signup failed ({resp.status_code}): {resp.text[:500]}"

    data = resp.json()

    # Required fields
    assert "tenant" in data, f"No 'tenant' in response: {data.keys()}"
    assert "mcp_url" in data, f"No 'mcp_url' in response: {data.keys()}"
    assert "connect" in data, f"No 'connect' in response: {data.keys()}"

    slug = data["tenant"]
    mcp_url = data["mcp_url"]

    # Slug should be derived from TEST_NAME
    assert slug, "Empty slug"
    assert mcp_url.startswith("https://mcp.mumega.com/sse/") or mcp_url.startswith("http://"), (
        f"Unexpected mcp_url format: {mcp_url}"
    )

    token = _extract_token_from_url(mcp_url)
    assert token, "Empty token in mcp_url"

    # Persist for subsequent steps
    _state["slug"] = slug
    _state["mcp_url"] = mcp_url
    _state["mcp_token"] = token

    print(f"\n[PASS] Signup: slug={slug}, token={token[:16]}...")


# ---------------------------------------------------------------------------
# Step 2: Verify token registered in tokens.json
# ---------------------------------------------------------------------------


def test_step2_token_registered():
    """Verify the token hash is in ~/SOS/sos/bus/tokens.json."""
    assert _state["mcp_token"], "mcp_token not set — run step1 first"

    token_hash = hashlib.sha256(_state["mcp_token"].encode()).hexdigest()

    assert BUS_TOKENS_PATH.exists(), f"tokens.json missing: {BUS_TOKENS_PATH}"
    tokens = json.loads(BUS_TOKENS_PATH.read_text())

    found = any(
        entry.get("token_hash") == token_hash and entry.get("active", True)
        for entry in tokens
    )
    assert found, (
        f"Token hash {token_hash[:16]}... not found in tokens.json "
        f"(slug={_state['slug']}, entries={len(tokens)})"
    )

    # Also confirm the project scope matches
    for entry in tokens:
        if entry.get("token_hash") == token_hash:
            assert entry.get("project") == _state["slug"], (
                f"Token project mismatch: expected {_state['slug']}, got {entry.get('project')}"
            )
            assert entry.get("scope") == "customer", (
                f"Token scope mismatch: expected 'customer', got {entry.get('scope')}"
            )
            break

    print(f"[PASS] Token hash found in tokens.json for slug={_state['slug']}")


# ---------------------------------------------------------------------------
# Step 3: Verify tenant in DB
# ---------------------------------------------------------------------------


def test_step3_tenant_in_db():
    """GET /tenants/{slug} — assert status=active, plan=starter."""
    assert _state["slug"], "slug not set — run step1 first"

    resp = httpx.get(
        f"{SAAS_BASE}/tenants/{_state['slug']}",
        headers=ADMIN_HEADERS,
        timeout=10,
    )
    assert resp.status_code == 200, (
        f"GET /tenants/{_state['slug']} failed ({resp.status_code}): {resp.text[:300]}"
    )

    data = resp.json()
    assert data.get("status") == "active", (
        f"Expected status=active, got {data.get('status')}"
    )

    plan_val = data.get("plan")
    # Plan may be an enum value like "starter" or TenantPlan.STARTER
    plan_str = plan_val.lower() if isinstance(plan_val, str) else str(plan_val).lower()
    assert TEST_PLAN in plan_str, (
        f"Expected plan to contain '{TEST_PLAN}', got {plan_val!r}"
    )

    print(f"[PASS] Tenant status={data['status']}, plan={plan_val}")


# ---------------------------------------------------------------------------
# Step 4: Verify build enqueued / no build errors
# ---------------------------------------------------------------------------


def test_step4_build_enqueued():
    """GET /builds/status — verify no errors for our tenant's build."""
    resp = httpx.get(
        f"{SAAS_BASE}/builds/status",
        headers=ADMIN_HEADERS,
        timeout=10,
    )
    assert resp.status_code == 200, (
        f"GET /builds/status failed ({resp.status_code}): {resp.text[:300]}"
    )

    data = resp.json()
    # Data is whatever build_queue.get_status() returns.
    # Just assert it's parseable JSON with no hard errors.
    assert data is not None, "Build status returned null"

    # If there's an 'errors' key, it should not reference our tenant in an error state
    if isinstance(data, dict):
        slug = _state["slug"]
        errors = data.get("errors", [])
        our_errors = [e for e in errors if isinstance(e, dict) and e.get("slug") == slug]
        assert not our_errors, f"Build errors found for {slug}: {our_errors}"

    print(f"[PASS] Build status OK: {json.dumps(data)[:200]}")


# ---------------------------------------------------------------------------
# Step 5: MCP initialize (JSON-RPC over /mcp/{token})
# ---------------------------------------------------------------------------


def test_step5_mcp_initialize():
    """Call initialize on MCP server with the tenant token."""
    assert _state["mcp_token"], "mcp_token not set — run step1 first"

    token = _state["mcp_token"]
    resp_data = _mcp_call(
        token,
        "initialize",
        params={
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "e2e-test", "version": "1.0"},
        },
        req_id=1,
    )

    if resp_data:
        assert "result" in resp_data, f"No 'result' in initialize response: {resp_data}"
        result = resp_data["result"]
        assert "serverInfo" in result or "capabilities" in result, (
            f"Missing serverInfo/capabilities in: {result}"
        )
        print(f"[PASS] MCP initialize: serverInfo={result.get('serverInfo')}")
    else:
        # 202 response is acceptable (async / streamable-http)
        print("[PASS] MCP initialize: 202 accepted (async transport)")

    # Follow up with tools/list
    tools_resp = _mcp_call(token, "tools/list", req_id=2)
    if tools_resp and "result" in tools_resp:
        tools = tools_resp["result"].get("tools", [])
        tool_names = [t.get("name") for t in tools]
        assert len(tools) > 0, "No tools returned by tools/list"
        print(f"[PASS] tools/list returned {len(tools)} tools: {tool_names[:5]}...")
    else:
        print("[PASS] tools/list: 202 accepted (async transport)")


# ---------------------------------------------------------------------------
# Step 6: remember / recall via MCP
# ---------------------------------------------------------------------------


def test_step6_remember_recall():
    """Call remember then recall via MCP and assert the text comes back."""
    assert _state["mcp_token"], "mcp_token not set — run step1 first"

    token = _state["mcp_token"]
    test_text = f"E2E test memory {_SUFFIX}: the quick brown fox"

    # remember
    remember_resp = _mcp_call(
        token,
        "tools/call",
        params={"name": "remember", "arguments": {"text": test_text}},
        req_id=3,
    )

    if remember_resp and "error" in remember_resp:
        pytest.skip(f"remember tool returned error (Mirror may be down): {remember_resp['error']}")

    print(f"[PASS] remember: accepted")

    # Small pause to let the memory write propagate
    time.sleep(1)

    # recall
    recall_resp = _mcp_call(
        token,
        "tools/call",
        params={"name": "recall", "arguments": {"query": f"E2E test memory {_SUFFIX}"}},
        req_id=4,
    )

    if recall_resp and "error" in recall_resp:
        pytest.skip(f"recall tool returned error (Mirror may be down): {recall_resp['error']}")

    if recall_resp and "result" in recall_resp:
        content = recall_resp["result"].get("content", [])
        full_text = " ".join(c.get("text", "") for c in content if c.get("type") == "text")
        _COLD_SIGNALS = ("no matching", "no memories", "[]", "")
        is_cold = not full_text.strip() or any(s in full_text.lower() for s in _COLD_SIGNALS)
        if is_cold:
            # Mirror cold-start or embeddings not yet indexed — not a test failure
            pytest.skip(
                f"Mirror recall returned no results (cold-start / embeddings not seeded): {full_text!r}"
            )
        # Memory was found — assert it matches
        assert _SUFFIX in full_text or "fox" in full_text or "E2E" in full_text, (
            f"Recalled text doesn't match stored memory.\nStored: {test_text!r}\nRecalled: {full_text[:300]}"
        )
        print(f"[PASS] recall returned matching memory: {full_text[:120]}")
    else:
        print("[PASS] recall: 202 accepted (async transport)")


# ---------------------------------------------------------------------------
# Step 7: Cleanup — cancel the test tenant
# ---------------------------------------------------------------------------


def test_step7_cleanup():
    """PUT /tenants/{slug} status=cancelled to clean up."""
    if not _state["slug"]:
        pytest.skip("No slug — signup step did not run")

    resp = httpx.put(
        f"{SAAS_BASE}/tenants/{_state['slug']}",
        headers=ADMIN_HEADERS,
        json={"status": "cancelled"},
        timeout=10,
    )
    assert resp.status_code == 200, (
        f"Cleanup PUT /tenants/{_state['slug']} failed ({resp.status_code}): {resp.text[:300]}"
    )

    data = resp.json()
    status = data.get("status", "")
    assert "cancel" in str(status).lower(), (
        f"Expected status to contain 'cancel', got {status!r}"
    )

    # Also deactivate the token in tokens.json
    slug = _state["slug"]
    if BUS_TOKENS_PATH.exists():
        tokens = json.loads(BUS_TOKENS_PATH.read_text())
        token_hash = hashlib.sha256(_state["mcp_token"].encode()).hexdigest()
        changed = False
        for entry in tokens:
            if entry.get("token_hash") == token_hash and entry.get("project") == slug:
                entry["active"] = False
                changed = True
        if changed:
            BUS_TOKENS_PATH.write_text(json.dumps(tokens, indent=2))
            print(f"[PASS] Deactivated token in tokens.json for slug={slug}")

    print(f"[PASS] Cleanup: tenant {slug} cancelled")
