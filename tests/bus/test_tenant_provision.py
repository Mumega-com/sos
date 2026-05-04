"""
S027 D-1b — Tests for tenant_provisioning module + bridge.py endpoint shape.

LOCK invariants verified (4):
  - LOCK-D-1b-internal-bearer-fail-closed
  - LOCK-D-1b-mirror-key-idempotent
  - LOCK-D-1b-bus-token-idempotent
  - LOCK-D-1b-scaffold-idempotent

Hermetic discipline: each test uses monkeypatched paths to tmp dirs (no global state leak).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from sos.bus import tenant_provisioning as tp
from sos.bus.tenant_provisioning import ProvisionError


# -----------------------------------------------------------------------
# Fixtures — hermetic tmp paths, env clearing
# -----------------------------------------------------------------------
@pytest.fixture
def tmp_substrate(tmp_path, monkeypatch):
    """Fresh substrate state per test: tmp tokens.json, mirror_keys.json, customers/, templates/."""
    tokens_path = tmp_path / "tokens.json"
    mirror_keys_path = tmp_path / "mirror_keys.json"
    customers_dir = tmp_path / "customers"
    templates_dir = tmp_path / "templates" / "customer"

    # Seed templates dir with minimal valid templates
    templates_dir.mkdir(parents=True)
    (templates_dir / "CLAUDE.md").write_text("# {{DISPLAY_NAME}}\nslug={{TENANT_SLUG}}\nindustry={{INDUSTRY}}\n")
    (templates_dir / "README.md").write_text("# Tenant {{TENANT_SLUG}}\n")
    (templates_dir / ".env.example").write_text("TENANT_SLUG={{TENANT_SLUG}}\n")
    (templates_dir / ".gitignore").write_text(".env\n")

    monkeypatch.setattr(tp, "TOKENS_PATH", tokens_path)
    monkeypatch.setattr(tp, "MIRROR_KEYS_PATH", mirror_keys_path)
    monkeypatch.setattr(tp, "CUSTOMERS_DIR", customers_dir)
    monkeypatch.setattr(tp, "TEMPLATES_DIR", templates_dir)

    return {
        "tokens_path": tokens_path,
        "mirror_keys_path": mirror_keys_path,
        "customers_dir": customers_dir,
        "templates_dir": templates_dir,
    }


@pytest.fixture
def secret_set(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", "test-secret-xyz")
    return "test-secret-xyz"


@pytest.fixture
def secret_unset(monkeypatch):
    monkeypatch.delenv("INTERNAL_API_SECRET", raising=False)


# -----------------------------------------------------------------------
# LOCK-D-1b-internal-bearer-fail-closed — 6 tests
# -----------------------------------------------------------------------
class TestInternalBearerFailClosed:
    def test_missing_env_returns_none(self, secret_unset):
        assert tp.get_internal_secret() is None

    def test_empty_env_returns_none(self, monkeypatch):
        monkeypatch.setenv("INTERNAL_API_SECRET", "")
        assert tp.get_internal_secret() is None

    def test_set_env_returns_secret(self, secret_set):
        assert tp.get_internal_secret() == "test-secret-xyz"

    def test_authenticate_bearer_fails_when_env_missing(self, secret_unset):
        # Even with valid-looking header, fails closed when env unset
        assert tp.authenticate_bearer("Bearer test-secret-xyz") is False

    def test_authenticate_bearer_fails_on_missing_header(self, secret_set):
        assert tp.authenticate_bearer("") is False
        assert tp.authenticate_bearer("Token test-secret-xyz") is False

    def test_authenticate_bearer_fails_on_wrong_token(self, secret_set):
        assert tp.authenticate_bearer("Bearer wrong-token") is False

    def test_authenticate_bearer_succeeds_on_match(self, secret_set):
        assert tp.authenticate_bearer("Bearer test-secret-xyz") is True

    def test_constant_time_equal_basics(self):
        assert tp.constant_time_equal("a", "a") is True
        assert tp.constant_time_equal("a", "b") is False
        assert tp.constant_time_equal("", "") is True
        assert tp.constant_time_equal("a", "ab") is False
        # Type guard
        assert tp.constant_time_equal(None, "a") is False  # type: ignore[arg-type]


# -----------------------------------------------------------------------
# Body validation — 8 tests
# -----------------------------------------------------------------------
class TestValidateProvisionBody:
    def _valid(self):
        return {
            "tenant_id": "t-abc-123",
            "slug": "acme-corp-x9",
            "display_name": "Acme Corporation",
            "industry": "saas",
        }

    def test_valid_passes(self):
        out = tp.validate_provision_body(self._valid())
        assert out["slug"] == "acme-corp-x9"

    def test_non_dict_body(self):
        with pytest.raises(ProvisionError) as e:
            tp.validate_provision_body("not a dict")  # type: ignore[arg-type]
        assert e.value.status == 422
        assert e.value.code == "invalid_body"

    def test_missing_tenant_id(self):
        b = self._valid()
        del b["tenant_id"]
        with pytest.raises(ProvisionError) as e:
            tp.validate_provision_body(b)
        assert e.value.code == "invalid_tenant_id"

    def test_bad_slug_uppercase(self):
        b = self._valid()
        b["slug"] = "AcmeCorp"
        with pytest.raises(ProvisionError) as e:
            tp.validate_provision_body(b)
        assert e.value.code == "invalid_slug"

    def test_bad_slug_starts_with_dash(self):
        b = self._valid()
        b["slug"] = "-acme"
        with pytest.raises(ProvisionError) as e:
            tp.validate_provision_body(b)
        assert e.value.code == "invalid_slug"

    def test_display_name_trimmed(self):
        b = self._valid()
        b["display_name"] = "   Acme Corp   "
        out = tp.validate_provision_body(b)
        assert out["display_name"] == "Acme Corp"

    def test_display_name_too_long(self):
        b = self._valid()
        b["display_name"] = "x" * 201
        with pytest.raises(ProvisionError) as e:
            tp.validate_provision_body(b)
        assert e.value.code == "invalid_display_name"

    def test_bad_industry(self):
        b = self._valid()
        b["industry"] = "Bad-Industry"  # uppercase + dash not in regex
        with pytest.raises(ProvisionError) as e:
            tp.validate_provision_body(b)
        assert e.value.code == "invalid_industry"


# -----------------------------------------------------------------------
# LOCK-D-1b-mirror-key-idempotent — 5 tests
# -----------------------------------------------------------------------
class TestMirrorKeyIdempotent:
    def test_fresh_mint_creates_file(self, tmp_substrate):
        key, minted = tp.mint_or_get_mirror_key("acme", "Acme Corp")
        assert minted is True
        assert key.startswith("sk-mumega-acme-")
        assert tmp_substrate["mirror_keys_path"].exists()
        data = json.loads(tmp_substrate["mirror_keys_path"].read_text())
        assert len(data) == 1
        assert data[0]["agent_slug"] == "acme"

    def test_idempotent_returns_existing(self, tmp_substrate):
        key1, minted1 = tp.mint_or_get_mirror_key("acme", "Acme Corp")
        key2, minted2 = tp.mint_or_get_mirror_key("acme", "Acme Corp")
        assert minted1 is True
        assert minted2 is False
        assert key1 == key2
        # No duplicate appended
        data = json.loads(tmp_substrate["mirror_keys_path"].read_text())
        assert len(data) == 1

    def test_legacy_partial_state_finds_existing(self, tmp_substrate):
        # Pre-seed with an existing mirror key as if from manual setup
        seed = [{
            "key": "sk-mumega-legacy-deadbeef",
            "agent_slug": "legacy-tenant",
            "created_at": "2026-04-01T00:00:00+00:00",
            "active": True,
            "label": "legacy",
        }]
        tmp_substrate["mirror_keys_path"].write_text(json.dumps(seed))
        key, minted = tp.mint_or_get_mirror_key("legacy-tenant", "Legacy")
        assert minted is False
        assert key == "sk-mumega-legacy-deadbeef"

    def test_inactive_record_skipped(self, tmp_substrate):
        seed = [{
            "key": "sk-mumega-old-key",
            "agent_slug": "acme",
            "created_at": "2026-04-01T00:00:00+00:00",
            "active": False,
            "label": "deactivated",
        }]
        tmp_substrate["mirror_keys_path"].write_text(json.dumps(seed))
        key, minted = tp.mint_or_get_mirror_key("acme", "Acme")
        assert minted is True
        assert key != "sk-mumega-old-key"

    def test_corrupt_json_treated_as_empty(self, tmp_substrate):
        tmp_substrate["mirror_keys_path"].write_text("{not valid json")
        key, minted = tp.mint_or_get_mirror_key("acme", "Acme")
        assert minted is True
        # Corrupt file overwritten with valid array
        data = json.loads(tmp_substrate["mirror_keys_path"].read_text())
        assert isinstance(data, list)
        assert len(data) == 1


# -----------------------------------------------------------------------
# LOCK-D-1b-bus-token-idempotent — 6 tests
# -----------------------------------------------------------------------
class TestBusTokenIdempotent:
    def test_fresh_mint_creates_file(self, tmp_substrate):
        token, minted = tp.mint_or_get_bus_token("acme", "Acme Corp")
        assert minted is True
        assert token.startswith("sk-acme-admin-")
        data = json.loads(tmp_substrate["tokens_path"].read_text())
        assert len(data) == 1
        rec = data[0]
        assert rec["agent"] == "acme-admin"
        assert rec["scope"] == "tenant"
        assert rec["role"] == "owner"
        assert rec["project"] == "acme"
        assert "token_hash" in rec

    def test_idempotent_returns_existing_plaintext(self, tmp_substrate):
        t1, m1 = tp.mint_or_get_bus_token("acme", "Acme")
        t2, m2 = tp.mint_or_get_bus_token("acme", "Acme")
        assert m1 is True
        assert m2 is False
        assert t1 == t2
        data = json.loads(tmp_substrate["tokens_path"].read_text())
        # Only one tenant-admin record
        admins = [r for r in data if r.get("agent") == "acme-admin" and r.get("scope") == "tenant"]
        assert len(admins) == 1

    def test_existing_record_without_plaintext_fails_loud(self, tmp_substrate):
        # Historic record with only token_hash — cannot return identity, must fail loud
        seed = [{
            "token_hash": "abc123",
            "project": "acme",
            "label": "Acme",
            "active": True,
            "created_at": "2026-04-01T00:00:00+00:00",
            "agent": "acme-admin",
            "scope": "tenant",
            "role": "owner",
        }]
        tmp_substrate["tokens_path"].write_text(json.dumps(seed))
        with pytest.raises(ProvisionError) as e:
            tp.mint_or_get_bus_token("acme", "Acme")
        assert e.value.code == "bus_token_plaintext_missing"
        assert e.value.status == 500

    def test_does_not_collide_with_existing_knight_token(self, tmp_substrate):
        # A knight token for "acme" already exists with scope=customer, agent=acme
        # D-1b should NOT match it (different scope) and mint a fresh tenant-admin token
        seed = [{
            "token": "sk-acme-knight-deadbeef",
            "project": "acme",
            "agent": "acme",
            "scope": "customer",
            "role": "owner",
            "active": True,
            "created_at": "2026-04-01T00:00:00+00:00",
            "label": "Acme knight",
        }]
        tmp_substrate["tokens_path"].write_text(json.dumps(seed))
        token, minted = tp.mint_or_get_bus_token("acme", "Acme")
        assert minted is True
        assert token != "sk-acme-knight-deadbeef"
        # Both records present
        data = json.loads(tmp_substrate["tokens_path"].read_text())
        assert len(data) == 2

    def test_inactive_record_skipped(self, tmp_substrate):
        seed = [{
            "token": "sk-old",
            "agent": "acme-admin",
            "scope": "tenant",
            "role": "owner",
            "active": False,
            "project": "acme",
            "label": "old",
            "created_at": "2026-04-01T00:00:00+00:00",
        }]
        tmp_substrate["tokens_path"].write_text(json.dumps(seed))
        token, minted = tp.mint_or_get_bus_token("acme", "Acme")
        assert minted is True
        assert token != "sk-old"

    def test_token_hash_matches_plaintext(self, tmp_substrate):
        import hashlib
        token, _ = tp.mint_or_get_bus_token("acme", "Acme")
        data = json.loads(tmp_substrate["tokens_path"].read_text())
        rec = data[0]
        assert rec["token_hash"] == hashlib.sha256(token.encode()).hexdigest()


# -----------------------------------------------------------------------
# LOCK-D-1b-scaffold-idempotent — 5 tests
# -----------------------------------------------------------------------
class TestScaffoldIdempotent:
    def test_fresh_creates_dir_and_files(self, tmp_substrate):
        path, created = tp.scaffold_or_skip("acme", "Acme Corp", "saas")
        assert created is True
        assert path.exists()
        assert (path / "CLAUDE.md").exists()
        # Placeholder substitution check
        claude = (path / "CLAUDE.md").read_text()
        assert "Acme Corp" in claude
        assert "slug=acme" in claude
        assert "industry=saas" in claude

    def test_re_invocation_does_not_overwrite(self, tmp_substrate):
        path1, c1 = tp.scaffold_or_skip("acme", "Acme", "saas")
        # User edits the file
        (path1 / "CLAUDE.md").write_text("CUSTOM TENANT EDIT\n")
        path2, c2 = tp.scaffold_or_skip("acme", "Acme", "saas")
        assert c2 is False
        assert (path2 / "CLAUDE.md").read_text() == "CUSTOM TENANT EDIT\n"

    def test_partial_state_only_dir_exists(self, tmp_substrate):
        # Pre-create dir but no template files
        target = tmp_substrate["customers_dir"] / "legacy"
        target.mkdir(parents=True)
        path, created = tp.scaffold_or_skip("legacy", "Legacy", "other")
        assert created is False
        # Templates rendered into the existing dir
        assert (path / "CLAUDE.md").exists()
        assert (path / "README.md").exists()

    def test_missing_templates_dir_fails_loud(self, tmp_substrate, tmp_path):
        # Point templates dir at a path that doesn't exist
        missing = tmp_path / "no-such-dir"
        import sos.bus.tenant_provisioning as tp_mod
        original = tp_mod.TEMPLATES_DIR
        tp_mod.TEMPLATES_DIR = missing
        try:
            with pytest.raises(ProvisionError) as e:
                tp.scaffold_or_skip("acme", "Acme", "saas")
            assert e.value.code == "templates_missing"
            assert e.value.status == 500
        finally:
            tp_mod.TEMPLATES_DIR = original

    def test_dotfile_templates_rendered(self, tmp_substrate):
        path, _ = tp.scaffold_or_skip("acme", "Acme Corp", "saas")
        assert (path / ".env.example").exists()
        assert (path / ".gitignore").exists()
        assert "TENANT_SLUG=acme" in (path / ".env.example").read_text()


# -----------------------------------------------------------------------
# Orchestrator end-to-end — 4 tests
# -----------------------------------------------------------------------
class TestProvisionTenantOrchestrator:
    def _body(self):
        return {
            "tenant_id": "t-abc-123",
            "slug": "acme",
            "display_name": "Acme Corp",
            "industry": "saas",
        }

    def test_full_flow_returns_all_fields(self, tmp_substrate):
        out = tp.provision_tenant(self._body())
        assert out["tenant_id"] == "t-abc-123"
        assert out["slug"] == "acme"
        assert out["mirror_key"].startswith("sk-mumega-acme-")
        assert out["bus_token"].startswith("sk-acme-admin-")
        assert out["scaffold_path"].endswith("/customers/acme")
        assert out["idempotency"] == {
            "mirror_minted": True,
            "token_minted": True,
            "scaffold_created": True,
        }

    def test_idempotent_re_provision(self, tmp_substrate):
        a = tp.provision_tenant(self._body())
        b = tp.provision_tenant(self._body())
        assert a["mirror_key"] == b["mirror_key"]
        assert a["bus_token"] == b["bus_token"]
        assert b["idempotency"] == {
            "mirror_minted": False,
            "token_minted": False,
            "scaffold_created": False,
        }

    def test_legacy_partial_state_backfill(self, tmp_substrate):
        # Tenant dir exists (from old manual seed) but no token / no mirror key
        legacy = tmp_substrate["customers_dir"] / "gaf"
        legacy.mkdir(parents=True)
        body = self._body()
        body["slug"] = "gaf"
        body["display_name"] = "Grant & Funding"
        out = tp.provision_tenant(body)
        # Mints token + mirror key, scaffold not freshly created (dir already existed)
        assert out["idempotency"]["mirror_minted"] is True
        assert out["idempotency"]["token_minted"] is True
        assert out["idempotency"]["scaffold_created"] is False

    def test_invalid_body_raises_before_any_disk_write(self, tmp_substrate):
        body = self._body()
        body["slug"] = "BAD-SLUG"
        with pytest.raises(ProvisionError) as e:
            tp.provision_tenant(body)
        assert e.value.code == "invalid_slug"
        # Disk untouched
        assert not tmp_substrate["mirror_keys_path"].exists()
        assert not tmp_substrate["tokens_path"].exists()


# -----------------------------------------------------------------------
# Atomic write — 3 tests
# -----------------------------------------------------------------------
class TestAtomicWrite:
    def test_writes_valid_json(self, tmp_path):
        target = tmp_path / "out.json"
        tp.atomic_write_json(target, [{"a": 1}])
        assert json.loads(target.read_text()) == [{"a": 1}]

    def test_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "deep" / "nested" / "out.json"
        tp.atomic_write_json(target, {"k": "v"})
        assert target.exists()

    def test_no_temp_file_remains(self, tmp_path):
        target = tmp_path / "out.json"
        tp.atomic_write_json(target, [1, 2, 3])
        # Tmp file cleaned up via rename
        assert not (tmp_path / "out.json.tmp").exists()


# -----------------------------------------------------------------------
# Hermetic-static-shape — bridge.py wiring + LOCK markers
# -----------------------------------------------------------------------
BRIDGE_SOURCE = (Path(__file__).parent.parent.parent / "sos" / "bus" / "bridge.py").read_text()
TENANT_PROVISIONING_SOURCE = (
    Path(__file__).parent.parent.parent / "sos" / "bus" / "tenant_provisioning.py"
).read_text()


class TestBridgeWiringShape:
    def test_bridge_routes_provision_endpoint(self):
        assert '"/api/internal/tenants/provision"' in BRIDGE_SOURCE

    def test_bridge_dispatches_before_tokens_json_auth(self):
        # Provision endpoint MUST be matched and handled BEFORE the global _auth() call
        # in do_POST. Verify by character-position ordering.
        provision_idx = BRIDGE_SOURCE.find('"/api/internal/tenants/provision"')
        # Find the do_POST header
        do_post_idx = BRIDGE_SOURCE.find("def do_POST")
        # And the first _auth() call inside do_POST (after our new branch)
        # Use first _auth() that follows do_POST
        post_section = BRIDGE_SOURCE[do_post_idx:]
        first_auth_in_do_post = post_section.find("token = self._auth()")
        assert first_auth_in_do_post > 0
        absolute_auth_idx = do_post_idx + first_auth_in_do_post
        assert do_post_idx < provision_idx < absolute_auth_idx, (
            "Provision endpoint must be routed BEFORE _auth() to keep auth domains separated"
        )

    def test_handler_imports_authenticate_bearer(self):
        assert "authenticate_bearer" in BRIDGE_SOURCE

    def test_handler_imports_get_internal_secret(self):
        assert "get_internal_secret" in BRIDGE_SOURCE

    def test_handler_returns_503_on_missing_secret(self):
        assert '503, {"error": "internal_secret_unconfigured"}' in BRIDGE_SOURCE

    def test_handler_returns_401_on_bad_bearer(self):
        assert '401, {"error": "unauthorized"}' in BRIDGE_SOURCE


class TestLockMarkers:
    def test_lock_d1b_internal_bearer_marker(self):
        assert "LOCK-D-1b-internal-bearer-fail-closed" in TENANT_PROVISIONING_SOURCE
        assert "LOCK-D-1b-internal-bearer-fail-closed" in BRIDGE_SOURCE

    def test_lock_d1b_mirror_idempotent_marker(self):
        assert "LOCK-D-1b-mirror-key-idempotent" in TENANT_PROVISIONING_SOURCE

    def test_lock_d1b_token_idempotent_marker(self):
        assert "LOCK-D-1b-bus-token-idempotent" in TENANT_PROVISIONING_SOURCE

    def test_lock_d1b_scaffold_idempotent_marker(self):
        assert "LOCK-D-1b-scaffold-idempotent" in TENANT_PROVISIONING_SOURCE

    def test_constant_time_compare_uses_hmac(self):
        assert "hmac.compare_digest" in TENANT_PROVISIONING_SOURCE
