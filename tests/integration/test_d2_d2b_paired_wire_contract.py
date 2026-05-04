"""S027 D-2 + D-2b paired wire-contract test (§4 of paired brief stub).

Closes AGD candidate **`boundary-contract-not-exercised-together`** (filed S027 D-1
iter-1) at the pair-source level: the Worker's TypeScript source and the SOS
Python source are exercised TOGETHER in one test, so any drift in field names,
auth-path branching, or response shape between them fails this test BEFORE
deploy.

Wire contract under test (single source of truth):

    Worker (`POST /api/tenants/:id/agents/activate`, written in
    `mumega.com/workers/inkwell-api/src/routes/tenants-agent-activate.ts`)
        ──HTTP POST──▶  D-2b bus-bridge handler at
                        `/api/internal/tenants/:id/agents/activate` which delegates
                        to `sos.bus.tenant_agent_activation.activate_tenant_agent(body)`

Discipline:
    1. We read the Worker source as text (not mocked — actual deployed code).
    2. We extract the EXACT field names Worker writes to `reqBody`.
    3. We construct a body using only those names.
    4. We pass that body through the REAL D-2b functions (`validate_activation_body`
       + `activate_tenant_agent`) with hermetic tmpfs-backed registries.
    5. We extract the EXACT field names Worker reads from `ack`.
    6. We assert D-2b's response carries every one of those names.
    7. We assert auth-path mutual exclusion is honored on both sides.
    8. We assert idempotency on second-call shape (the contract Worker depends on).

If any field is renamed on either side, this test fails — that's the whole point.
Pair-mandatory per S027 D-1 iter-1 carry: any wire-contract spanning Worker↔SOS
gets one of these tests.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from sos.bus import tenant_agent_activation as taa
from sos.bus.tenant_agent_activation import ProvisionError


# ---------------------------------------------------------------------------
# Source-of-truth file locations
# ---------------------------------------------------------------------------
WORKER_SOURCE = Path(
    "/home/mumega/mumega.com/workers/inkwell-api/src/routes/"
    "tenants-agent-activate.ts"
)


# ---------------------------------------------------------------------------
# Static extraction helpers — these read the Worker source as text. If the
# Worker source moves or the field names change, these pinpoint the drift.
# ---------------------------------------------------------------------------
def _worker_source_text() -> str:
    assert WORKER_SOURCE.exists(), (
        f"Worker source missing at {WORKER_SOURCE}; paired test "
        "cannot run without both sides of the contract present."
    )
    return WORKER_SOURCE.read_text()


def _extract_worker_request_fields(src: str) -> set[str]:
    """Return the set of field names Worker writes into the outbound body
    (`reqBody`) before POSTing to D-2b. Mutually-exclusive auth-path fields
    are included as a UNION (both branches contribute their field).
    """
    # Required fields (always present)
    required_block = re.search(
        r"const\s+reqBody[^=]*=\s*\{([^}]+)\}", src, re.DOTALL
    )
    assert required_block, (
        "could not locate `const reqBody = { ... }` in Worker source; "
        "static extraction broke — was the request body construction "
        "refactored away from object-literal form?"
    )
    fields: set[str] = set()
    for m in re.finditer(r"^\s*(\w+):", required_block.group(1), re.MULTILINE):
        fields.add(m.group(1))

    # Conditional auth-path fields — these are added on a branch so they're
    # not in the literal. Match assignment statements like
    # `reqBody.actor_token_hash = ...` and `reqBody.actor_type = ...`.
    for m in re.finditer(r"reqBody\.(\w+)\s*=", src):
        fields.add(m.group(1))
    return fields


def _extract_worker_response_field_reads(src: str) -> set[str]:
    """Return the set of field names Worker reads off of the D-2b response
    (`ack`). These are the fields D-2b is contractually obligated to return.
    """
    # Pattern: `ack.field_name`
    fields = set(re.findall(r"\back\.(\w+)\b", src))
    # Filter out type-only or destructure noise — only keep snake_case-ish
    # response fields (D-2b convention).
    return {f for f in fields if re.match(r"^[a-z][a-z0-9_]*$", f)}


# ---------------------------------------------------------------------------
# Hermetic substrate fixture (mirrors test_tenant_agent_activation.py)
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_substrate(tmp_path, monkeypatch):
    tokens_path = tmp_path / "tokens.json"
    qnft_registry_path = tmp_path / "qnft_registry.json"
    routing_path = tmp_path / "agent_routing.json"
    customers_dir = tmp_path / "customers"
    templates_dir = tmp_path / "agent-fork-templates"
    templates_dir.mkdir(parents=True)
    for kind in ("athena", "kasra", "calliope"):
        (templates_dir / f"{kind}.md").write_text(
            f"# {{{{AGENT_NAME}}}} ({kind} fork)\n"
            "tenant={{TENANT_SLUG}}\n"
            "kind={{AGENT_KIND}}\n"
            "seed={{QNFT_SEED_HEX}}\n"
        )
    monkeypatch.setattr(taa, "TOKENS_PATH", tokens_path)
    monkeypatch.setattr(taa, "QNFT_REGISTRY_PATH", qnft_registry_path)
    monkeypatch.setattr(taa, "DYNAMIC_ROUTING_PATH", routing_path)
    monkeypatch.setattr(taa, "CUSTOMERS_DIR", customers_dir)
    monkeypatch.setattr(taa, "AGENT_FORK_TEMPLATES_DIR", templates_dir)
    return {
        "tokens_path": tokens_path,
        "qnft_registry_path": qnft_registry_path,
        "customers_dir": customers_dir,
    }


@pytest.fixture
def seeded_tenant_admin(tmp_substrate):
    raw = "sk-acme-admin-deadbeefcafebabedeadbeefcafebabe"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    tmp_substrate["tokens_path"].write_text(
        json.dumps(
            [
                {
                    "token": raw,
                    "token_hash": digest,
                    "project": "acme",
                    "active": True,
                    "agent": "acme-admin",
                    "scope": "tenant",
                }
            ]
        )
    )
    return {"raw": raw, "hash": digest, "slug": "acme"}


# ---------------------------------------------------------------------------
# §1 — Static contract extraction sanity (catches refactor-away-from-pattern)
# ---------------------------------------------------------------------------
class TestStaticContractExtraction:
    def test_worker_source_exists(self):
        assert WORKER_SOURCE.exists(), WORKER_SOURCE

    def test_worker_request_fields_extracted(self):
        fields = _extract_worker_request_fields(_worker_source_text())
        # The base reqBody object MUST carry these three (per §3 of brief).
        assert {"tenant_id", "tenant_slug", "agent_kind"}.issubset(fields), (
            f"Worker reqBody missing baseline contract fields; got {fields}"
        )

    def test_worker_request_includes_both_auth_paths(self):
        fields = _extract_worker_request_fields(_worker_source_text())
        # Mutually-exclusive auth-path fields. Static extraction sees both
        # because both are reachable code paths.
        assert "actor_token_hash" in fields, fields
        assert "actor_type" in fields, fields

    def test_worker_reads_back_required_response_fields(self):
        reads = _extract_worker_response_field_reads(_worker_source_text())
        # Worker MUST consume each of these to populate D1 INSERT + response.
        for required in ("agent_name", "qnft_seed_hex", "token_hash"):
            assert required in reads, (
                f"Worker source no longer reads ack.{required}; "
                f"D-2b's response contract has drifted away from Worker. "
                f"reads={reads}"
            )


# ---------------------------------------------------------------------------
# §2 — Worker-shape body PASSES through real D-2b validation (round-trip)
# ---------------------------------------------------------------------------
class TestWorkerBodyValidatesAgainstD2b:
    def test_tenant_admin_path_body_passes_validation(self, seeded_tenant_admin):
        # Construct body using the Worker's tenant-admin-path branch:
        # base + actor_token_hash (NOT actor_type).
        body = {
            "tenant_id": "01HD2WIRE",
            "tenant_slug": "acme",
            "agent_kind": "athena",
            "actor_token_hash": seeded_tenant_admin["hash"],
        }
        sanitized = taa.validate_activation_body(body)
        assert sanitized["tenant_id"] == "01HD2WIRE"
        assert sanitized["tenant_slug"] == "acme"
        assert sanitized["agent_kind"] == "athena"
        assert sanitized["actor_token_hash"] == seeded_tenant_admin["hash"]
        assert sanitized["actor_type"] is None

    def test_platform_admin_path_body_passes_validation(self, tmp_substrate):
        # Construct body using the Worker's platform-admin-path branch:
        # base + actor_type (NOT actor_token_hash).
        body = {
            "tenant_id": "01HD2WIRE",
            "tenant_slug": "acme",
            "agent_kind": "athena",
            "actor_type": "platform-admin",
        }
        sanitized = taa.validate_activation_body(body)
        assert sanitized["actor_type"] == "platform-admin"
        assert sanitized["actor_token_hash"] is None

    def test_worker_cannot_send_both_auth_fields_simultaneously(
        self, seeded_tenant_admin
    ):
        # If a future Worker refactor accidentally sends both, D-2b MUST reject.
        # Mutual exclusion is enforced at the substrate layer regardless.
        body = {
            "tenant_id": "01HD2WIRE",
            "tenant_slug": "acme",
            "agent_kind": "athena",
            "actor_token_hash": seeded_tenant_admin["hash"],
            "actor_type": "platform-admin",
        }
        with pytest.raises(ProvisionError) as exc:
            taa.validate_activation_body(body)
        assert exc.value.code == "invalid_actor"
        assert exc.value.status == 422


# ---------------------------------------------------------------------------
# §3 — End-to-end: real activate_tenant_agent + verify response carries
# every field Worker reads. NOT mocked on either side — Worker source is the
# real spec; D-2b functions are real code with real disk writes.
# ---------------------------------------------------------------------------
class TestEndToEndContractRoundTrip:
    def test_tenant_admin_path_returns_all_worker_required_fields(
        self, seeded_tenant_admin
    ):
        body = {
            "tenant_id": "01HE2EWIRE",
            "tenant_slug": "acme",
            "agent_kind": "athena",
            "actor_token_hash": seeded_tenant_admin["hash"],
        }
        result = taa.activate_tenant_agent(body)

        worker_reads = _extract_worker_response_field_reads(_worker_source_text())
        for field in worker_reads:
            assert field in result, (
                f"D-2b response missing field `{field}` that Worker reads as "
                f"`ack.{field}`. Wire contract drift: keys returned = "
                f"{sorted(result.keys())}, keys expected = {sorted(worker_reads)}."
            )
        # And the load-bearing trio specifically — these gate Worker's D1 INSERT.
        assert result["agent_name"]
        assert re.match(r"^[a-f0-9]{64}$", result["qnft_seed_hex"]), result[
            "qnft_seed_hex"
        ]
        assert re.match(r"^[a-f0-9]{64}$", result["token_hash"]), result[
            "token_hash"
        ]
        # Agent-name shape contract: `{kind}-{slug}` (Worker fallback computes
        # the same; if D-2b changes naming, Worker's fallback becomes wrong).
        assert result["agent_name"] == "athena-acme"

    def test_platform_admin_path_returns_all_worker_required_fields(
        self, tmp_substrate
    ):
        body = {
            "tenant_id": "01HE2EWIRE",
            "tenant_slug": "acme",
            "agent_kind": "kasra",
            "actor_type": "platform-admin",
        }
        result = taa.activate_tenant_agent(body)
        worker_reads = _extract_worker_response_field_reads(_worker_source_text())
        for field in worker_reads:
            assert field in result, (
                f"D-2b response missing `{field}` that Worker reads via ack."
                f" platform-admin-path response keys: {sorted(result.keys())}"
            )
        assert result["agent_name"] == "kasra-acme"

    def test_idempotency_field_shape_matches_worker_consumption(
        self, seeded_tenant_admin
    ):
        # Worker reads `ack.idempotency` and forwards it untouched in its
        # response payload. The shape inside is what tenant-admin tooling
        # observes, so it must remain stable.
        body = {
            "tenant_id": "01HIDEM",
            "tenant_slug": "acme",
            "agent_kind": "calliope",
            "actor_token_hash": seeded_tenant_admin["hash"],
        }
        first = taa.activate_tenant_agent(body)
        assert "idempotency" in first
        assert isinstance(first["idempotency"], dict)
        for k in (
            "qnft_minted",
            "token_minted",
            "routing_registered",
            "scaffold_created",
        ):
            assert k in first["idempotency"], (
                f"idempotency.{k} missing — Worker forwards this object to "
                f"caller as-is, schema drift breaks consumers."
            )

        # Second call → all flags False (true convergence).
        second = taa.activate_tenant_agent(body)
        assert second["agent_name"] == first["agent_name"]
        assert second["qnft_seed_hex"] == first["qnft_seed_hex"]
        assert second["token_hash"] == first["token_hash"]
        assert second["idempotency"] == {
            "qnft_minted": False,
            "token_minted": False,
            "routing_registered": False,
            "scaffold_created": False,
        }, (
            "Idempotent re-activate must report all-False; Worker propagates "
            "this to caller and any drift will mislead automation."
        )


# ---------------------------------------------------------------------------
# §4 — Cross-tenant attack at wire layer (paired with L-7 RLS test)
# ---------------------------------------------------------------------------
class TestCrossTenantAttackAtWire:
    def test_tenant_admin_token_for_one_tenant_cannot_activate_for_another(
        self, seeded_tenant_admin
    ):
        # acme admin token; body claims tenant_slug=other → D-2b's claim
        # validator must reject. Worker hashes the token and forwards
        # blindly; D-2b is the real claim validator (Athena REFINE-1).
        body = {
            "tenant_id": "01HOTHER",
            "tenant_slug": "other",
            "agent_kind": "athena",
            "actor_token_hash": seeded_tenant_admin["hash"],
        }
        with pytest.raises(ProvisionError) as exc:
            taa.activate_tenant_agent(body)
        # Either tenant_slug-mismatch on the claim (preferred) or
        # invalid_token if the lookup fails. Both are acceptable; the
        # critical invariant is "must raise".
        assert exc.value.status in (401, 403), (
            f"cross-tenant attack must raise 401/403; got {exc.value.status} "
            f"with code={exc.value.code}"
        )
