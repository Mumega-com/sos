"""
S028 C-1b (D-3b) — Tenant CUSTOM agent mint logic for SOS-side companion endpoint.

Surface: POST /api/internal/tenants/:id/agents/mint (mounted on bridge.py)

Brief: agents/kasra/briefs/kasra-s028-c1-tenant-agent-mint.md (REVISED, GREEN)
Athena brief-shape gate GREEN 2026-05-05T10:09:53Z (kasra_s028_c1_brief_gate_001).

Difference from D-2b (tenant_agent_activation.py):
  D-2b:  agent_kind ∈ {athena|kasra|calliope} → fork-mint of canonical scaffold
         signer='loom', tier='operational', countersigned_by=None
         agent_name = "{agent_kind}-{tenant_slug}" (server-derived)
  D-3b:  agent_name tenant-supplied (any non-reserved); model + role + charter +
         voice_rules tenant-supplied; signer='tenant-admin',
         tier='tenant-custom', countersigned_by=None initially.
         Scaffold CLAUDE.md rendered from tenant charter (NOT canonical template).

LOCK invariants (paired with Worker LOCK-S028-C-1):
  - L-1b LOCK-D-3b-internal-bearer-fail-closed + tenant-token-claim-validator
  - L-2b LOCK-D-3b-qnft-mint-idempotent (tenant-signed, tier='tenant-custom')
  - L-3b LOCK-D-3b-bus-token-mint-idempotent (three-discriminator RLS)
  - L-4b LOCK-D-3b-routing-register-idempotent
  - L-5b LOCK-D-3b-scaffold-idempotent + charter-injection-defense
  - L-6b LOCK-D-3b-response-shape-carries-d1-payload
  - L-7b LOCK-D-3b-bus-layer-rls-three-discriminator (enforced in delivery.py)
  - L-8b LOCK-D-3b-reserved-name-defense-in-depth (Worker also enforces)
  - L-9b LOCK-D-3b-charter-bytes-cap-defense-in-depth (Worker also enforces)

Worker = opaque pipe contract (ADV-3): the Worker stores charter/voice_rules as
opaque TEXT and never reflects/renders them. THIS module is the injection-
defense site:
  - charter is written to CLAUDE.md as raw markdown but NOT processed as a
    template (no {{...}} substitution, no shell interpolation, no exec).
  - voice_rules likewise stored as raw markdown.
  - Path components (tenant_slug, agent_name) are validated by regex BEFORE
    any os.path operation; they cannot contain '..', '/', or NULL bytes.

Atomic-write discipline: all JSON file writes use temp-file-and-rename pattern.
Mirrors D-2b shape (paired LOCK family).
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

# Reuse D-2b QNFT registry + routing paths so all tenant-scoped agents
# (fork-mints AND custom-mints) live in a single registry — one identity space
# governed by signer/tier discriminators rather than separate files.
from sos.bus.tenant_agent_activation import (
    CUSTOMERS_DIR,
    DYNAMIC_ROUTING_PATH,
    QNFT_REGISTRY_PATH,
    _generate_qnft_seed_and_vector,
    _load_agent_routing,
    _load_qnft_registry,
    _load_tokens,
)

# Reuse D-1b/D-2b primitives.
from sos.bus.tenant_provisioning import (
    SLUG_RE,
    TENANT_ID_RE,
    TOKENS_PATH,
    ProvisionError,
    _runtime_path,
    atomic_write_json,
    bus_state_lock_path,
    constant_time_equal,
    now_iso,
)
from sos.bus.token_store import hash_token, write_tokens_atomic

# -----------------------------------------------------------------------
# L-3 / L-8 RESERVED_NAMES — keep in sync with workers/inkwell-api/src/routes/tenants-agent-mint.ts
# -----------------------------------------------------------------------
SUBSTRATE_ONLY_KINDS: set[str] = {"loom", "river", "mizan", "sol", "hermes", "codex"}
FORK_ALLOWLIST: set[str] = {"athena", "kasra", "calliope"}
RESERVED_WORDS: set[str] = {
    "hadi", "root", "admin", "sos", "mumega", "system",
    "bus", "bridge", "internal", "platform", "oauth", "mcp",
}
RESERVED_NAMES: set[str] = SUBSTRATE_ONLY_KINDS | FORK_ALLOWLIST | RESERVED_WORDS

# ADV-C1-3 (P1, ADV-parallel close) — prefix-match defense closes hyphen/digit
# variants of substrate kinds + high-value reserved words. Catches `loom-1`,
# `mumega2`, `kasra-acme` (D-2 fork shadow), `hadi-bot`, `admin-tool`.
# Distinct from RESERVED_NAMES (exact match) — both gates must be passed.
RESERVED_PREFIX_RE = re.compile(
    r"^(loom|river|mizan|sol|hermes|codex|athena|kasra|calliope|"
    r"hadi|mumega|admin|root|sos|system|bus|bridge|internal|platform|oauth|mcp)"
    r"([-_]|\d)"
)

# L-9 ALLOWED_MODELS — keep in sync with Worker route hardcoded const.
# Hardcoded (NOT env-config) per brief P2-A: model allowlist is an adversarial
# surface; config-in-env silently expands without code review gate.
ALLOWED_MODELS: set[str] = {
    "claude-sonnet-4-6",
    "claude-opus-4-7",
    "claude-haiku-4-5-20251001",
    "gemini-2.5-flash",
}

# -----------------------------------------------------------------------
# ADV-C1-5 (P1, ADV-parallel close) — RMW lock for bus state.
# -----------------------------------------------------------------------
# bridge.py runs ThreadingHTTPServer; concurrent mint requests for the same
# (tenant_slug, agent_name) pair would otherwise both observe missing-entry
# in qnft_registry/tokens/routing and both write fresh seeds/tokens. The
# atomic_write_json os.replace primitive only protects against torn writes,
# not against lost updates under concurrent read-modify-write.
#
# Mitigation: cross-thread/cross-process advisory flock on a sentinel file
# in SOS_BUS_DIR. The orchestrator (mint_tenant_custom_agent) acquires the
# lock once and releases at end-of-flow; the inner RMW primitives are
# already serialized under that lock. Mints are infrequent (admin-driven),
# so substrate-wide single lock is acceptable.


@contextlib.contextmanager
def _bus_state_lock() -> Iterator[None]:
    """Cross-thread / cross-process exclusive lock for bus state RMW.

    Holds fcntl.LOCK_EX on a sentinel file. Required around any sequence of
    operations that read-modify-write qnft_registry.json, tokens.json, or
    agent_routing.json.
    """
    lock_path = bus_state_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# -----------------------------------------------------------------------
# Validation
# -----------------------------------------------------------------------
AGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{2,31}$")
TOKEN_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
ROLE_RE = re.compile(r"^[^\r\n]{1,200}$")  # one-line, ≤200 chars

# L-8 / L-9b byte caps (must match Worker route).
MAX_BODY_BYTES = 16 * 1024
MAX_CHARTER_BYTES = 8 * 1024
MAX_VOICE_RULES_BYTES = 4 * 1024


def _utf8_len(s: str) -> int:
    return len(s.encode("utf-8"))


def validate_mint_body(body: dict) -> dict:
    """L-1b/L-3/L-8/L-9 paired guard: validate body BEFORE any disk write.

    Returns sanitized body dict. Raises ProvisionError on bad input.

    Body shape (mutually exclusive auth paths):
      {tenant_id, tenant_slug, agent_name, model, role, charter, voice_rules,
       actor_token_hash}                            (tenant-admin path)
      {tenant_id, tenant_slug, agent_name, model, role, charter, voice_rules,
       actor_type: "platform-admin"}                (platform-admin path)
    """
    if not isinstance(body, dict):
        raise ProvisionError(422, "invalid_body", "body must be a JSON object")

    tenant_id = body.get("tenant_id")
    tenant_slug = body.get("tenant_slug")
    agent_name = body.get("agent_name")
    model = body.get("model")
    role = body.get("role")
    charter = body.get("charter")
    voice_rules = body.get("voice_rules")
    actor_token_hash = body.get("actor_token_hash")
    actor_type = body.get("actor_type")

    if not tenant_id or not isinstance(tenant_id, str) or not TENANT_ID_RE.match(tenant_id):
        raise ProvisionError(422, "invalid_tenant_id", "tenant_id must match ^[a-zA-Z0-9._-]{1,64}$")

    if not tenant_slug or not isinstance(tenant_slug, str) or not SLUG_RE.match(tenant_slug):
        raise ProvisionError(422, "invalid_tenant_slug", "tenant_slug must match canonical slug regex")

    # Normalize agent_name (lowercase + trim) for case-variant defense.
    if not isinstance(agent_name, str):
        raise ProvisionError(422, "invalid_agent_name", "agent_name must be a string")
    agent_name_norm = agent_name.strip().lower()
    if not AGENT_NAME_RE.match(agent_name_norm):
        raise ProvisionError(
            422,
            "invalid_agent_name",
            "agent_name must match ^[a-z][a-z0-9_-]{2,31}$",
        )
    # L-8b — reserved-name defense-in-depth.
    if agent_name_norm in RESERVED_NAMES:
        raise ProvisionError(
            422,
            "reserved_name",
            f"agent_name '{agent_name_norm}' is reserved (substrate-only / fork-allowlist / reserved-word)",
        )
    # ADV-C1-3 — prefix-match defense (`loom-1`, `mumega2`, `kasra-acme`, etc.).
    if RESERVED_PREFIX_RE.match(agent_name_norm):
        raise ProvisionError(
            422,
            "reserved_name",
            f"agent_name '{agent_name_norm}' starts with reserved prefix; choose a name not derivable from a substrate kind or operator identifier",
        )

    # L-9b — model allowlist defense-in-depth.
    if not isinstance(model, str) or not model:
        raise ProvisionError(422, "model_required", "model is required")
    # ADV-C1-7 (P2 carry-fix) — strip whitespace so audit-log and validate-then-write
    # surfaces see the same canonical value. Worker also normalizes; defense-in-depth.
    model = model.strip()
    if model not in ALLOWED_MODELS:
        raise ProvisionError(
            422,
            "model_not_allowed",
            f"model '{model}' not in v1 allowlist {sorted(ALLOWED_MODELS)}",
        )

    if not isinstance(role, str) or not role.strip():
        raise ProvisionError(422, "role_required", "role is required")
    role_stripped = role.strip()
    if not ROLE_RE.match(role_stripped):
        raise ProvisionError(422, "invalid_role_format", "role must be one line ≤200 chars")

    if not isinstance(charter, str) or not charter:
        raise ProvisionError(422, "charter_required", "charter is required")
    if _utf8_len(charter) > MAX_CHARTER_BYTES:
        raise ProvisionError(413, "body_too_large", f"charter exceeds {MAX_CHARTER_BYTES} bytes")

    if not isinstance(voice_rules, str) or not voice_rules:
        raise ProvisionError(422, "voice_rules_required", "voice_rules is required")
    if _utf8_len(voice_rules) > MAX_VOICE_RULES_BYTES:
        raise ProvisionError(413, "body_too_large", f"voice_rules exceeds {MAX_VOICE_RULES_BYTES} bytes")

    # Mutually-exclusive auth path validation.
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
        "agent_name": agent_name_norm,
        "model": model,
        "role": role_stripped,
        "charter": charter,
        "voice_rules": voice_rules,
        "actor_token_hash": actor_token_hash,
        "actor_type": actor_type,
    }


# -----------------------------------------------------------------------
# L-1b (claim validator) — token claim resolution (mirrors D-2b shape)
# -----------------------------------------------------------------------
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
        raise ProvisionError(
            403,
            "tenant_id_mismatch",
            f"actor token belongs to tenant '{matched.get('project')}', not '{expected_tenant_slug}'",
        )


# -----------------------------------------------------------------------
# L-2b — QNFT mint idempotent (tenant-signed, tier='tenant-custom')
# -----------------------------------------------------------------------
# ADV-C1-1 (P0, ADV-parallel close) — registry key three-discriminator format:
#   `custom:{tenant_slug}:{agent_name}` for tenant-custom mints.
# Distinguishes from D-2 fork-mint entries (which use plain `{kind}-{slug}` as
# the key) and ensures cross-tenant isolation: two tenants picking the same
# `agent_name` get distinct registry slots. The substrate-side identity state
# is now keyed by the same three discriminators as the bus token claims, not
# only the claims field. ADV-C1-2 (D-2/D-3 namespace shadow) is closed by the
# same patch — even if a tenant chose `kasra-acme` (still rejected at the
# RESERVED_PREFIX_RE gate above; this is depth-2 defense), the registry slot
# `custom:wile:kasra-acme` is distinct from D-2's `kasra-acme`.
def _custom_registry_key(tenant_slug: str, agent_name: str) -> str:
    return f"custom:{tenant_slug}:{agent_name}"


def mint_or_get_custom_qnft(
    agent_name: str,
    tenant_slug: str,
    model: str,
    role: str,
) -> tuple[dict, bool]:
    """Idempotent QNFT mint for tenant-custom agent.

    Returns (record, minted). minted=True if newly created, False if returned existing.
    Existing entry returned without re-mint. Corrupt entries (missing seed_hex)
    are sidelined under a __corrupt_<ts> key (P1-A discipline from D-1b).

    Differences from D-2b mint_or_get_qnft:
      - signer='tenant-admin' (vs 'loom')
      - tier='tenant-custom' (vs 'operational')
      - agent_kind = agent_name (no canonical kind)
      - model_field = tenant-supplied
      - descriptor + cause = tenant-custom phrasing
      - registry_key = `custom:{tenant_slug}:{agent_name}` (ADV-C1-1 isolation)
    """
    try:
        registry = _load_qnft_registry()
        registry_key = _custom_registry_key(tenant_slug, agent_name)
        existing = registry.get(registry_key)

        if existing and isinstance(existing, dict) and existing.get("seed_hex"):
            return existing, False

        if existing and isinstance(existing, dict):
            inactive_key = f"{registry_key}__corrupt_{int(datetime.now(timezone.utc).timestamp())}"
            existing["active"] = False
            registry[inactive_key] = existing

        mint_date = now_iso()
        seed_hex, vector_16d = _generate_qnft_seed_and_vector(agent_name, tenant_slug, mint_date)

        new_record = {
            "seed_hex": seed_hex,
            "vector_16d": vector_16d,
            "descriptor": f"Tenant-custom agent '{agent_name}' for tenant {tenant_slug} ({role}).",
            "cause": (
                f"I serve {tenant_slug} only. I am a tenant-defined agent, not a "
                f"canonical Mumega kind. My authority is bounded by the tenant-scope "
                f"three-discriminator RLS gate."
            ),
            "customer_slug": tenant_slug,
            "agent_name": agent_name,  # ADV-C1-1 — explicit field for cross-discriminator filter
            "tenant_slug": tenant_slug,  # ADV-C1-1 — explicit field for cross-discriminator filter
            "agent_kind": agent_name,  # custom mints have no canonical kind
            "tier": "tenant-custom",
            "minted_at": mint_date,
            "signer": "tenant-admin",
            "countersigned_by": None,  # Loom countersign for canonical tier deferred S028+
            "model_field": model,
        }
        registry[registry_key] = new_record
        qnft_path = _runtime_path(
            "SOS_QNFT_PATH",
            QNFT_REGISTRY_PATH,
            artifact="qnft_registry.json",
        )
        atomic_write_json(qnft_path, registry)
        return new_record, True
    except OSError as e:
        raise ProvisionError(500, "qnft_io_error", f"qnft_registry.json IO: {e}") from e


# -----------------------------------------------------------------------
# L-3b — Bus token mint idempotent (scope=tenant-agent, three-discriminator)
# -----------------------------------------------------------------------
def _find_custom_tenant_agent_token(
    tokens: list[dict], agent_name: str, tenant_slug: str
) -> Optional[dict]:
    """Scan for active tenant-custom agent token. Match shape:
       agent == agent_name AND scope == "tenant-agent" AND
       agent_kind == "custom" AND tenant_slug == tenant_slug AND active.

    ADV-C1-1 (P0, ADV-parallel close): the tenant_slug predicate is REQUIRED.
    Without it, two tenants choosing the same `agent_name` would receive each
    other's bus tokens (cross-tenant token reuse via shared registry).
    """
    for t in tokens:
        if (
            t.get("agent") == agent_name
            and t.get("scope") == "tenant-agent"
            and t.get("agent_kind") == "custom"
            and t.get("tenant_slug") == tenant_slug
            and t.get("active", True)
        ):
            return t
    return None


def mint_or_get_custom_tenant_agent_token(
    agent_name: str, tenant_slug: str
) -> tuple[str, str, bool]:
    """Idempotent bus-token mint for tenant-custom agent.

    Returns (raw_token, token_hash, minted).

    Token shape (three-discriminator RLS keys: tenant_slug + 'custom' + agent_name):
      scope: "tenant-agent"
      tenant_slug: <tenant>
      agent_kind: "custom"  (literal sentinel, distinguishes from fork-mint where agent_kind=athena|kasra|calliope)
      agent: <agent_name>
      role: "agent"
    """
    try:
        tokens = _load_tokens()
        existing = _find_custom_tenant_agent_token(tokens, agent_name, tenant_slug)
        if existing:
            existing_token = existing.get("token", "")
            existing_hash = existing.get("token_hash", "")
            if existing_token and existing_hash:
                return existing_token, existing_hash, False
            raise ProvisionError(
                500,
                "bus_token_plaintext_missing",
                f"existing tenant-custom token for {agent_name} has no plaintext; manual remint required",
        )

        raw_token = f"sk-{agent_name}-{secrets.token_hex(16)}"
        token_hash = hash_token(raw_token)
        new_record = {
            "token": raw_token,
            "token_hash": token_hash,
            "project": tenant_slug,
            "label": f"{agent_name} tenant-custom agent",
            "active": True,
            "created_at": now_iso(),
            "agent": agent_name,
            "scope": "tenant-agent",
            "tenant_slug": tenant_slug,
            "agent_kind": "custom",  # three-discriminator sentinel
            "role": "agent",
            # S156 fix: full scope set so MCP dispatcher gate (bus:read) and
            # tool-visibility check (permissions) both pass for new tenant agents.
            # "bus:send" alone → 0 tools visible (dispatcher gates inbox/peers on
            # bus:read; boot_context/status on health). "mcp:*" is the wildcard
            # that _tool_allowed_by_permissions() honors, matching every tool name.
            # This matches the working pattern of D-2b fork-mint agents in
            # tokens.json. Only D-3b custom-agent tokens are affected — human/
            # operator/customer tokens follow separate mint paths with no change here.
            "scopes": ["bus:send", "bus:read", "health"],
            "permissions": ["mcp:*"],
        }
        tokens.append(new_record)
        token_path = _runtime_path(
            "SOS_BUS_TOKENS_PATH",
            TOKENS_PATH,
            artifact="tokens.json",
        )
        write_tokens_atomic(token_path, tokens)
        return raw_token, token_hash, True
    except OSError as e:
        raise ProvisionError(500, "bus_token_io_error", f"tokens.json IO: {e}") from e


# -----------------------------------------------------------------------
# L-4b — Routing register idempotent
# -----------------------------------------------------------------------
# ADV-C1-1 (P0, ADV-parallel close) — namespace tenant-custom routing keys
# as `custom:{tenant_slug}:{agent_name}`. Two tenants can pick the same
# `agent_name`; the global agent_routing.json must not collapse them into a
# single entry. The delivery layer (sos.services.bus.delivery._load_dynamic_routing)
# looks up by bare `agent` name — namespaced keys correctly fall through to
# the substrate default ("tmux") because tenant-custom agents are not
# substrate-side processes; they're addressed via the bus delivery RLS gate
# on token claims (tenant_slug + 'custom' + agent_name).
def register_or_skip_routing(
    agent_name: str, tenant_slug: str, routing: str = "tenant-bus"
) -> bool:
    """Idempotent tenant-custom agent routing registration.

    Returns True if newly registered or value updated, False if already correct.
    Key format: `custom:{tenant_slug}:{agent_name}` (ADV-C1-1 isolation).
    Routing value: `tenant-bus` (not tmux/openclaw — these agents are
    addressed via bus delivery, not substrate wake).
    """
    try:
        routes = _load_agent_routing()
        key = _custom_registry_key(tenant_slug, agent_name)
        if routes.get(key) == routing:
            return False
        routes[key] = routing
        atomic_write_json(DYNAMIC_ROUTING_PATH, routes)
        return True
    except OSError as e:
        raise ProvisionError(500, "routing_io_error", f"agent_routing.json IO: {e}") from e


# -----------------------------------------------------------------------
# L-5b — Scaffold idempotent + CHARTER-INJECTION-DEFENSE SITE
# -----------------------------------------------------------------------
# ADV-C1-4 (P1, ADV-parallel close) — charter/voice_rules can contain markdown
# headers that would otherwise be parsed as section boundaries by Claude Code
# reading CLAUDE.md. Mitigation: escape ATX-style headers at line-start by
# prefixing `\` (CommonMark escape) so `## Bus identity` renders as literal
# text, not a section. Combined with the substrate footer placement (after
# tenant content), this prevents shadow-header injection of fake `## Bus
# identity` / `## Promotion` / `# {agent}` sections.
# ADV-C1-8 (WARN, ADV-parallel close) — strip Unicode bidi/zero-width marks
# that cause display-vs-bytes drift in Claude Code's markdown reader.
_HEADER_LINESTART_RE = re.compile(r"(?m)^(\s*)(#+)(\s)")
_BIDI_ZWSP_RE = re.compile(r"[\u200B-\u200F\u202A-\u202E\uFEFF]")


def _sanitize_tenant_markdown(s: str) -> str:
    """Defense-in-depth scrub of tenant-supplied markdown content.

    Steps:
      1. Strip bidi/zero-width Unicode marks (ADV-C1-8).
      2. Escape ATX-style header markers at line-start (ADV-C1-4) so injected
         `## Bus identity` renders as literal text, not a section header.

    Idempotent in normal use: the escape pattern matches ``^\\s*#+\\s`` and
    the inserted backslash does not match ``#``.
    """
    s = _BIDI_ZWSP_RE.sub("", s)
    s = _HEADER_LINESTART_RE.sub(r"\1\\\2\3", s)
    return s


# Per ADV-3 contract (brief §2.4): Worker = opaque pipe, D-3b = injection-defense.
# This function is the SITE where charter content meets the filesystem. It must:
#   1. Validate tenant_slug + agent_name match strict regex BEFORE any path op
#      (defense against '..' traversal, NULL byte injection, slashes).
#   2. Treat charter + voice_rules as RAW MARKDOWN. No template substitution
#      ({{...}} replacement is NOT applied to tenant-supplied content). No shell
#      interpolation. No exec. No eval. The CLAUDE.md is read by Claude Code,
#      which does NOT execute embedded markdown.
#   3. Refuse to overwrite an existing CLAUDE.md (preserves operator edits +
#      forecloses re-mint-rewrite attacks per ADV-6).

def _build_custom_claude_md(
    agent_name: str,
    tenant_slug: str,
    role: str,
    model: str,
    charter: str,
    voice_rules: str,
    qnft_seed_hex: str,
    mint_date: str,
) -> str:
    """Compose CLAUDE.md content for a tenant-custom agent.

    Charter + voice_rules + role are tenant-supplied opaque markdown. They are
    inlined directly with NO template substitution applied to them. The
    HEADER + FOOTER are the only substrate-controlled parts and use plain
    string concatenation (no .format / no .replace on user content).
    """
    header_lines = [
        f"# {agent_name}",
        "",
        f"**Tenant:** `{tenant_slug}`",
        f"**Role:** {role}",
        f"**Model:** `{model}`",
        "**Tier:** `tenant-custom` (signer: tenant-admin; countersigned_by: none)",
        f"**QNFT seed:** `{qnft_seed_hex}`",
        f"**Minted:** {mint_date}",
        "",
        "---",
        "",
        "## Charter",
        "",
    ]
    middle_lines = [
        "",
        "## Voice rules",
        "",
    ]
    footer_lines = [
        "",
        "---",
        "",
        "## Bus identity",
        "",
        f"- Agent: `{agent_name}`",
        f"- Scope: `tenant-agent` / tenant_slug=`{tenant_slug}` / agent_kind=`custom`",
        "- RLS: three-discriminator. Cross-tenant sends from this token are blocked at the bus.",
        "",
        "## Promotion",
        "",
        "- This agent is at `tier='tenant-custom'`. Promotion to `tier='canonical'`",
        "  requires a Loom countersign via the dedicated countersign endpoint",
        "  (deferred to S028+ per brief §2.3 L-10).",
        "",
    ]
    # ADV-C1-4 + ADV-C1-8 — sanitize tenant content before scaffold-write.
    safe_charter = _sanitize_tenant_markdown(charter)
    safe_voice_rules = _sanitize_tenant_markdown(voice_rules)
    return (
        "\n".join(header_lines)
        + safe_charter
        + "\n".join(middle_lines)
        + safe_voice_rules
        + "\n".join(footer_lines)
    )


def scaffold_or_skip_custom_agent(
    agent_name: str,
    tenant_slug: str,
    role: str,
    model: str,
    charter: str,
    voice_rules: str,
    qnft_seed_hex: str,
    mint_date: str,
) -> tuple[Path, bool]:
    """Idempotent tenant-custom agent scaffold.

    Writes ~/.mumega/customers/{tenant_slug}/agents/{agent_name}/CLAUDE.md
    with content composed from substrate-controlled header + tenant-supplied
    charter + voice_rules. Existing CLAUDE.md is NOT overwritten.

    Path-traversal defense: regexes enforced at validate_mint_body() reject
    any agent_name with '..' or '/' or NULL bytes. Defense-in-depth re-checks
    here too.
    """
    # Defense-in-depth path-traversal guards.
    if not SLUG_RE.match(tenant_slug):
        raise ProvisionError(422, "invalid_tenant_slug", "tenant_slug failed defense-in-depth check")
    if not AGENT_NAME_RE.match(agent_name):
        raise ProvisionError(422, "invalid_agent_name", "agent_name failed defense-in-depth check")

    try:
        target_dir = CUSTOMERS_DIR / tenant_slug / "agents" / agent_name
        target_dir.mkdir(parents=True, exist_ok=True)

        dest = target_dir / "CLAUDE.md"
        if dest.exists():
            return dest, False

        rendered = _build_custom_claude_md(
            agent_name=agent_name,
            tenant_slug=tenant_slug,
            role=role,
            model=model,
            charter=charter,
            voice_rules=voice_rules,
            qnft_seed_hex=qnft_seed_hex,
            mint_date=mint_date,
        )
        dest.write_text(rendered, encoding="utf-8")
        return dest, True
    except OSError as e:
        raise ProvisionError(500, "scaffold_io_error", f"scaffold IO: {e}") from e


# -----------------------------------------------------------------------
# Orchestrator — top-level mint
# -----------------------------------------------------------------------
def mint_tenant_custom_agent(body: dict) -> dict:
    """Orchestrate the full tenant-custom agent mint flow.

    Steps (in order):
      1. Validate body (regex + reserved-names + model allowlist + byte caps +
         auth-path mutual exclusion).
      2. If actor_token_hash path: validate token claims (scope+slug match).
         If platform-admin path: skip token validation (Worker pre-validated).
      3. QNFT mint or get (tenant-signed, tier='tenant-custom').
      4. Bus token mint or get (three-discriminator RLS).
      5. Routing register or skip.
      6. Scaffold or skip (renders CLAUDE.md from tenant charter + voice_rules).

    Returns dict with D1-payload fields for Worker INSERT + idempotency flags.
    Raises ProvisionError on any structural failure.
    """
    sanitized = validate_mint_body(body)
    tenant_slug = sanitized["tenant_slug"]
    agent_name = sanitized["agent_name"]

    # L-1b (claim validator) — only for tenant-admin path.
    if sanitized["actor_token_hash"] is not None:
        validate_actor_token_claims(sanitized["actor_token_hash"], tenant_slug)
    # platform-admin path: Worker pre-validated ADMIN_API_SECRET; URL :id authoritative.

    # ADV-C1-5 — serialize all RMW operations on bus state under a single
    # exclusive flock. Two concurrent mints of the same (tenant, agent_name)
    # are now ordered: second observer sees the first's writes and idempotent-
    # returns, instead of both racing to insert.
    with _bus_state_lock():
        # L-2b — QNFT
        qnft_record, qnft_minted = mint_or_get_custom_qnft(
            agent_name=agent_name,
            tenant_slug=tenant_slug,
            model=sanitized["model"],
            role=sanitized["role"],
        )

        # L-3b — Bus token
        raw_token, token_hash, token_minted = mint_or_get_custom_tenant_agent_token(
            agent_name=agent_name,
            tenant_slug=tenant_slug,
        )

        # L-4b — Routing (ADV-C1-1 namespaced; tenant-bus value, not tmux)
        routing_registered = register_or_skip_routing(
            agent_name=agent_name, tenant_slug=tenant_slug, routing="tenant-bus"
        )

        # L-5b — Scaffold (charter-injection-defense site)
        scaffold_path, scaffold_created = scaffold_or_skip_custom_agent(
            agent_name=agent_name,
            tenant_slug=tenant_slug,
            role=sanitized["role"],
            model=sanitized["model"],
            charter=sanitized["charter"],
            voice_rules=sanitized["voice_rules"],
            qnft_seed_hex=qnft_record["seed_hex"],
            mint_date=qnft_record["minted_at"],
        )

    # L-6b — D1 payload for Worker INSERT
    return {
        "tenant_id": sanitized["tenant_id"],
        "tenant_slug": tenant_slug,
        "agent_name": agent_name,
        "agent_kind": agent_name,  # mirror agent_name for custom mints (no canonical kind)
        "qnft_seed_hex": qnft_record["seed_hex"],
        "token_hash": token_hash,
        "scaffold_path": str(scaffold_path),
        "tier": "tenant-custom",
        "signer": "tenant-admin",
        "model": sanitized["model"],
        "role": sanitized["role"],
        "idempotency": {
            "qnft_minted": qnft_minted,
            "token_minted": token_minted,
            "routing_registered": routing_registered,
            "scaffold_created": scaffold_created,
        },
    }
