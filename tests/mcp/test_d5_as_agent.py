"""S027 D-5 — `as_agent` MCP primitive (LOCK-S027-D-5-as-agent-mcp-primitive).

Tests for `_handle_as_agent` in sos.mcp.sos_mcp_sse covering the 5 LOCKs:
  L-1 — Layer A (caller scope class) + Layer B (target-tenant match for
        tenant-admin path only). Order split so tenant-admin callers cannot
        distinguish "agent doesn't exist" from "you can't access it"
        (agent-existence oracle leak closure — Athena REFINE 23:43Z).
  L-2 — Mirror engram fetch tenant-scoped (project=target_tenant_slug).
  L-3 — Scaffold-missing failure mode: re-render via D-2b idempotent
        function; if template also missing → fail loud with `scaffold_missing`.
  L-4 — Per-SSE-connection session-identity mutation. session_id is
        per-connection (uuid4 at SSE accept), NOT per-token — two
        connections sharing one Bearer get distinct session_ids.
  L-5 — Mandatory audit row (fire-and-forget, fail-open, substrate-callers
        also emit).

§3 adversarial cases (12) + §4 acceptance criteria (10) = 22 tests covering
the full spec.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

# Mirror stubs (mirror modules not on test path).
_mirror_db_stub = types.ModuleType("mirror.kernel.db")
_mirror_db_stub.get_db = lambda: None
_mirror_embeddings_stub = types.ModuleType("mirror.kernel.embeddings")
_mirror_embeddings_stub.get_embedding = lambda text: []
sys.modules.setdefault("mirror.kernel.db", _mirror_db_stub)
sys.modules.setdefault("mirror.kernel.embeddings", _mirror_embeddings_stub)

from sos.mcp.sos_mcp_sse import (
    MCPAuthContext,
    _handle_as_agent,
    _session_as_agent,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _ctx_tenant_admin(*, tenant_slug: str, role: str = "owner") -> MCPAuthContext:
    return MCPAuthContext(
        token="t" * 64,
        tenant_id=tenant_slug,
        is_system=False,
        source="test",
        agent_name=f"admin-{tenant_slug}",
        scope="tenant",
        role=role,
    )


def _ctx_substrate(agent_name: str = "kasra") -> MCPAuthContext:
    return MCPAuthContext(
        token="s" * 64,
        tenant_id="sos",
        is_system=False,
        source="test",
        agent_name=agent_name,
        scope="",  # substrate "agent" tokens have empty scope
    )


def _ctx_tenant_agent(
    *, tenant_slug: str, agent_name: str, agent_kind: str
) -> MCPAuthContext:
    return MCPAuthContext(
        token="a" * 64,
        tenant_id=tenant_slug,
        is_system=False,
        source="test",
        agent_name=agent_name,
        scope="tenant-agent",
        agent_kind=agent_kind,
    )


def _ctx_customer(tenant_slug: str = "acme") -> MCPAuthContext:
    return MCPAuthContext(
        token="c" * 64,
        tenant_id=tenant_slug,
        is_system=False,
        source="test",
        agent_name="customer-x",
        scope="customer",
    )


def _seed_target_token(monkeypatch, *, agent_name: str, scope: str,
                      tenant_slug: str = "", agent_kind: str = "") -> None:
    """Seed _local_token_cache with a single target token entry."""
    from sos.mcp import sos_mcp_sse as module

    target_ctx = MCPAuthContext(
        token="hash_target",
        tenant_id=tenant_slug or None,
        is_system=False,
        source="test",
        agent_name=agent_name,
        scope=scope,
        agent_kind=agent_kind,
    )

    fake_cache = {"hash_target": target_ctx}

    class _StubCache:
        def get(self):
            return fake_cache

        def invalidate(self):
            pass

    monkeypatch.setattr(module, "_local_token_cache", _StubCache())


def _empty_token_cache(monkeypatch) -> None:
    from sos.mcp import sos_mcp_sse as module

    class _StubCache:
        def get(self):
            return {}

        def invalidate(self):
            pass

    monkeypatch.setattr(module, "_local_token_cache", _StubCache())


def _stub_qnft_registry(monkeypatch, registry: dict) -> None:
    """Override the QNFT registry loader with a fixed dict."""
    from sos.mcp import sos_mcp_sse as module
    monkeypatch.setattr(
        module, "_load_qnft_registry_for_as_agent", lambda: registry
    )


def _stub_scaffold_path(monkeypatch, tmp_path: Path, *,
                       tenant_slug: str, agent_kind: str,
                       content: str = "# Scaffold for {agent_kind}\n") -> Path:
    """Create a scaffold file at tmp_path/.mumega/customers/.../CLAUDE.md and
    monkeypatch Path.home() so the handler resolves to it.
    """
    home_root = tmp_path / "home"
    scaffold_dir = home_root / ".mumega" / "customers" / tenant_slug / "agents" / agent_kind
    scaffold_dir.mkdir(parents=True, exist_ok=True)
    scaffold_path = scaffold_dir / "CLAUDE.md"
    scaffold_path.write_text(content.format(agent_kind=agent_kind), encoding="utf-8")

    monkeypatch.setattr(Path, "home", lambda: home_root)
    return scaffold_path


def _stub_emit_audit(monkeypatch) -> list:
    """Replace _emit_audit with a recorder. Returns the list events are appended to."""
    from sos.mcp import sos_mcp_sse as module
    events: list = []

    async def _recorder(event):
        events.append(event)

    monkeypatch.setattr(module, "_emit_audit", _recorder)
    return events


def _stub_mirror(monkeypatch, rows: list[dict] | None = None,
                raises: Exception | None = None) -> None:
    """Stub the Mirror DB so recent_engrams() returns *rows* (or raises)."""
    from sos.mcp import sos_mcp_sse as module

    if rows is None and raises is None:
        monkeypatch.setattr(module, "_mirror_db", None)
        return

    class _FakeMirror:
        def recent_engrams(self, agent, limit, project):
            if raises:
                raise raises
            return rows or []

    monkeypatch.setattr(module, "_mirror_db", _FakeMirror())


def _parse_text_payload(result: dict) -> dict:
    """Extract the JSON payload from an MCP `_text(...)` result."""
    text = result["content"][0]["text"]
    return json.loads(text)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _reset_session_state():
    """Clear module-level _session_as_agent before each test."""
    _session_as_agent.clear()
    yield
    _session_as_agent.clear()


# ---------------------------------------------------------------------------
# §3.3 — Customer-token impersonation (Layer A)
# ---------------------------------------------------------------------------


def test_customer_token_forbidden(monkeypatch, tmp_path):
    """§3.3 — `scope='customer'` callers cannot use as_agent."""
    _empty_token_cache(monkeypatch)
    _stub_emit_audit(monkeypatch)
    auth = _ctx_customer()
    result = _run(_handle_as_agent(auth, {"name": "athena-acme"}, "sess-1"))
    body = _parse_text_payload(result)
    assert body["ok"] is False
    assert body["error"] == "customer_token_forbidden"
    # No session state mutation on rejection.
    assert auth.as_agent_active is False
    assert "sess-1" not in _session_as_agent


# ---------------------------------------------------------------------------
# §3.2 — Tenant-agent escalation attempt (Layer A)
# ---------------------------------------------------------------------------


def test_tenant_agent_token_cannot_escalate(monkeypatch, tmp_path):
    """§3.2 — already-forked tenant-agents cannot use as_agent."""
    _empty_token_cache(monkeypatch)
    _stub_emit_audit(monkeypatch)
    auth = _ctx_tenant_agent(
        tenant_slug="acme", agent_name="athena-acme", agent_kind="athena"
    )
    result = _run(_handle_as_agent(auth, {"name": "kasra-acme"}, "sess-2"))
    body = _parse_text_payload(result)
    assert body["ok"] is False
    assert body["error"] == "tenant_agent_token_cannot_escalate"


# ---------------------------------------------------------------------------
# §3.6 — Editor-role rejection (Layer A)
# ---------------------------------------------------------------------------


def test_tenant_admin_role_insufficient_for_editor(monkeypatch, tmp_path):
    """§3.6 — `scope='tenant'` + `role='editor'` rejected with named code."""
    _empty_token_cache(monkeypatch)
    _stub_emit_audit(monkeypatch)
    auth = _ctx_tenant_admin(tenant_slug="acme", role="editor")
    result = _run(_handle_as_agent(auth, {"name": "athena-acme"}, "sess-3"))
    body = _parse_text_payload(result)
    assert body["ok"] is False
    assert body["error"] == "tenant_admin_role_insufficient"
    assert body.get("role") == "editor"


def test_tenant_admin_role_insufficient_for_viewer(monkeypatch, tmp_path):
    """Viewer is also rejected — owner-only v1."""
    _empty_token_cache(monkeypatch)
    _stub_emit_audit(monkeypatch)
    auth = _ctx_tenant_admin(tenant_slug="acme", role="viewer")
    result = _run(_handle_as_agent(auth, {"name": "athena-acme"}, "sess-3v"))
    body = _parse_text_payload(result)
    assert body["error"] == "tenant_admin_role_insufficient"


# ---------------------------------------------------------------------------
# §3.5 — Phantom target — leak-resistant for tenant-admin
# ---------------------------------------------------------------------------


def test_phantom_target_tenant_admin_returns_not_authorized(monkeypatch, tmp_path):
    """§3.5a — tenant-admin caller probing nonexistent name → `not_authorized`
    (NOT `agent_not_found` — that would leak existence to cross-tenant probe).
    """
    _empty_token_cache(monkeypatch)
    _stub_emit_audit(monkeypatch)
    auth = _ctx_tenant_admin(tenant_slug="acme")
    result = _run(_handle_as_agent(auth, {"name": "athena-zzz-nonexistent"}, "sess-4"))
    body = _parse_text_payload(result)
    assert body["error"] == "not_authorized"


def test_phantom_target_substrate_returns_agent_not_found(monkeypatch, tmp_path):
    """§3.5b — substrate caller probing nonexistent name → distinct
    `agent_not_found` (no leak concern; full authority).
    """
    _empty_token_cache(monkeypatch)
    _stub_emit_audit(monkeypatch)
    auth = _ctx_substrate("kasra")
    result = _run(_handle_as_agent(auth, {"name": "athena-zzz-nonexistent"}, "sess-5"))
    body = _parse_text_payload(result)
    assert body["error"] == "agent_not_found"


# ---------------------------------------------------------------------------
# §3.1 — Spoofed cross-tenant attack (Layer B)
# ---------------------------------------------------------------------------


def test_cross_tenant_attack_returns_not_authorized(monkeypatch, tmp_path):
    """§3.1 — caller=tenant-admin@other, target=athena-acme → `not_authorized`.
    Layer B `hmac.compare_digest("other", "acme")` defeats spoofed `name`.
    """
    _seed_target_token(
        monkeypatch,
        agent_name="athena-acme",
        scope="tenant-agent",
        tenant_slug="acme",
        agent_kind="athena",
    )
    _stub_emit_audit(monkeypatch)
    auth = _ctx_tenant_admin(tenant_slug="other")
    result = _run(_handle_as_agent(auth, {"name": "athena-acme"}, "sess-6"))
    body = _parse_text_payload(result)
    assert body["error"] == "not_authorized"


# ---------------------------------------------------------------------------
# §4.3 — No-existence-oracle invariant (response equality)
# ---------------------------------------------------------------------------


def test_no_existence_oracle_phantom_equals_cross_tenant(monkeypatch, tmp_path):
    """§4.3 — for tenant-admin callers, phantom-target and cross-tenant-attack
    must produce IDENTICAL response payloads. Caller cannot distinguish
    "agent doesn't exist" from "you can't access it".
    """
    _stub_emit_audit(monkeypatch)

    # Case A: phantom target (cache empty)
    _empty_token_cache(monkeypatch)
    auth_a = _ctx_tenant_admin(tenant_slug="acme")
    result_a = _run(_handle_as_agent(auth_a, {"name": "athena-zzz"}, "sess-7a"))
    body_a = _parse_text_payload(result_a)

    # Case B: real target in different tenant
    _seed_target_token(
        monkeypatch,
        agent_name="athena-other",
        scope="tenant-agent",
        tenant_slug="other",
        agent_kind="athena",
    )
    auth_b = _ctx_tenant_admin(tenant_slug="acme")
    result_b = _run(_handle_as_agent(auth_b, {"name": "athena-other"}, "sess-7b"))
    body_b = _parse_text_payload(result_b)

    # Same error code, same `ok` flag — caller learns nothing about existence.
    assert body_a["error"] == body_b["error"] == "not_authorized"
    assert body_a["ok"] is False and body_b["ok"] is False
    # Response shape parity (no extra hint fields that leak).
    assert set(body_a.keys()) == set(body_b.keys())


# ---------------------------------------------------------------------------
# §3.4 — Substrate-name selection (target wrong scope)
# ---------------------------------------------------------------------------


def test_substrate_name_selection_rejected_for_substrate_caller(monkeypatch, tmp_path):
    """§3.4 — substrate caller targets a substrate name (e.g. 'loom') →
    `not_a_tenant_agent` (substrate names not selectable).
    """
    _seed_target_token(monkeypatch, agent_name="loom", scope="")
    _stub_emit_audit(monkeypatch)
    auth = _ctx_substrate("kasra")
    result = _run(_handle_as_agent(auth, {"name": "loom"}, "sess-8"))
    body = _parse_text_payload(result)
    assert body["error"] == "not_a_tenant_agent"


def test_substrate_name_selection_tenant_admin_rejected_as_not_authorized(
    monkeypatch, tmp_path
):
    """For tenant-admin caller, targeting a substrate name in their own tenant
    space — Layer B fails first (substrate has no tenant_slug) → returns
    `not_authorized`. (Tenant-admin path closes oracle: any non-tenant-fork
    target either gets `not_authorized` if Layer B fails, or `not_a_tenant_agent`
    if Layer B passes — substrate names have empty tenant_slug so Layer B
    fails first.)
    """
    _seed_target_token(monkeypatch, agent_name="loom", scope="", tenant_slug="")
    _stub_emit_audit(monkeypatch)
    auth = _ctx_tenant_admin(tenant_slug="acme")
    result = _run(_handle_as_agent(auth, {"name": "loom"}, "sess-8b"))
    body = _parse_text_payload(result)
    # Either not_authorized (Layer B fails on empty tenant_slug) — caller
    # cannot distinguish from "agent doesn't exist".
    assert body["error"] == "not_authorized"


# ---------------------------------------------------------------------------
# §4.2 / §4.6 — Successful swap (substrate path) + session-identity propagation
# ---------------------------------------------------------------------------


def test_successful_swap_substrate_caller(monkeypatch, tmp_path):
    """§4.6 — successful as_agent sets session-identity state and returns
    full canonical response shape.
    """
    _seed_target_token(
        monkeypatch,
        agent_name="athena-acme",
        scope="tenant-agent",
        tenant_slug="acme",
        agent_kind="athena",
    )
    _stub_qnft_registry(monkeypatch, {
        "athena-acme": {
            "seed_hex": "deadbeef" * 8,
            "minted_at": "2026-05-04T23:00:00Z",
            "cause": "I serve acme only.",
        }
    })
    _stub_scaffold_path(
        monkeypatch, tmp_path,
        tenant_slug="acme", agent_kind="athena",
        content="# Athena-acme scaffold\n",
    )
    _stub_emit_audit(monkeypatch)
    _stub_mirror(monkeypatch, rows=[])

    auth = _ctx_substrate("kasra")
    result = _run(_handle_as_agent(auth, {"name": "athena-acme"}, "sess-9"))
    body = _parse_text_payload(result)

    assert body["ok"] is True
    assert body["agent_name"] == "athena-acme"
    assert body["tenant_slug"] == "acme"
    assert body["agent_kind"] == "athena"
    assert body["qnft_seed_hex"] == "deadbeef" * 8
    assert body["scaffold_loaded"] is True
    assert "Athena-acme scaffold" in body["scaffold_content"]
    assert body["cause_loaded"] is True
    assert body["cause_source"] == "registry"
    assert body["cause_content"] == "I serve acme only."
    assert body["session_identity_set"] is True
    assert body["session_id"] == "sess-9"

    # Session-identity propagation — agent_scope now resolves to swapped name.
    assert auth.as_agent_active is True
    assert auth.as_agent_name == "athena-acme"
    assert auth.as_agent_kind == "athena"
    assert auth.as_agent_tenant_slug == "acme"
    assert auth.agent_scope == "athena-acme"
    # Module-level mirror is populated for cross-handler lookup.
    assert "sess-9" in _session_as_agent
    assert _session_as_agent["sess-9"]["as_agent_name"] == "athena-acme"


def test_successful_swap_tenant_admin_owner(monkeypatch, tmp_path):
    """tenant-admin owner targeting their own tenant's agent succeeds."""
    _seed_target_token(
        monkeypatch,
        agent_name="kasra-acme",
        scope="tenant-agent",
        tenant_slug="acme",
        agent_kind="kasra",
    )
    _stub_qnft_registry(monkeypatch, {
        "kasra-acme": {"seed_hex": "ab" * 32, "minted_at": "2026-05-04T23:00:00Z"}
    })
    _stub_scaffold_path(
        monkeypatch, tmp_path, tenant_slug="acme", agent_kind="kasra"
    )
    _stub_emit_audit(monkeypatch)
    _stub_mirror(monkeypatch, rows=[])

    auth = _ctx_tenant_admin(tenant_slug="acme", role="owner")
    result = _run(_handle_as_agent(auth, {"name": "kasra-acme"}, "sess-10"))
    body = _parse_text_payload(result)

    assert body["ok"] is True
    assert body["tenant_slug"] == "acme"
    assert auth.as_agent_active is True


# ---------------------------------------------------------------------------
# §3.8 / §4.4 — L-2 Mirror engram tenant-scope filter
# ---------------------------------------------------------------------------


def test_mirror_engram_excludes_cross_tenant_rows(monkeypatch, tmp_path):
    """§3.8 — synthetic engram with matching agent_name but project=other
    MUST be excluded from recent_engrams (defensive double-filter).
    """
    _seed_target_token(
        monkeypatch,
        agent_name="athena-acme",
        scope="tenant-agent",
        tenant_slug="acme",
        agent_kind="athena",
    )
    _stub_qnft_registry(monkeypatch, {"athena-acme": {"seed_hex": "x" * 64}})
    _stub_scaffold_path(monkeypatch, tmp_path, tenant_slug="acme", agent_kind="athena")
    _stub_emit_audit(monkeypatch)

    rows = [
        {"agent": "athena-acme", "project": "acme", "text": "real engram"},
        {"agent": "athena-acme", "project": "other", "text": "leak attempt"},  # synthetic
        {"agent": "athena-acme", "project": "acme", "text": "another real engram"},
    ]
    _stub_mirror(monkeypatch, rows=rows)

    auth = _ctx_substrate("kasra")
    result = _run(_handle_as_agent(auth, {"name": "athena-acme"}, "sess-11"))
    body = _parse_text_payload(result)

    assert body["ok"] is True
    # The leak-attempt row MUST NOT appear in the response.
    projects = {r.get("project") for r in body["recent_engrams"]}
    assert projects == {"acme"}
    assert all(r["project"] == "acme" for r in body["recent_engrams"])
    assert len(body["recent_engrams"]) == 2


def test_mirror_failure_does_not_block_swap(monkeypatch, tmp_path):
    """Mirror failure → recent_engrams=[] but swap still succeeds (read-side
    is informational; only blocks if scaffold/auth fail).
    """
    _seed_target_token(
        monkeypatch,
        agent_name="athena-acme",
        scope="tenant-agent",
        tenant_slug="acme",
        agent_kind="athena",
    )
    _stub_qnft_registry(monkeypatch, {"athena-acme": {"seed_hex": "x" * 64}})
    _stub_scaffold_path(monkeypatch, tmp_path, tenant_slug="acme", agent_kind="athena")
    _stub_emit_audit(monkeypatch)
    _stub_mirror(monkeypatch, raises=RuntimeError("Mirror down"))

    auth = _ctx_substrate("kasra")
    result = _run(_handle_as_agent(auth, {"name": "athena-acme"}, "sess-12"))
    body = _parse_text_payload(result)

    assert body["ok"] is True
    assert body["recent_engrams"] == []


# ---------------------------------------------------------------------------
# §3.9 / §4.5 — L-3 Scaffold-missing failure mode
# ---------------------------------------------------------------------------


def test_scaffold_missing_returns_named_error(monkeypatch, tmp_path):
    """§3.9 — scaffold deleted + template also deleted → `scaffold_missing`."""
    _seed_target_token(
        monkeypatch,
        agent_name="athena-acme",
        scope="tenant-agent",
        tenant_slug="acme",
        agent_kind="athena",
    )
    _stub_qnft_registry(monkeypatch, {"athena-acme": {"seed_hex": "x" * 64}})
    _stub_emit_audit(monkeypatch)
    _stub_mirror(monkeypatch, rows=[])

    # Point Path.home() at empty tmp_path — no scaffold exists.
    home_root = tmp_path / "home"
    home_root.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home_root)

    # Force scaffold_or_skip_agent_fork to raise (simulates template missing).
    from sos.bus import tenant_agent_activation as ta_mod

    def _raise_template_missing(**kwargs):
        from sos.bus.tenant_agent_activation import ProvisionError
        raise ProvisionError(500, "template_missing", "agent-fork template missing")

    monkeypatch.setattr(ta_mod, "scaffold_or_skip_agent_fork", _raise_template_missing)
    monkeypatch.setattr(
        ta_mod, "_resolve_tenant_metadata", lambda slug: (slug, "general")
    )

    auth = _ctx_substrate("kasra")
    result = _run(_handle_as_agent(auth, {"name": "athena-acme"}, "sess-13"))
    body = _parse_text_payload(result)

    assert body["ok"] is False
    assert body["error"] == "scaffold_missing"
    assert body["kind"] == "athena"
    # Session-identity is NOT mutated on failure.
    assert auth.as_agent_active is False


def test_scaffold_re_render_path_succeeds(monkeypatch, tmp_path):
    """§3.9 / §4.5 — scaffold deleted but template available → re-render and
    succeed. Verifies the L-3 idempotent re-render reuses
    scaffold_or_skip_agent_fork from D-2b (no duplication).
    """
    _seed_target_token(
        monkeypatch,
        agent_name="athena-acme",
        scope="tenant-agent",
        tenant_slug="acme",
        agent_kind="athena",
    )
    _stub_qnft_registry(monkeypatch, {"athena-acme": {"seed_hex": "x" * 64}})
    _stub_emit_audit(monkeypatch)
    _stub_mirror(monkeypatch, rows=[])

    home_root = tmp_path / "home"
    home_root.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home_root)
    rendered_path = (
        home_root / ".mumega" / "customers" / "acme" / "agents" / "athena" / "CLAUDE.md"
    )

    from sos.bus import tenant_agent_activation as ta_mod

    def _re_render(**kwargs):
        rendered_path.parent.mkdir(parents=True, exist_ok=True)
        rendered_path.write_text("# re-rendered scaffold\n", encoding="utf-8")
        return rendered_path, True

    monkeypatch.setattr(ta_mod, "scaffold_or_skip_agent_fork", _re_render)
    monkeypatch.setattr(
        ta_mod, "_resolve_tenant_metadata", lambda slug: (slug, "general")
    )

    auth = _ctx_substrate("kasra")
    result = _run(_handle_as_agent(auth, {"name": "athena-acme"}, "sess-14"))
    body = _parse_text_payload(result)

    assert body["ok"] is True
    assert body["scaffold_loaded"] is True
    assert "re-rendered scaffold" in body["scaffold_content"]


# ---------------------------------------------------------------------------
# §3.7 / §4.7 — L-5 Audit row mandatory; failure does not block
# ---------------------------------------------------------------------------


def test_audit_emitted_on_successful_swap(monkeypatch, tmp_path):
    """§4.7 — every successful as_agent emits actor_type=session_identity_swap."""
    _seed_target_token(
        monkeypatch,
        agent_name="athena-acme",
        scope="tenant-agent",
        tenant_slug="acme",
        agent_kind="athena",
    )
    _stub_qnft_registry(monkeypatch, {"athena-acme": {"seed_hex": "x" * 64}})
    _stub_scaffold_path(monkeypatch, tmp_path, tenant_slug="acme", agent_kind="athena")
    events = _stub_emit_audit(monkeypatch)
    _stub_mirror(monkeypatch, rows=[])

    auth = _ctx_substrate("kasra")
    _run(_handle_as_agent(auth, {"name": "athena-acme"}, "sess-15"))

    # Yield the asyncio loop briefly so the create_task fires the recorder.
    _run(asyncio.sleep(0))

    assert any(
        getattr(e, "actor_type", None) == "session_identity_swap"
        and getattr(e, "action", None) == "mcp.as_agent"
        for e in events
    ), f"expected audit row not emitted; events={events}"


def test_audit_emitted_for_substrate_callers(monkeypatch, tmp_path):
    """L-5 substrate clause — scope='' callers ALSO emit audit (higher
    privilege = MORE traceability).
    """
    _seed_target_token(
        monkeypatch,
        agent_name="athena-acme",
        scope="tenant-agent",
        tenant_slug="acme",
        agent_kind="athena",
    )
    _stub_qnft_registry(monkeypatch, {"athena-acme": {"seed_hex": "x" * 64}})
    _stub_scaffold_path(monkeypatch, tmp_path, tenant_slug="acme", agent_kind="athena")
    events = _stub_emit_audit(monkeypatch)
    _stub_mirror(monkeypatch, rows=[])

    auth = _ctx_substrate("kasra")  # scope=""
    _run(_handle_as_agent(auth, {"name": "athena-acme"}, "sess-15b"))
    _run(asyncio.sleep(0))

    assert any(
        getattr(e, "payload", {}).get("from_agent") == "kasra"
        and getattr(e, "payload", {}).get("to_agent") == "athena-acme"
        for e in events
    ), f"substrate-caller audit row missing; events={events}"


def test_audit_failure_does_not_block_swap(monkeypatch, tmp_path):
    """§3.7 — _emit_audit raising must NOT block as_agent; swap still in effect."""
    _seed_target_token(
        monkeypatch,
        agent_name="athena-acme",
        scope="tenant-agent",
        tenant_slug="acme",
        agent_kind="athena",
    )
    _stub_qnft_registry(monkeypatch, {"athena-acme": {"seed_hex": "x" * 64}})
    _stub_scaffold_path(monkeypatch, tmp_path, tenant_slug="acme", agent_kind="athena")
    _stub_mirror(monkeypatch, rows=[])

    from sos.mcp import sos_mcp_sse as module

    async def _raise_audit(event):
        raise RuntimeError("audit chain down")

    monkeypatch.setattr(module, "_emit_audit", _raise_audit)

    auth = _ctx_substrate("kasra")
    result = _run(_handle_as_agent(auth, {"name": "athena-acme"}, "sess-16"))
    body = _parse_text_payload(result)

    # Swap proceeds despite audit failure.
    assert body["ok"] is True
    assert body["session_identity_set"] is True
    assert auth.as_agent_active is True


# ---------------------------------------------------------------------------
# §3.10 / §4.8 — L-4 Per-SSE isolation (session bleed)
# ---------------------------------------------------------------------------


def test_two_parallel_sessions_no_state_bleed(monkeypatch, tmp_path):
    """§3.10 — Two parallel SSE sessions A, B sharing the same TOKEN.
    A calls as_agent('athena-acme'). B's session must NOT have as_agent_active.
    Verifies session_id is per-CONNECTION (uuid4 at SSE accept), not per-token.
    """
    _seed_target_token(
        monkeypatch,
        agent_name="athena-acme",
        scope="tenant-agent",
        tenant_slug="acme",
        agent_kind="athena",
    )
    _stub_qnft_registry(monkeypatch, {"athena-acme": {"seed_hex": "x" * 64}})
    _stub_scaffold_path(monkeypatch, tmp_path, tenant_slug="acme", agent_kind="athena")
    _stub_emit_audit(monkeypatch)
    _stub_mirror(monkeypatch, rows=[])

    # SAME TOKEN, distinct session_ids — simulates two SSE connections sharing
    # one Bearer (e.g. browser tab + claude.ai opening the same agent).
    SHARED_TOKEN = "shared-bearer-token"
    auth_a = MCPAuthContext(
        token=SHARED_TOKEN, tenant_id="sos", is_system=False, source="test",
        agent_name="kasra", scope="",
    )
    auth_b = MCPAuthContext(
        token=SHARED_TOKEN, tenant_id="sos", is_system=False, source="test",
        agent_name="kasra", scope="",
    )

    # Conn A swaps; Conn B does not.
    _run(_handle_as_agent(auth_a, {"name": "athena-acme"}, "sess-A"))

    # Conn A's auth has the swap; Conn B's auth is independent (stays default).
    assert auth_a.as_agent_active is True
    assert auth_a.agent_scope == "athena-acme"
    assert auth_b.as_agent_active is False
    assert auth_b.agent_scope == "kasra"

    # Module-level dict reflects only Conn A.
    assert "sess-A" in _session_as_agent
    assert "sess-B" not in _session_as_agent


# ---------------------------------------------------------------------------
# §3.11 — Reset path
# ---------------------------------------------------------------------------


def test_reset_clears_session_state(monkeypatch, tmp_path):
    """§3.11 — as_agent({name: ''}) clears all swap state and emits reset audit."""
    _seed_target_token(
        monkeypatch,
        agent_name="athena-acme",
        scope="tenant-agent",
        tenant_slug="acme",
        agent_kind="athena",
    )
    _stub_qnft_registry(monkeypatch, {"athena-acme": {"seed_hex": "x" * 64}})
    _stub_scaffold_path(monkeypatch, tmp_path, tenant_slug="acme", agent_kind="athena")
    events = _stub_emit_audit(monkeypatch)
    _stub_mirror(monkeypatch, rows=[])

    auth = _ctx_substrate("kasra")
    _run(_handle_as_agent(auth, {"name": "athena-acme"}, "sess-17"))
    assert auth.as_agent_active is True
    assert "sess-17" in _session_as_agent

    # Reset
    result = _run(_handle_as_agent(auth, {"name": ""}, "sess-17"))
    body = _parse_text_payload(result)
    _run(asyncio.sleep(0))

    assert body["ok"] is True
    assert body["reset"] is True
    assert body["session_identity_set"] is False
    # Auth + module-level state cleared.
    assert auth.as_agent_active is False
    assert auth.as_agent_name is None
    assert "sess-17" not in _session_as_agent
    # agent_scope reverts to caller's default identity.
    assert auth.agent_scope == "kasra"
    # Reset audit row emitted.
    assert any(
        getattr(e, "action", None) == "mcp.as_agent.reset"
        for e in events
    ), f"reset audit row missing; events={events}"


def test_reset_idempotent_when_no_swap_active(monkeypatch, tmp_path):
    """Reset call when no swap is active is a no-op success (idempotent)."""
    _empty_token_cache(monkeypatch)
    _stub_emit_audit(monkeypatch)
    auth = _ctx_substrate("kasra")
    result = _run(_handle_as_agent(auth, {"name": ""}, "sess-18"))
    body = _parse_text_payload(result)
    assert body["ok"] is True
    assert body["reset"] is True
    assert auth.as_agent_active is False


# ---------------------------------------------------------------------------
# §3.12 — sign_out clears as_agent state
# ---------------------------------------------------------------------------


def test_sign_out_clears_as_agent_state(monkeypatch, tmp_path):
    """§3.12 — sign_out → as_agent state cleared alongside active_project."""
    _seed_target_token(
        monkeypatch,
        agent_name="athena-acme",
        scope="tenant-agent",
        tenant_slug="acme",
        agent_kind="athena",
    )
    _stub_qnft_registry(monkeypatch, {"athena-acme": {"seed_hex": "x" * 64}})
    _stub_scaffold_path(monkeypatch, tmp_path, tenant_slug="acme", agent_kind="athena")
    _stub_emit_audit(monkeypatch)
    _stub_mirror(monkeypatch, rows=[])

    auth = _ctx_substrate("kasra")
    _run(_handle_as_agent(auth, {"name": "athena-acme"}, "sess-19"))
    assert auth.as_agent_active is True

    # Invoke sign_out — must clear as_agent state.
    from sos.mcp.sos_mcp_sse import _handle_sign_out
    _run(_handle_sign_out(auth, "sess-19"))

    assert auth.as_agent_active is False
    assert auth.as_agent_name is None
    assert "sess-19" not in _session_as_agent
