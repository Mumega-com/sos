"""
S027 D-2b — Tenant agent activation logic for SOS-side companion endpoint.

Surface: POST /api/internal/tenants/:id/agents/activate (mounted on bridge.py)

Brief: agents/kasra/notes/s027-d2-d2b-paired-brief-stub.md (REVISED post Athena REFINE)
Athena brief-shape gate iter-2 GREEN 2026-05-04T22:33Z (kasra_s027_d2_d2b_brief_gate_002).

LOCK invariants (7):
  - L-1 LOCK-D-2b-internal-bearer-fail-closed + tenant-token-claim-validator (Path A)
  - L-2 LOCK-D-2b-qnft-mint-idempotent
  - L-3 LOCK-D-2b-bus-token-mint-idempotent
  - L-4 LOCK-D-2b-routing-register-idempotent
  - L-5 LOCK-D-2b-scaffold-idempotent
  - L-6 LOCK-D-2b-response-shape-carries-d1-payload (Worker performs D1 INSERT)
  - L-7 LOCK-D-2b-bus-layer-rls-three-discriminator (enforced in delivery.py)

Allowlist v1: {athena, kasra, calliope}. Substrate-only kinds (loom/river/mizan/sol/hermes/codex)
→ 422 not_forkable. Loom approved 2026-05-04T22:24Z (Q9 routing decision).

Path A auth decomposition (Athena REFINE-1):
  - Worker = format+presence+SHA-256 hash-and-forward
  - D-2b = real claim validator (tokens.json lookup by hash, scope+slug check)
  - ADMIN_API_SECRET path: Worker validates directly; sends actor_type="platform-admin";
    D-2b skips token validation; URL :id authoritative.

Atomic-write discipline: all JSON file writes use temp-file-and-rename pattern.
Mirrors D-1b's tenant_provisioning.py shape (paired LOCK family).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Reuse D-1b primitives where possible (Path A consistency, atomic write, constant-time)
from sos.bus.tenant_provisioning import (
    SOS_BUS_DIR,
    TOKENS_PATH,
    SLUG_RE,
    TENANT_ID_RE,
    INDUSTRY_RE,
    DISPLAY_NAME_MAX,
    ProvisionError,
    now_iso,
    get_internal_secret,
    constant_time_equal,
    authenticate_bearer,
    atomic_write_json,
)

# -----------------------------------------------------------------------
# Paths — substrate-side artifacts for D-2b
# -----------------------------------------------------------------------
QNFT_REGISTRY_PATH = SOS_BUS_DIR / "qnft_registry.json"
SOS_HOME_DIR = Path.home() / ".sos"
DYNAMIC_ROUTING_PATH = SOS_HOME_DIR / "agent_routing.json"
CUSTOMERS_DIR = Path.home() / ".mumega" / "customers"
# Agent-fork templates live in the SOS repo for version-control + reproducibility
# (per `feedback_config_canon_vs_instance_state.md` — config-as-canon in-repo,
# instance-state out-of-repo). Renders go to ~/.mumega/customers/{slug}/agents/{kind}/.
AGENT_FORK_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "scripts" / "agent-fork-templates"

# -----------------------------------------------------------------------
# Allowlist (v1) — Loom Q9 approved 2026-05-04T22:24Z
# -----------------------------------------------------------------------
ALLOWED_AGENT_KINDS: set[str] = {"athena", "kasra", "calliope"}

# Substrate-only kinds (defense-in-depth: Worker also enforces, but SOS rejects
# substrate-only kinds even if Worker is bypassed — adversarial-parallel discipline).
SUBSTRATE_ONLY_KINDS: set[str] = {"loom", "river", "mizan", "sol", "hermes", "codex"}

# -----------------------------------------------------------------------
# Validation
# -----------------------------------------------------------------------
AGENT_KIND_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
TOKEN_HASH_RE = re.compile(r"^[a-f0-9]{64}$")  # SHA-256 hex digest


def validate_activation_body(body: dict) -> dict:
    """L-1 paired guard: validate body BEFORE any disk write.

    Returns sanitized body dict. Raises ProvisionError on bad input.

    Body shape (mutually exclusive auth paths):
      {tenant_id, tenant_slug, agent_kind, actor_token_hash}      (tenant-admin path)
      {tenant_id, tenant_slug, agent_kind, actor_type: "platform-admin"}  (platform-admin path)
    """
    if not isinstance(body, dict):
        raise ProvisionError(422, "invalid_body", "body must be a JSON object")

    tenant_id = body.get("tenant_id")
    tenant_slug = body.get("tenant_slug")
    agent_kind = body.get("agent_kind")
    actor_token_hash = body.get("actor_token_hash")
    actor_type = body.get("actor_type")

    if not tenant_id or not isinstance(tenant_id, str) or not TENANT_ID_RE.match(tenant_id):
        raise ProvisionError(422, "invalid_tenant_id", "tenant_id must match ^[a-zA-Z0-9._-]{1,64}$")

    if not tenant_slug or not isinstance(tenant_slug, str) or not SLUG_RE.match(tenant_slug):
        raise ProvisionError(422, "invalid_tenant_slug", "tenant_slug must match ^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$")

    if not agent_kind or not isinstance(agent_kind, str) or not AGENT_KIND_RE.match(agent_kind):
        raise ProvisionError(422, "invalid_agent_kind", "agent_kind must match ^[a-z][a-z0-9_-]{0,31}$")

    # L-3 allowlist guard (defense-in-depth — Worker also enforces).
    # Substrate-only check first so the error is more diagnostic.
    if agent_kind in SUBSTRATE_ONLY_KINDS:
        raise ProvisionError(
            422,
            "not_forkable",
            f"agent_kind '{agent_kind}' is substrate-only and not forkable per S027 D-2 v1 allowlist",
        )
    if agent_kind not in ALLOWED_AGENT_KINDS:
        raise ProvisionError(
            422,
            "invalid_agent_kind",
            f"agent_kind '{agent_kind}' not in v1 allowlist {sorted(ALLOWED_AGENT_KINDS)}",
        )

    # Mutually-exclusive auth path validation
    if actor_token_hash is not None and actor_type is not None:
        raise ProvisionError(
            422,
            "invalid_actor",
            "body must include exactly one of {actor_token_hash, actor_type}, not both",
        )
    if actor_token_hash is None and actor_type is None:
        raise ProvisionError(
            422,
            "invalid_actor",
            "body must include exactly one of {actor_token_hash, actor_type}",
        )

    if actor_token_hash is not None:
        if not isinstance(actor_token_hash, str) or not TOKEN_HASH_RE.match(actor_token_hash):
            raise ProvisionError(422, "invalid_actor_token_hash", "actor_token_hash must be 64-char SHA-256 hex")

    if actor_type is not None:
        if actor_type != "platform-admin":
            raise ProvisionError(422, "invalid_actor_type", "actor_type must be 'platform-admin'")

    return {
        "tenant_id": tenant_id,
        "tenant_slug": tenant_slug,
        "agent_kind": agent_kind,
        "actor_token_hash": actor_token_hash,
        "actor_type": actor_type,
    }


# -----------------------------------------------------------------------
# L-1 (claim validator) — token claim resolution
# -----------------------------------------------------------------------
def _load_tokens() -> list[dict]:
    if not TOKENS_PATH.exists():
        return []
    try:
        data = json.loads(TOKENS_PATH.read_text())
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def validate_actor_token_claims(actor_token_hash: str, expected_tenant_slug: str) -> None:
    """Validate token claims for tenant-admin path.

    Looks up token by hash in tokens.json. Verifies:
      - record exists and is active
      - scope == "tenant"  (tenant-admin token from D-1b mint)
      - project == expected_tenant_slug  (cross-tenant attack guard)

    Raises ProvisionError on any mismatch. Returns None on success.
    """
    tokens = _load_tokens()
    matched: Optional[dict] = None
    for t in tokens:
        # Constant-time compare on the hash to avoid timing oracles even though hash space is large.
        stored_hash = t.get("token_hash", "")
        if isinstance(stored_hash, str) and constant_time_equal(stored_hash, actor_token_hash):
            matched = t
            break

    if matched is None:
        raise ProvisionError(401, "invalid_token", "actor_token_hash not found in registry")

    if not matched.get("active", True):
        raise ProvisionError(401, "invalid_token", "actor token is inactive")

    if matched.get("scope") != "tenant":
        raise ProvisionError(403, "invalid_scope", "actor token must have scope=tenant (tenant-admin token)")

    if matched.get("project") != expected_tenant_slug:
        # Cross-tenant attack: token-from-tenant-A presented for tenant-B activation
        raise ProvisionError(
            403,
            "tenant_id_mismatch",
            f"actor token belongs to tenant '{matched.get('project')}', "
            f"not '{expected_tenant_slug}'",
        )


# -----------------------------------------------------------------------
# L-2 — QNFT mint idempotent
# -----------------------------------------------------------------------
def _load_qnft_registry() -> dict:
    if not QNFT_REGISTRY_PATH.exists():
        return {}
    try:
        data = json.loads(QNFT_REGISTRY_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _generate_qnft_seed_and_vector(agent_name: str, tenant_slug: str, mint_date: str) -> tuple[str, list[float]]:
    """Generate QNFT seed (32-byte hex) + 16D vector deterministic from inputs.

    Mirror of mint-knight.py generate_qnft signature shape.
    """
    seed_hex = secrets.token_bytes(32).hex()
    h = hashlib.sha256(f"{agent_name}:{tenant_slug}:{mint_date}:{seed_hex}".encode()).digest()
    vector_16d = [
        round(((b0 << 8 | b1) / 65535.0) * 2.0 - 1.0, 6)
        for b0, b1 in zip(h[::2], h[1::2])
    ]
    return seed_hex, vector_16d


def mint_or_get_qnft(agent_name: str, agent_kind: str, tenant_slug: str) -> tuple[dict, bool]:
    """Idempotent QNFT mint for tenant-scoped agent fork.

    Returns (record, minted). minted=True if newly created, False if returned existing.
    Raises ProvisionError on filesystem error.

    Existing entry returned without re-mint. Corrupt/empty entries (missing seed_hex)
    are marked active=False before re-mint (P1-A discipline from D-1b iter-2 close).
    """
    try:
        registry = _load_qnft_registry()
        existing = registry.get(agent_name)

        if existing and isinstance(existing, dict) and existing.get("seed_hex"):
            return existing, False

        # Corrupt/missing entry: mark inactive in-place (preserve as forensic record)
        # before writing fresh record. New fresh record overrides the dict key.
        if existing and isinstance(existing, dict):
            # Forensic keep: relabel inactive copy under suffixed key
            inactive_key = f"{agent_name}__corrupt_{int(datetime.now(timezone.utc).timestamp())}"
            existing["active"] = False
            registry[inactive_key] = existing

        mint_date = now_iso()
        seed_hex, vector_16d = _generate_qnft_seed_and_vector(agent_name, tenant_slug, mint_date)

        new_record = {
            "seed_hex": seed_hex,
            "vector_16d": vector_16d,
            "descriptor": f"Tenant-scoped fork of {agent_kind} for tenant {tenant_slug}.",
            "cause": f"I serve {tenant_slug} only. I cannot read across tenants. "
                     f"My authority is bounded by the tenant-scope RLS gate.",
            "customer_slug": tenant_slug,
            "agent_kind": agent_kind,
            "tier": "operational",
            "minted_at": mint_date,
            "signer": "loom",
            "countersigned_by": None,
            "model_field": "sonnet-4-6",
        }
        registry[agent_name] = new_record
        atomic_write_json(QNFT_REGISTRY_PATH, registry)
        return new_record, True
    except OSError as e:
        raise ProvisionError(500, "qnft_io_error", f"qnft_registry.json IO: {e}") from e


# -----------------------------------------------------------------------
# L-3 — Bus token mint idempotent (scope=tenant-agent)
# -----------------------------------------------------------------------
def _find_tenant_agent_token(tokens: list[dict], agent_name: str) -> Optional[dict]:
    """Scan for active tenant-agent token. Match shape:
       agent == agent_name AND scope == "tenant-agent" AND active == True.
    """
    for t in tokens:
        if (
            t.get("agent") == agent_name
            and t.get("scope") == "tenant-agent"
            and t.get("active", True)
        ):
            return t
    return None


def mint_or_get_tenant_agent_token(
    agent_name: str, agent_kind: str, tenant_slug: str
) -> tuple[str, str, bool]:
    """Idempotent bus-token mint for tenant-scoped agent fork.

    Returns (raw_token, token_hash, minted).
    minted=True if newly created, False if returned existing plaintext.
    Raises ProvisionError on filesystem error.

    Token shape:
      scope: "tenant-agent" (new vocabulary, three discriminators)
      tenant_slug: <tenant>
      agent_kind: <kind>
      agent: <agent_name>  (== "{agent_kind}-{tenant_slug}")
    """
    try:
        tokens = _load_tokens()
        existing = _find_tenant_agent_token(tokens, agent_name)
        if existing:
            existing_token = existing.get("token", "")
            existing_hash = existing.get("token_hash", "")
            if existing_token and existing_hash:
                return existing_token, existing_hash, False
            raise ProvisionError(
                500,
                "bus_token_plaintext_missing",
                f"existing tenant-agent token for {agent_name} has no plaintext; "
                "manual remint required",
            )

        raw_token = f"sk-{agent_name}-{secrets.token_hex(16)}"
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        new_record = {
            "token": raw_token,
            "token_hash": token_hash,
            "project": tenant_slug,
            "label": f"{agent_name} tenant-scoped agent",
            "active": True,
            "created_at": now_iso(),
            "agent": agent_name,
            "scope": "tenant-agent",
            "tenant_slug": tenant_slug,
            "agent_kind": agent_kind,
            "role": "agent",
        }
        tokens.append(new_record)
        atomic_write_json(TOKENS_PATH, tokens)
        return raw_token, token_hash, True
    except OSError as e:
        raise ProvisionError(500, "bus_token_io_error", f"tokens.json IO: {e}") from e


# -----------------------------------------------------------------------
# L-4 — Routing register idempotent
# -----------------------------------------------------------------------
def _load_agent_routing() -> dict:
    if not DYNAMIC_ROUTING_PATH.exists():
        return {}
    try:
        data = json.loads(DYNAMIC_ROUTING_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def register_or_skip_routing(agent_name: str, routing: str = "tmux") -> bool:
    """Idempotent agent routing registration.

    Returns True if newly registered or value updated, False if already correct.
    Raises ProvisionError on filesystem error.

    Atomic temp-rename preserves existing routes.
    """
    try:
        routes = _load_agent_routing()
        if routes.get(agent_name) == routing:
            return False
        routes[agent_name] = routing
        atomic_write_json(DYNAMIC_ROUTING_PATH, routes)
        return True
    except OSError as e:
        raise ProvisionError(500, "routing_io_error", f"agent_routing.json IO: {e}") from e


# -----------------------------------------------------------------------
# L-5 — Scaffold idempotent (per-kind template render)
# -----------------------------------------------------------------------
def _render_agent_template(
    content: str,
    agent_name: str,
    agent_kind: str,
    tenant_slug: str,
    tenant_display_name: str,
    industry: str,
    qnft_seed_hex: str,
    mint_date: str,
) -> str:
    return (
        content
        .replace("{{AGENT_NAME}}", agent_name)
        .replace("{{AGENT_KIND}}", agent_kind)
        .replace("{{TENANT_SLUG}}", tenant_slug)
        .replace("{{TENANT_DISPLAY_NAME}}", tenant_display_name)
        .replace("{{INDUSTRY}}", industry)
        .replace("{{QNFT_SEED_HEX}}", qnft_seed_hex)
        .replace("{{MINT_DATE}}", mint_date)
    )


def scaffold_or_skip_agent_fork(
    agent_name: str,
    agent_kind: str,
    tenant_slug: str,
    tenant_display_name: str,
    industry: str,
    qnft_seed_hex: str,
    mint_date: str,
) -> tuple[Path, bool]:
    """Idempotent tenant-scoped agent fork scaffold.

    Renders SOS/scripts/agent-fork-templates/{agent_kind}.md to
    ~/.mumega/customers/{tenant_slug}/agents/{agent_kind}/CLAUDE.md.

    Returns (path, created). created=True if CLAUDE.md was newly written, False if it already existed.
    Existing CLAUDE.md is NOT overwritten (preserves tenant-admin edits).
    Raises ProvisionError on missing template or IO failure.
    """
    try:
        template_path = AGENT_FORK_TEMPLATES_DIR / f"{agent_kind}.md"
        if not template_path.exists():
            raise ProvisionError(
                500,
                "template_missing",
                f"agent-fork template missing: {template_path} — substrate misconfigured",
            )

        target_dir = CUSTOMERS_DIR / tenant_slug / "agents" / agent_kind
        target_dir.mkdir(parents=True, exist_ok=True)

        dest = target_dir / "CLAUDE.md"
        if dest.exists():
            return dest, False

        content = template_path.read_text(encoding="utf-8")
        rendered = _render_agent_template(
            content,
            agent_name=agent_name,
            agent_kind=agent_kind,
            tenant_slug=tenant_slug,
            tenant_display_name=tenant_display_name,
            industry=industry,
            qnft_seed_hex=qnft_seed_hex,
            mint_date=mint_date,
        )
        dest.write_text(rendered, encoding="utf-8")
        return dest, True
    except OSError as e:
        raise ProvisionError(500, "scaffold_io_error", f"scaffold IO: {e}") from e


# -----------------------------------------------------------------------
# L-6 — Tenant display_name + industry resolution
# -----------------------------------------------------------------------
def _resolve_tenant_metadata(tenant_slug: str) -> tuple[str, str]:
    """Resolve display_name + industry for tenant from existing customer scaffold or defaults.

    D-2b can't reach D1, so it reads from the D-1b-rendered scaffold at
    ~/.mumega/customers/{tenant_slug}/ which contains tenant metadata.

    Returns (display_name, industry). Falls back to (tenant_slug, "general") if
    scaffold missing — agent fork can be activated even before D-1b finished
    (operational order: D-1b runs first; D-2b only activates after).
    """
    customer_dir = CUSTOMERS_DIR / tenant_slug
    metadata_path = customer_dir / ".tenant.json"
    if metadata_path.exists():
        try:
            data = json.loads(metadata_path.read_text())
            return (
                data.get("display_name", tenant_slug),
                data.get("industry", "general"),
            )
        except (json.JSONDecodeError, OSError):
            pass
    return tenant_slug, "general"


# -----------------------------------------------------------------------
# Orchestrator — top-level activation
# -----------------------------------------------------------------------
def activate_tenant_agent(body: dict) -> dict:
    """Orchestrate the full tenant agent activation flow.

    Steps (in order):
      1. Validate body (allowlist + auth-path mutual exclusion)
      2. If actor_token_hash path: validate token claims (scope+slug match)
         If platform-admin path: skip token validation (Worker pre-validated)
      3. QNFT mint or get
      4. Bus token mint or get
      5. Routing register or skip
      6. Scaffold or skip (renders agent-fork CLAUDE.md per kind)

    Returns dict with D1-payload fields for Worker INSERT + idempotency flags.
    Raises ProvisionError on any structural failure.
    """
    sanitized = validate_activation_body(body)
    tenant_slug = sanitized["tenant_slug"]
    agent_kind = sanitized["agent_kind"]

    # L-1 (claim validator) — only for tenant-admin path
    if sanitized["actor_token_hash"] is not None:
        validate_actor_token_claims(sanitized["actor_token_hash"], tenant_slug)
    # platform-admin path: Worker pre-validated ADMIN_API_SECRET; URL :id authoritative

    agent_name = f"{agent_kind}-{tenant_slug}"

    # Resolve tenant metadata for scaffold rendering
    display_name, industry = _resolve_tenant_metadata(tenant_slug)

    # L-2 — QNFT
    qnft_record, qnft_minted = mint_or_get_qnft(agent_name, agent_kind, tenant_slug)

    # L-3 — Bus token
    raw_token, token_hash, token_minted = mint_or_get_tenant_agent_token(
        agent_name, agent_kind, tenant_slug
    )

    # L-4 — Routing
    routing_registered = register_or_skip_routing(agent_name, routing="tmux")

    # L-5 — Scaffold
    scaffold_path, scaffold_created = scaffold_or_skip_agent_fork(
        agent_name=agent_name,
        agent_kind=agent_kind,
        tenant_slug=tenant_slug,
        tenant_display_name=display_name,
        industry=industry,
        qnft_seed_hex=qnft_record["seed_hex"],
        mint_date=qnft_record["minted_at"],
    )

    # L-6 — D1 payload for Worker INSERT
    return {
        "tenant_id": sanitized["tenant_id"],
        "tenant_slug": tenant_slug,
        "agent_kind": agent_kind,
        "agent_name": agent_name,
        "qnft_seed_hex": qnft_record["seed_hex"],
        "token_hash": token_hash,
        "scaffold_path": str(scaffold_path),
        "idempotency": {
            "qnft_minted": qnft_minted,
            "token_minted": token_minted,
            "routing_registered": routing_registered,
            "scaffold_created": scaffold_created,
        },
    }
