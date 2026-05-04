"""
S027 D-2b — Tests for tenant_agent_activation module + bridge.py endpoint shape.

LOCK invariants verified (7):
  - L-1 LOCK-D-2b-internal-bearer-fail-closed + tenant-token-claim-validator
  - L-2 LOCK-D-2b-qnft-mint-idempotent
  - L-3 LOCK-D-2b-bus-token-mint-idempotent
  - L-4 LOCK-D-2b-routing-register-idempotent
  - L-5 LOCK-D-2b-scaffold-idempotent
  - L-6 LOCK-D-2b-response-shape-carries-d1-payload
  - L-7 LOCK-D-2b-bus-layer-rls-three-discriminator (delivery.py — separate test)

Hermetic discipline: each test uses monkeypatched paths to tmp dirs (no global state leak).
Mirrors test_tenant_provision.py shape (paired LOCK family).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from sos.bus import tenant_agent_activation as taa
from sos.bus.tenant_agent_activation import ProvisionError


# -----------------------------------------------------------------------
# Fixtures — hermetic tmp paths
# -----------------------------------------------------------------------
@pytest.fixture
def tmp_substrate(tmp_path, monkeypatch):
    """Fresh substrate state per test: tmp tokens.json, qnft_registry.json, routing, customers/, templates/."""
    tokens_path = tmp_path / "tokens.json"
    qnft_registry_path = tmp_path / "qnft_registry.json"
    routing_path = tmp_path / "agent_routing.json"
    customers_dir = tmp_path / "customers"
    templates_dir = tmp_path / "agent-fork-templates"

    # Seed agent-fork templates for the 3 v1-allowlist kinds
    templates_dir.mkdir(parents=True)
    for kind in ("athena", "kasra", "calliope"):
        (templates_dir / f"{kind}.md").write_text(
            f"# {{{{AGENT_NAME}}}} ({kind} fork)\n"
            "tenant={{TENANT_SLUG}}\n"
            "display={{TENANT_DISPLAY_NAME}}\n"
            "industry={{INDUSTRY}}\n"
            "seed={{QNFT_SEED_HEX}}\n"
            "minted={{MINT_DATE}}\n"
        )

    monkeypatch.setattr(taa, "TOKENS_PATH", tokens_path)
    monkeypatch.setattr(taa, "QNFT_REGISTRY_PATH", qnft_registry_path)
    monkeypatch.setattr(taa, "DYNAMIC_ROUTING_PATH", routing_path)
    monkeypatch.setattr(taa, "CUSTOMERS_DIR", customers_dir)
    monkeypatch.setattr(taa, "AGENT_FORK_TEMPLATES_DIR", templates_dir)

    return {
        "tokens_path": tokens_path,
        "qnft_registry_path": qnft_registry_path,
        "routing_path": routing_path,
        "customers_dir": customers_dir,
        "templates_dir": templates_dir,
    }


@pytest.fixture
def seeded_tenant_admin_token(tmp_substrate):
    """Seed a valid tenant-admin token (D-1b style) for tenant 'acme' in the tmp tokens.json."""
    raw_token = "sk-acme-admin-deadbeefcafebabedeadbeefcafebabe"
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    record = {
        "token": raw_token,
        "token_hash": token_hash,
        "project": "acme",
        "label": "Acme tenant admin",
        "active": True,
        "created_at": "2026-05-04T22:00Z",
        "agent": "acme-admin",
        "scope": "tenant",
        "role": "owner",
    }
    tmp_substrate["tokens_path"].write_text(json.dumps([record], indent=2))
    return {"token": raw_token, "hash": token_hash, "slug": "acme"}


# -----------------------------------------------------------------------
# L-1 — body validation + auth-path mutual exclusion
# -----------------------------------------------------------------------
class TestL1BodyValidation:
    def test_non_dict_body_rejected(self):
        with pytest.raises(ProvisionError) as exc:
            taa.validate_activation_body([1, 2])
        assert exc.value.status == 422
        assert exc.value.code == "invalid_body"

    def test_missing_tenant_id(self):
        with pytest.raises(ProvisionError) as exc:
            taa.validate_activation_body(
                {"tenant_slug": "acme", "agent_kind": "athena", "actor_token_hash": "a" * 64}
            )
        assert exc.value.code == "invalid_tenant_id"

    def test_invalid_tenant_id_chars(self):
        with pytest.raises(ProvisionError) as exc:
            taa.validate_activation_body(
                {"tenant_id": "bad/id!", "tenant_slug": "acme", "agent_kind": "athena", "actor_token_hash": "a" * 64}
            )
        assert exc.value.code == "invalid_tenant_id"

    def test_missing_tenant_slug(self):
        with pytest.raises(ProvisionError) as exc:
            taa.validate_activation_body(
                {"tenant_id": "01H", "agent_kind": "athena", "actor_token_hash": "a" * 64}
            )
        assert exc.value.code == "invalid_tenant_slug"

    def test_invalid_agent_kind_format(self):
        with pytest.raises(ProvisionError) as exc:
            taa.validate_activation_body(
                {"tenant_id": "01H", "tenant_slug": "acme", "agent_kind": "BadKind!", "actor_token_hash": "a" * 64}
            )
        assert exc.value.code == "invalid_agent_kind"

    def test_substrate_only_kind_rejected_loom(self):
        with pytest.raises(ProvisionError) as exc:
            taa.validate_activation_body(
                {"tenant_id": "01H", "tenant_slug": "acme", "agent_kind": "loom", "actor_token_hash": "a" * 64}
            )
        assert exc.value.status == 422
        assert exc.value.code == "not_forkable"

    def test_substrate_only_kind_rejected_river(self):
        with pytest.raises(ProvisionError) as exc:
            taa.validate_activation_body(
                {"tenant_id": "01H", "tenant_slug": "acme", "agent_kind": "river", "actor_token_hash": "a" * 64}
            )
        assert exc.value.code == "not_forkable"

    def test_substrate_only_kind_rejected_codex_v1(self):
        # Loom Q9 routing: codex deferred to S028 thaw
        with pytest.raises(ProvisionError) as exc:
            taa.validate_activation_body(
                {"tenant_id": "01H", "tenant_slug": "acme", "agent_kind": "codex", "actor_token_hash": "a" * 64}
            )
        assert exc.value.code == "not_forkable"

    def test_unknown_kind_rejected(self):
        with pytest.raises(ProvisionError) as exc:
            taa.validate_activation_body(
                {"tenant_id": "01H", "tenant_slug": "acme", "agent_kind": "unknown", "actor_token_hash": "a" * 64}
            )
        assert exc.value.code == "invalid_agent_kind"

    def test_allowlist_athena_passes(self):
        result = taa.validate_activation_body(
            {"tenant_id": "01H", "tenant_slug": "acme", "agent_kind": "athena", "actor_token_hash": "a" * 64}
        )
        assert result["agent_kind"] == "athena"

    def test_allowlist_kasra_passes(self):
        result = taa.validate_activation_body(
            {"tenant_id": "01H", "tenant_slug": "acme", "agent_kind": "kasra", "actor_token_hash": "a" * 64}
        )
        assert result["agent_kind"] == "kasra"

    def test_allowlist_calliope_passes(self):
        result = taa.validate_activation_body(
            {"tenant_id": "01H", "tenant_slug": "acme", "agent_kind": "calliope", "actor_token_hash": "a" * 64}
        )
        assert result["agent_kind"] == "calliope"

    def test_actor_path_mutual_exclusion_both_provided(self):
        with pytest.raises(ProvisionError) as exc:
            taa.validate_activation_body(
                {
                    "tenant_id": "01H",
                    "tenant_slug": "acme",
                    "agent_kind": "athena",
                    "actor_token_hash": "a" * 64,
                    "actor_type": "platform-admin",
                }
            )
        assert exc.value.code == "invalid_actor"

    def test_actor_path_mutual_exclusion_neither_provided(self):
        with pytest.raises(ProvisionError) as exc:
            taa.validate_activation_body(
                {"tenant_id": "01H", "tenant_slug": "acme", "agent_kind": "athena"}
            )
        assert exc.value.code == "invalid_actor"

    def test_actor_token_hash_must_be_64_hex(self):
        with pytest.raises(ProvisionError) as exc:
            taa.validate_activation_body(
                {"tenant_id": "01H", "tenant_slug": "acme", "agent_kind": "athena", "actor_token_hash": "short"}
            )
        assert exc.value.code == "invalid_actor_token_hash"

    def test_actor_type_must_be_platform_admin(self):
        with pytest.raises(ProvisionError) as exc:
            taa.validate_activation_body(
                {"tenant_id": "01H", "tenant_slug": "acme", "agent_kind": "athena", "actor_type": "user"}
            )
        assert exc.value.code == "invalid_actor_type"

    def test_platform_admin_path_passes(self):
        result = taa.validate_activation_body(
            {"tenant_id": "01H", "tenant_slug": "acme", "agent_kind": "athena", "actor_type": "platform-admin"}
        )
        assert result["actor_type"] == "platform-admin"
        assert result["actor_token_hash"] is None


# -----------------------------------------------------------------------
# L-1 (claim validator) — token claim resolution
# -----------------------------------------------------------------------
class TestL1ClaimValidation:
    def test_valid_token_passes(self, tmp_substrate, seeded_tenant_admin_token):
        # Should not raise
        taa.validate_actor_token_claims(seeded_tenant_admin_token["hash"], "acme")

    def test_unknown_hash_rejected(self, tmp_substrate, seeded_tenant_admin_token):
        with pytest.raises(ProvisionError) as exc:
            taa.validate_actor_token_claims("0" * 64, "acme")
        assert exc.value.status == 401
        assert exc.value.code == "invalid_token"

    def test_inactive_token_rejected(self, tmp_substrate):
        # Seed an inactive token
        raw = "sk-acme-admin-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        h = hashlib.sha256(raw.encode()).hexdigest()
        rec = {
            "token": raw, "token_hash": h, "project": "acme", "label": "x",
            "active": False, "created_at": "2026-01-01", "agent": "acme-admin",
            "scope": "tenant", "role": "owner",
        }
        tmp_substrate["tokens_path"].write_text(json.dumps([rec]))
        with pytest.raises(ProvisionError) as exc:
            taa.validate_actor_token_claims(h, "acme")
        assert exc.value.status == 401

    def test_wrong_scope_rejected(self, tmp_substrate):
        raw = "sk-acme-admin-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        h = hashlib.sha256(raw.encode()).hexdigest()
        rec = {
            "token": raw, "token_hash": h, "project": "acme", "label": "x",
            "active": True, "created_at": "2026-01-01", "agent": "acme-admin",
            "scope": "customer",  # wrong scope
            "role": "owner",
        }
        tmp_substrate["tokens_path"].write_text(json.dumps([rec]))
        with pytest.raises(ProvisionError) as exc:
            taa.validate_actor_token_claims(h, "acme")
        assert exc.value.status == 403
        assert exc.value.code == "invalid_scope"

    def test_cross_tenant_attack_rejected(self, tmp_substrate, seeded_tenant_admin_token):
        # Token belongs to acme; presented for 'evil' tenant
        with pytest.raises(ProvisionError) as exc:
            taa.validate_actor_token_claims(seeded_tenant_admin_token["hash"], "evil")
        assert exc.value.status == 403
        assert exc.value.code == "tenant_id_mismatch"


# -----------------------------------------------------------------------
# L-2 — QNFT mint idempotent
# -----------------------------------------------------------------------
class TestL2QnftMintIdempotent:
    def test_first_mint_creates_record(self, tmp_substrate):
        record, minted = taa.mint_or_get_qnft("athena-acme", "athena", "acme")
        assert minted is True
        assert "seed_hex" in record
        assert len(record["seed_hex"]) == 64
        assert len(record["vector_16d"]) == 16
        assert record["agent_kind"] == "athena"
        assert record["customer_slug"] == "acme"
        assert record["tier"] == "operational"
        assert record["signer"] == "loom"
        assert record["countersigned_by"] is None

    def test_second_mint_returns_existing(self, tmp_substrate):
        rec1, minted1 = taa.mint_or_get_qnft("athena-acme", "athena", "acme")
        rec2, minted2 = taa.mint_or_get_qnft("athena-acme", "athena", "acme")
        assert minted1 is True
        assert minted2 is False
        assert rec2["seed_hex"] == rec1["seed_hex"]

    def test_corrupt_empty_record_remint_safe_no_loop(self, tmp_substrate):
        # Seed corrupt entry (missing seed_hex)
        registry = {"athena-acme": {"signer": "loom", "tier": "operational"}}
        tmp_substrate["qnft_registry_path"].write_text(json.dumps(registry))

        rec, minted = taa.mint_or_get_qnft("athena-acme", "athena", "acme")
        assert minted is True
        assert "seed_hex" in rec

        # Verify only one valid + one inactive corrupt forensic copy after remint
        post = json.loads(tmp_substrate["qnft_registry_path"].read_text())
        valid_keys = [k for k, v in post.items() if v.get("seed_hex")]
        assert len(valid_keys) == 1
        # Corrupt copy should be present under suffixed key, marked inactive
        corrupt_keys = [k for k in post.keys() if "corrupt" in k]
        assert len(corrupt_keys) == 1
        assert post[corrupt_keys[0]].get("active") is False

        # Re-invocation does NOT re-mint (corrupt key is now under suffixed key, not athena-acme)
        rec2, minted2 = taa.mint_or_get_qnft("athena-acme", "athena", "acme")
        assert minted2 is False
        assert rec2["seed_hex"] == rec["seed_hex"]


# -----------------------------------------------------------------------
# L-3 — Bus token mint idempotent
# -----------------------------------------------------------------------
class TestL3TokenMintIdempotent:
    def test_first_mint_creates_record(self, tmp_substrate):
        raw, h, minted = taa.mint_or_get_tenant_agent_token("athena-acme", "athena", "acme")
        assert minted is True
        assert raw.startswith("sk-athena-acme-")
        assert h == hashlib.sha256(raw.encode()).hexdigest()

    def test_second_mint_returns_existing(self, tmp_substrate):
        raw1, h1, minted1 = taa.mint_or_get_tenant_agent_token("athena-acme", "athena", "acme")
        raw2, h2, minted2 = taa.mint_or_get_tenant_agent_token("athena-acme", "athena", "acme")
        assert minted1 is True
        assert minted2 is False
        assert raw1 == raw2
        assert h1 == h2

    def test_token_record_has_three_discriminators(self, tmp_substrate):
        taa.mint_or_get_tenant_agent_token("athena-acme", "athena", "acme")
        tokens = json.loads(tmp_substrate["tokens_path"].read_text())
        rec = next(t for t in tokens if t["agent"] == "athena-acme")
        assert rec["scope"] == "tenant-agent"
        assert rec["tenant_slug"] == "acme"
        assert rec["agent_kind"] == "athena"
        assert rec["agent"] == "athena-acme"

    def test_existing_record_missing_plaintext_raises(self, tmp_substrate):
        # Seed token record without plaintext
        rec = {
            "token": "", "token_hash": "x" * 64, "project": "acme", "label": "x",
            "active": True, "created_at": "2026-01-01", "agent": "athena-acme",
            "scope": "tenant-agent", "tenant_slug": "acme", "agent_kind": "athena",
            "role": "agent",
        }
        tmp_substrate["tokens_path"].write_text(json.dumps([rec]))
        with pytest.raises(ProvisionError) as exc:
            taa.mint_or_get_tenant_agent_token("athena-acme", "athena", "acme")
        assert exc.value.code == "bus_token_plaintext_missing"


# -----------------------------------------------------------------------
# L-4 — Routing register idempotent
# -----------------------------------------------------------------------
class TestL4RoutingIdempotent:
    def test_first_register_creates_route(self, tmp_substrate):
        result = taa.register_or_skip_routing("athena-acme", "tmux")
        assert result is True
        routes = json.loads(tmp_substrate["routing_path"].read_text())
        assert routes["athena-acme"] == "tmux"

    def test_second_register_skips(self, tmp_substrate):
        taa.register_or_skip_routing("athena-acme", "tmux")
        result = taa.register_or_skip_routing("athena-acme", "tmux")
        assert result is False

    def test_routing_value_change_re_registers(self, tmp_substrate):
        taa.register_or_skip_routing("athena-acme", "tmux")
        result = taa.register_or_skip_routing("athena-acme", "remote")
        assert result is True
        routes = json.loads(tmp_substrate["routing_path"].read_text())
        assert routes["athena-acme"] == "remote"

    def test_other_routes_preserved(self, tmp_substrate):
        # Pre-existing route from another agent
        tmp_substrate["routing_path"].write_text(json.dumps({"existing-agent": "tmux"}))
        taa.register_or_skip_routing("athena-acme", "tmux")
        routes = json.loads(tmp_substrate["routing_path"].read_text())
        assert routes["existing-agent"] == "tmux"
        assert routes["athena-acme"] == "tmux"


# -----------------------------------------------------------------------
# L-5 — Scaffold idempotent
# -----------------------------------------------------------------------
class TestL5ScaffoldIdempotent:
    def test_first_scaffold_renders_claude_md(self, tmp_substrate):
        path, created = taa.scaffold_or_skip_agent_fork(
            "athena-acme", "athena", "acme", "Acme Inc", "saas",
            "deadbeef" * 8, "2026-05-04T22:30Z",
        )
        assert created is True
        content = path.read_text()
        assert "athena-acme" in content
        assert "tenant=acme" in content
        assert "display=Acme Inc" in content
        assert "industry=saas" in content
        assert "deadbeef" in content

    def test_second_scaffold_preserves_existing(self, tmp_substrate):
        path, created1 = taa.scaffold_or_skip_agent_fork(
            "athena-acme", "athena", "acme", "Acme Inc", "saas",
            "deadbeef" * 8, "2026-05-04T22:30Z",
        )
        # Modify the file mid-life (simulating tenant-admin edit)
        path.write_text("# user-edited content\nDO NOT OVERWRITE\n")

        path2, created2 = taa.scaffold_or_skip_agent_fork(
            "athena-acme", "athena", "acme", "Acme Inc", "saas",
            "deadbeef" * 8, "2026-05-04T22:30Z",
        )
        assert created2 is False
        assert path2 == path
        assert "DO NOT OVERWRITE" in path.read_text()

    def test_missing_template_raises(self, tmp_substrate):
        with pytest.raises(ProvisionError) as exc:
            taa.scaffold_or_skip_agent_fork(
                "loom-acme", "loom", "acme", "Acme Inc", "saas",
                "x" * 64, "2026-05-04",
            )
        assert exc.value.code == "template_missing"

    def test_scaffold_path_under_customers_agents_kind(self, tmp_substrate):
        path, _ = taa.scaffold_or_skip_agent_fork(
            "calliope-acme", "calliope", "acme", "Acme Inc", "saas",
            "deadbeef" * 8, "2026-05-04T22:30Z",
        )
        # ~/.mumega/customers/acme/agents/calliope/CLAUDE.md (per L-5)
        assert path.parent.name == "calliope"
        assert path.parent.parent.name == "agents"
        assert path.parent.parent.parent.name == "acme"
        assert path.name == "CLAUDE.md"


# -----------------------------------------------------------------------
# Orchestrator — full activate_tenant_agent flow
# -----------------------------------------------------------------------
class TestActivateTenantAgent:
    def test_happy_path_tenant_admin(self, tmp_substrate, seeded_tenant_admin_token):
        result = taa.activate_tenant_agent({
            "tenant_id": "01HQXACME",
            "tenant_slug": "acme",
            "agent_kind": "athena",
            "actor_token_hash": seeded_tenant_admin_token["hash"],
        })
        assert result["agent_name"] == "athena-acme"
        assert result["tenant_slug"] == "acme"
        assert result["agent_kind"] == "athena"
        assert len(result["qnft_seed_hex"]) == 64
        assert len(result["token_hash"]) == 64
        assert result["idempotency"]["qnft_minted"] is True
        assert result["idempotency"]["token_minted"] is True
        assert result["idempotency"]["routing_registered"] is True
        assert result["idempotency"]["scaffold_created"] is True

    def test_idempotent_re_activation(self, tmp_substrate, seeded_tenant_admin_token):
        body = {
            "tenant_id": "01HQXACME",
            "tenant_slug": "acme",
            "agent_kind": "athena",
            "actor_token_hash": seeded_tenant_admin_token["hash"],
        }
        result1 = taa.activate_tenant_agent(body)
        result2 = taa.activate_tenant_agent(body)
        assert result2["qnft_seed_hex"] == result1["qnft_seed_hex"]
        assert result2["token_hash"] == result1["token_hash"]
        assert result2["idempotency"]["qnft_minted"] is False
        assert result2["idempotency"]["token_minted"] is False
        assert result2["idempotency"]["routing_registered"] is False
        assert result2["idempotency"]["scaffold_created"] is False

    def test_platform_admin_path_skips_token_validation(self, tmp_substrate):
        # No token seeded — platform-admin path skips claim validator
        result = taa.activate_tenant_agent({
            "tenant_id": "01HQXACME",
            "tenant_slug": "acme",
            "agent_kind": "calliope",
            "actor_type": "platform-admin",
        })
        assert result["agent_name"] == "calliope-acme"

    def test_cross_tenant_token_attack_blocked(self, tmp_substrate, seeded_tenant_admin_token):
        # Token belongs to acme; activator presents acme's hash for 'evil' tenant
        with pytest.raises(ProvisionError) as exc:
            taa.activate_tenant_agent({
                "tenant_id": "01HEVIL",
                "tenant_slug": "evil",
                "agent_kind": "athena",
                "actor_token_hash": seeded_tenant_admin_token["hash"],
            })
        assert exc.value.status == 403
        assert exc.value.code == "tenant_id_mismatch"

    def test_substrate_only_kind_blocked_at_d2b(self, tmp_substrate, seeded_tenant_admin_token):
        with pytest.raises(ProvisionError) as exc:
            taa.activate_tenant_agent({
                "tenant_id": "01HQXACME",
                "tenant_slug": "acme",
                "agent_kind": "loom",
                "actor_token_hash": seeded_tenant_admin_token["hash"],
            })
        assert exc.value.code == "not_forkable"

    def test_d1_payload_fields_present(self, tmp_substrate, seeded_tenant_admin_token):
        # L-6: Response must carry D1 INSERT payload
        result = taa.activate_tenant_agent({
            "tenant_id": "01HQXACME",
            "tenant_slug": "acme",
            "agent_kind": "athena",
            "actor_token_hash": seeded_tenant_admin_token["hash"],
        })
        # Worker INSERT needs all of these
        for field in ("tenant_id", "agent_name", "agent_kind", "qnft_seed_hex", "token_hash"):
            assert field in result, f"L-6 violation: missing {field}"

    def test_three_kinds_all_forkable(self, tmp_substrate, seeded_tenant_admin_token):
        for kind in ("athena", "kasra", "calliope"):
            # Each gets its own slug to avoid name collision in qnft_registry
            slug = f"acme-{kind}"
            # Need a fresh token for each slug
            raw = f"sk-{slug}-admin-{'a' * 32}"
            h = hashlib.sha256(raw.encode()).hexdigest()
            tok = {
                "token": raw, "token_hash": h, "project": slug, "label": "x",
                "active": True, "created_at": "2026-01-01", "agent": f"{slug}-admin",
                "scope": "tenant", "role": "owner",
            }
            existing = json.loads(tmp_substrate["tokens_path"].read_text())
            existing.append(tok)
            tmp_substrate["tokens_path"].write_text(json.dumps(existing))

            result = taa.activate_tenant_agent({
                "tenant_id": f"01H{kind.upper()}",
                "tenant_slug": slug,
                "agent_kind": kind,
                "actor_token_hash": h,
            })
            assert result["agent_name"] == f"{kind}-{slug}"
            assert result["agent_kind"] == kind
