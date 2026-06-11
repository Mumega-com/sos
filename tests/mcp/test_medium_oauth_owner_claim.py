"""MEDIUM-tier verified-owner elevation + claim-by-name (§2.3 / §2.4).

Unit proof for the VPS side of the verified-owner identity model. Mocks the
inkwell-api resolve-owner call (no network) and the _peer_tenant_meta RLS lookup
(no tokens.json), then asserts the scope-elevation and claim ordering invariants
that close the adversarial findings:

  BLOCK-2  — elevation is driven by server-side resolve_owner(sub), never a bare
             X-Agent-Identity-Id header.
  BLOCK-3  — claim routes through _peer_tenant_meta RLS FIRST; qNFT presence is
             never an authorization input; a novel name hits NO mint path.
  BLOCK-4  — sub_matched=false (NULL-sub / unverified / mismatch) never elevates.
"""
from __future__ import annotations

import asyncio


class _Req:
    """Minimal Starlette-Request stand-in: only .headers is read."""

    def __init__(self, headers: dict[str, str]):
        self.headers = headers


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _customer_oauth_ctx(module, **over):
    """A base worker_oauth customer context (the pre-elevation shape)."""
    ctx = module.MCPAuthContext(
        token="deadbeef",
        tenant_id="free-tenant-xyz",
        is_system=False,
        source="worker_oauth",
        scope="customer",
        plan="free",
        email=over.get("email", "hadi@mumega.com"),
        email_verified=over.get("email_verified", True),
        agent_identity_id=over.get("agent_identity_id", "idn_hadi"),
    )
    return ctx


# ─────────────────────────────────────────── §2.3 elevation ──────────────────

def test_verified_owner_elevates_to_tenant_scope(monkeypatch):
    from sos.mcp import sos_mcp_sse as m

    monkeypatch.setattr(m, "SECURITY_MODE", "medium")

    async def fake_resolve(provider, sub, email, email_verified):
        return {
            "identity_id": "idn_hadi",
            "found_via": "idp_sub",
            "sub_matched": True,
            "person_email": "hadi@mumega.com",
            "memberships": [
                {"project_id": "com-mumega", "role": "owner"},
                {"project_id": "viamar", "role": "viewer"},
            ],
        }

    monkeypatch.setattr(m, "_resolve_owner", fake_resolve)

    ctx = _customer_oauth_ctx(m)
    req = _Req({
        "X-Idp-Provider": "google",
        "X-Idp-Sub": "google-sub-hadi",
        "X-Email-Verified": "true",
    })
    _run(m._elevate_verified_owner_if_eligible(ctx, req))

    assert ctx.scope == "tenant"
    assert ctx.role == "owner"
    assert ctx.source == "worker_oauth_owner"
    assert ctx.identity_id == "idn_hadi"
    # highest-membership project becomes the session default
    assert ctx.tenant_id == "com-mumega"
    assert ctx.is_customer is False
    assert ctx.owner_memberships is not None and len(ctx.owner_memberships) == 2


def test_unknown_principal_stays_customer(monkeypatch):
    from sos.mcp import sos_mcp_sse as m
    monkeypatch.setattr(m, "SECURITY_MODE", "medium")

    async def fake_resolve(*a, **k):
        return {"identity_id": None, "sub_matched": False, "memberships": []}
    monkeypatch.setattr(m, "_resolve_owner", fake_resolve)

    ctx = _customer_oauth_ctx(m, email="stranger@nowhere.com")
    req = _Req({"X-Idp-Provider": "google", "X-Idp-Sub": "x", "X-Email-Verified": "true"})
    _run(m._elevate_verified_owner_if_eligible(ctx, req))

    assert ctx.scope == "customer"  # WALL STAYS — verbatim customer path
    assert ctx.is_customer is True
    assert ctx.owner_memberships is None


def test_unverified_email_never_elevates(monkeypatch):
    from sos.mcp import sos_mcp_sse as m
    monkeypatch.setattr(m, "SECURITY_MODE", "medium")

    called = {"resolve": False}

    async def fake_resolve(*a, **k):
        called["resolve"] = True
        return {"sub_matched": True, "memberships": [{"project_id": "p", "role": "owner"}]}
    monkeypatch.setattr(m, "_resolve_owner", fake_resolve)

    ctx = _customer_oauth_ctx(m, email_verified=False)
    req = _Req({"X-Idp-Provider": "google", "X-Idp-Sub": "x", "X-Email-Verified": "false"})
    _run(m._elevate_verified_owner_if_eligible(ctx, req))

    assert ctx.scope == "customer"
    # short-circuited BEFORE the resolver — no resolve call on unverified email
    assert called["resolve"] is False


def test_low_role_membership_does_not_elevate(monkeypatch):
    from sos.mcp import sos_mcp_sse as m
    monkeypatch.setattr(m, "SECURITY_MODE", "medium")

    async def fake_resolve(*a, **k):
        return {
            "identity_id": "idn_v",
            "sub_matched": True,
            "memberships": [{"project_id": "p", "role": "viewer"}],
        }
    monkeypatch.setattr(m, "_resolve_owner", fake_resolve)

    ctx = _customer_oauth_ctx(m)
    req = _Req({"X-Idp-Provider": "google", "X-Idp-Sub": "x", "X-Email-Verified": "true"})
    _run(m._elevate_verified_owner_if_eligible(ctx, req))

    assert ctx.scope == "customer"  # viewer-only → not enough for tenant scope


def test_low_security_mode_disables_elevation(monkeypatch):
    from sos.mcp import sos_mcp_sse as m
    monkeypatch.setattr(m, "SECURITY_MODE", "low")

    called = {"resolve": False}

    async def fake_resolve(*a, **k):
        called["resolve"] = True
        return {"sub_matched": True, "memberships": [{"project_id": "p", "role": "owner"}]}
    monkeypatch.setattr(m, "_resolve_owner", fake_resolve)

    ctx = _customer_oauth_ctx(m)
    req = _Req({"X-Idp-Provider": "google", "X-Idp-Sub": "x", "X-Email-Verified": "true"})
    _run(m._elevate_verified_owner_if_eligible(ctx, req))

    assert ctx.scope == "customer"
    assert called["resolve"] is False


def test_resolver_error_fails_closed(monkeypatch):
    from sos.mcp import sos_mcp_sse as m
    monkeypatch.setattr(m, "SECURITY_MODE", "medium")

    async def fake_resolve(*a, **k):
        return None  # network error / non-200 → fail-closed
    monkeypatch.setattr(m, "_resolve_owner", fake_resolve)

    ctx = _customer_oauth_ctx(m)
    req = _Req({"X-Idp-Provider": "google", "X-Idp-Sub": "x", "X-Email-Verified": "true"})
    _run(m._elevate_verified_owner_if_eligible(ctx, req))

    assert ctx.scope == "customer"


def test_already_elevated_context_is_skipped(monkeypatch):
    from sos.mcp import sos_mcp_sse as m
    monkeypatch.setattr(m, "SECURITY_MODE", "medium")

    called = {"resolve": False}

    async def fake_resolve(*a, **k):
        called["resolve"] = True
        return {"sub_matched": True, "memberships": []}
    monkeypatch.setattr(m, "_resolve_owner", fake_resolve)

    ctx = _customer_oauth_ctx(m)
    ctx.scope = "tenant"  # already elevated (or a real tenant token)
    req = _Req({"X-Idp-Provider": "google", "X-Idp-Sub": "x", "X-Email-Verified": "true"})
    _run(m._elevate_verified_owner_if_eligible(ctx, req))

    assert called["resolve"] is False  # idempotent — no re-resolution


# ─────────────────────────────────────────── §2.4 claim-by-name ──────────────

def _owner_ctx(m):
    return m.MCPAuthContext(
        token="x", tenant_id="com-mumega", is_system=False,
        source="worker_oauth_owner", scope="tenant", role="owner",
        tenant_slug="com-mumega",
    )


def test_claim_customer_scope_forbidden(monkeypatch):
    from sos.mcp import sos_mcp_sse as m
    ctx = _customer_oauth_ctx(m)  # scope=customer
    out = _run(m._handle_claim(ctx, {"name": "bishno"}, None))
    assert "customer_token_forbidden" in out["content"][0]["text"]


def test_claim_novel_name_no_qnft_hits_river_gate_not_mint(monkeypatch):
    """(d) A name that PASSES RLS but has NO qNFT → qnft_required. NO mint call."""
    from sos.mcp import sos_mcp_sse as m

    # RLS passes: name resolves to a tenant-agent token in THIS tenant.
    monkeypatch.setattr(
        m, "_peer_tenant_meta",
        lambda name: ("tenant-agent", "com-mumega", "envoy"),
    )
    # Registry has NO entry for the novel name.
    monkeypatch.setattr(m, "_load_qnft_registry_for_as_agent", lambda: {})

    # Guard: as_agent (which contains the scaffold path) must NOT be reached.
    async def explode(*a, **k):
        raise AssertionError("as_agent must NOT run for a no-qNFT name")
    monkeypatch.setattr(m, "_handle_as_agent", explode)

    ctx = _owner_ctx(m)
    out = _run(m._handle_claim(ctx, {"name": "newname"}, None))
    txt = out["content"][0]["text"]
    assert "qnft_required" in txt
    assert "River authorization" in txt


def test_claim_name_without_tenant_token_blocked_by_rls(monkeypatch):
    """(f) A name with NO tenant-agent token → not_authorized (RLS first)."""
    from sos.mcp import sos_mcp_sse as m

    # _peer_tenant_meta returns None — no active token claims this name.
    monkeypatch.setattr(m, "_peer_tenant_meta", lambda name: None)
    # Even if a qNFT existed, it must not be consulted before RLS.
    monkeypatch.setattr(
        m, "_load_qnft_registry_for_as_agent",
        lambda: {"bishno": {"agent": "bishno"}},
    )

    ctx = _owner_ctx(m)
    out = _run(m._handle_claim(ctx, {"name": "bishno"}, None))
    # tenant-admin caller → not_authorized (oracle-leak-safe)
    assert "not_authorized" in out["content"][0]["text"]


def test_claim_cross_tenant_blocked(monkeypatch):
    """(f) Name resolves to a tenant-agent in a DIFFERENT tenant → not_authorized."""
    from sos.mcp import sos_mcp_sse as m

    monkeypatch.setattr(
        m, "_peer_tenant_meta",
        lambda name: ("tenant-agent", "some-other-tenant", "envoy"),
    )
    monkeypatch.setattr(
        m, "_load_qnft_registry_for_as_agent",
        lambda: {"bishno": {"agent": "bishno"}},
    )

    ctx = _owner_ctx(m)  # tenant_id=com-mumega
    out = _run(m._handle_claim(ctx, {"name": "bishno"}, None))
    assert "not_authorized" in out["content"][0]["text"]


def test_claim_existing_qnft_binds_via_as_agent(monkeypatch):
    """(c) Owner claims a name that passes RLS AND has a qNFT → delegates to as_agent."""
    from sos.mcp import sos_mcp_sse as m

    monkeypatch.setattr(
        m, "_peer_tenant_meta",
        lambda name: ("tenant-agent", "com-mumega", "envoy"),
    )
    monkeypatch.setattr(
        m, "_load_qnft_registry_for_as_agent",
        lambda: {"bishno": {"agent": "bishno", "seed_hex": "abc"}},
    )

    captured = {}

    async def fake_as_agent(auth, args, session_id):
        captured["name"] = args.get("name")
        return m._text('{"ok": true, "bound": "bishno"}')
    monkeypatch.setattr(m, "_handle_as_agent", fake_as_agent)

    ctx = _owner_ctx(m)
    out = _run(m._handle_claim(ctx, {"name": "bishno"}, None))
    assert captured["name"] == "bishno"
    assert "bound" in out["content"][0]["text"]
