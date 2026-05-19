"""
S027 D-1b — Tenant provisioning logic for SOS-side companion endpoint.

Surface: POST /api/internal/tenants/provision (mounted on bridge.py)

Brief: agents/loom/briefs/s027-tenant-self-service-substrate.md §2 (D-1b paired LOCK)
Pre-build memo: agents/kasra/notes/s027-d1b-prebuild-memo.md
Loom approval (2026-05-04 21:46Z) — 4 deviations resolved:
  1. Routing entry: SKIPPED at D-1b (per-agent at D-2; rationale: agent_routing.json
     is per-agent-instance, no agents at D-1b)
  2. Bus token shape: agent="{slug}-admin", scope="tenant" (new scope-vocabulary value)
  3. Auth domain: INTERNAL_API_SECRET env-var direct (NOT tokens.json Bearer auth);
     identity domain (tokens.json) and s2s domain (INTERNAL_API_SECRET) stay separated
  4. Scaffold templates: ~/.mumega/templates/customer/ (live-edit forbidden); rendered
     to ~/.mumega/customers/{slug}/ at provision time

LOCK invariants (4):
  - LOCK-D-1b-internal-bearer-fail-closed
  - LOCK-D-1b-mirror-key-idempotent
  - LOCK-D-1b-bus-token-idempotent
  - LOCK-D-1b-scaffold-idempotent

Atomic-write discipline: all JSON file writes use temp-file-and-rename pattern.
Failure of any step → loud error; inkwell-api D1 catches non-2xx and flips D1 row to
provisioning_state='failed' (no orphan rows, partial state preserved as forensic record).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sos.bus.token_store import find_active, hash_token, load_tokens, write_tokens_atomic

# -----------------------------------------------------------------------
# Paths — substrate-side artifacts
# -----------------------------------------------------------------------
SOS_BUS_DIR = Path(__file__).parent
TOKENS_PATH = SOS_BUS_DIR / "tokens.json"
SOS_HOME_DIR = Path.home() / ".sos"
MIRROR_KEYS_PATH = SOS_HOME_DIR / "mirror_keys.json"
CUSTOMERS_DIR = Path.home() / ".mumega" / "customers"
# Templates live INSIDE the SOS repo (not ~/.mumega/templates/) for version-control +
# fresh-deploy reproducibility. Live tenant data lives at CUSTOMERS_DIR (per-tenant,
# runtime, NOT git-tracked per `feedback_runtime_state_in_git.md`).
# Small deviation from Loom Q4 approval (path differs; separation discipline preserved).
TEMPLATES_DIR = SOS_BUS_DIR / "templates" / "customer"
MIRROR_URL_DEFAULT = "http://localhost:8844"

# -----------------------------------------------------------------------
# Validation
# -----------------------------------------------------------------------
SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$")
TENANT_ID_RE = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")
INDUSTRY_RE = re.compile(r"^[a-z0-9_]{1,32}$")
DISPLAY_NAME_MAX = 200


class ProvisionError(Exception):
    """Raised when provisioning fails for a structural reason (validation, IO)."""

    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_provision_body(body: dict) -> dict:
    """LOCK-D-1b-internal-bearer-fail-closed paired guard: validate body BEFORE any disk write.

    Returns sanitized body dict. Raises ProvisionError on bad input.
    """
    if not isinstance(body, dict):
        raise ProvisionError(422, "invalid_body", "body must be a JSON object")

    tenant_id = body.get("tenant_id")
    slug = body.get("slug")
    display_name = body.get("display_name")
    industry = body.get("industry")

    if not tenant_id or not isinstance(tenant_id, str) or not TENANT_ID_RE.match(tenant_id):
        raise ProvisionError(422, "invalid_tenant_id", "tenant_id must match ^[a-zA-Z0-9._-]{1,64}$")

    if not slug or not isinstance(slug, str) or not SLUG_RE.match(slug):
        raise ProvisionError(422, "invalid_slug", "slug must match ^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$")

    if not display_name or not isinstance(display_name, str):
        raise ProvisionError(422, "invalid_display_name", "display_name required")
    display_name = display_name.strip()
    if not display_name or len(display_name) > DISPLAY_NAME_MAX:
        raise ProvisionError(422, "invalid_display_name", f"display_name must be 1-{DISPLAY_NAME_MAX} chars after trim")

    # industry is OPTIONAL (S027 iter-2 close, Athena BLOCKED 2026-05-04T21:57Z):
    # D-1 may omit it or pass null when caller didn't supply one. We default to
    # "general" rather than failing — template rendering needs *some* string but
    # the actual industry vocabulary is enforced upstream by D-1's whitelist.
    # When provided, the type+regex guard still applies (no silent typo fallback).
    if industry is None:
        industry = "general"
    elif not isinstance(industry, str) or not INDUSTRY_RE.match(industry):
        raise ProvisionError(422, "invalid_industry", "industry must match ^[a-z0-9_]{1,32}$ or be null")

    return {
        "tenant_id": tenant_id,
        "slug": slug,
        "display_name": display_name,
        "industry": industry,
    }


# -----------------------------------------------------------------------
# Auth — LOCK-D-1b-internal-bearer-fail-closed
# -----------------------------------------------------------------------
def get_internal_secret() -> Optional[str]:
    """Read INTERNAL_API_SECRET from env. Returns None if unset.

    Caller MUST treat None as fail-closed (503), not 500. Missing env var is a
    substrate misconfiguration, not a request error.
    """
    secret = os.environ.get("INTERNAL_API_SECRET", "")
    return secret if secret else None


def constant_time_equal(a: str, b: str) -> bool:
    """Constant-time string equality. Uses hmac.compare_digest under the hood."""
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def authenticate_bearer(authorization_header: str) -> bool:
    """Returns True iff the Bearer header matches INTERNAL_API_SECRET via constant-time compare.

    Returns False on:
      - missing INTERNAL_API_SECRET env (caller maps to 503)
      - missing/malformed Authorization header (caller maps to 401)
      - non-matching token (caller maps to 401)

    Caller distinguishes 503 vs 401 by calling `get_internal_secret()` first.
    """
    secret = get_internal_secret()
    if not secret:
        return False
    if not authorization_header or not authorization_header.startswith("Bearer "):
        return False
    presented = authorization_header[len("Bearer "):]
    return constant_time_equal(presented, secret)


# -----------------------------------------------------------------------
# Atomic write — temp-file-and-rename
# -----------------------------------------------------------------------
def atomic_write_json(path: Path, data) -> None:
    """Write data as JSON to path atomically. Temp file in same dir, fsync, rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(data, indent=2) + "\n"
    with tmp.open("w", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


# -----------------------------------------------------------------------
# Mirror key — LOCK-D-1b-mirror-key-idempotent
# -----------------------------------------------------------------------
def _load_mirror_keys() -> list[dict]:
    if not MIRROR_KEYS_PATH.exists():
        return []
    try:
        data = json.loads(MIRROR_KEYS_PATH.read_text())
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _find_mirror_key(keys: list[dict], slug: str) -> Optional[dict]:
    """Scan for active mirror key with agent_slug == slug. Returns record or None."""
    for k in keys:
        if k.get("agent_slug") == slug and k.get("active", True):
            return k
    return None


def _mirror_admin_request(method: str, path: str, token: str, body: Optional[dict] = None) -> dict:
    """Call Mirror's root-admin API using stdlib HTTP to avoid adding deps."""
    base = os.environ.get("MIRROR_URL", MIRROR_URL_DEFAULT).rstrip("/")
    payload = None
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{base}{path}",
        data=payload,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def _find_or_create_mirror_workspace(slug: str, display_name: str, admin_token: str) -> dict:
    workspaces = _mirror_admin_request("GET", "/admin/workspaces", admin_token).get("workspaces", [])
    for workspace in workspaces:
        if workspace.get("slug") == slug:
            return workspace
    return _mirror_admin_request(
        "POST",
        "/admin/workspaces",
        admin_token,
        {"slug": slug, "name": display_name},
    )


def _issue_db_mirror_token(slug: str, display_name: str) -> Optional[dict]:
    """Issue a DB-backed Mirror token when root admin config is available.

    Returns None when DB issuance is not configured; callers may use the legacy
    file-backed path as a compatibility bridge.
    """
    admin_token = os.environ.get("MIRROR_ADMIN_TOKEN", "")
    if not admin_token:
        return None

    try:
        workspace = _find_or_create_mirror_workspace(slug, display_name, admin_token)
        workspace_id = workspace["id"]
        issued = _mirror_admin_request(
            "POST",
            f"/admin/workspaces/{workspace_id}/tokens",
            admin_token,
            {
                "label": f"{display_name} mirror access",
                "token_type": "agent",
                "owner_id": slug,
            },
        )
    except urllib.error.HTTPError as exc:
        raise ProvisionError(502, "mirror_admin_http_error", f"Mirror admin API HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # Mirror admin API is not reachable in this environment. Keep S027
        # provisioning usable via the legacy non-admin file-backed key path.
        return None
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ProvisionError(500, "mirror_admin_bad_response", f"Mirror admin API bad response: {exc}") from exc
    token = issued.get("token")
    token_id = issued.get("token_id")
    if not token or not token_id:
        raise ProvisionError(500, "mirror_admin_bad_response", "Mirror token response missing token/token_id")
    return {
        "key": token,
        "mirror_workspace_id": workspace_id,
        "mirror_token_id": token_id,
        "source": "mirror_tokens",
    }


def _cached_db_mirror_token_is_active(record: dict) -> bool:
    """Return whether a cached DB-issued Mirror token still exists upstream.

    The plaintext row in ~/.sos/mirror_keys.json is a delivery/idempotency cache,
    not an auth source. When root-admin config is available, verify DB-issued
    cache rows against Mirror before returning them so revoked tokens can be
    replaced instead of re-delivered.
    """
    if record.get("source") != "mirror_tokens":
        return True

    admin_token = os.environ.get("MIRROR_ADMIN_TOKEN", "")
    if not admin_token:
        return True

    workspace_id = record.get("mirror_workspace_id")
    token_id = record.get("mirror_token_id")
    if not workspace_id or not token_id:
        return False

    try:
        response = _mirror_admin_request(
            "GET",
            f"/admin/workspaces/{workspace_id}/tokens",
            admin_token,
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise ProvisionError(502, "mirror_admin_http_error", f"Mirror admin API HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError):
        # Do not make a temporary Mirror admin outage break idempotent delivery of
        # an already-cached token. Auth remains enforced by Mirror DB state.
        return True
    except json.JSONDecodeError as exc:
        raise ProvisionError(500, "mirror_admin_bad_response", f"Mirror admin API bad response: {exc}") from exc

    tokens = response.get("tokens")
    if not isinstance(tokens, list):
        raise ProvisionError(500, "mirror_admin_bad_response", "Mirror token list missing tokens")
    return any(token.get("id") == token_id and token.get("active", True) for token in tokens)


def mint_or_get_mirror_key(slug: str, display_name: str) -> tuple[str, bool]:
    """Idempotent mirror key mint.

    Returns (key, minted). minted=True if newly created, False if returned existing.
    Raises ProvisionError on filesystem error.
    """
    try:
        keys = _load_mirror_keys()
        existing = _find_mirror_key(keys, slug)
        if existing:
            existing_key = existing.get("key", "")
            if existing_key and _cached_db_mirror_token_is_active(existing):
                return existing_key, False
            # Record exists but key field is empty — corrupt. Mark inactive BEFORE
            # appending fresh, otherwise next call would find it again (still active=True
            # with empty key) and unbounded-re-mint. (S027 iter-2 close P1-A,
            # Athena BLOCKED 2026-05-04T21:57Z.)
            existing["active"] = False
            existing["inactive_reason"] = "empty_key" if not existing_key else "mirror_db_token_inactive"
            existing["inactive_at"] = now_iso()

        issued = _issue_db_mirror_token(slug, display_name)
        new_key = issued["key"] if issued else f"sk-mumega-{slug}-{secrets.token_hex(8)}"
        new_record = {
            "key": new_key,
            "agent_slug": slug,
            "created_at": now_iso(),
            "active": True,
            "label": f"{display_name} mirror access",
            "source": issued["source"] if issued else "legacy_file",
        }
        if issued:
            new_record["mirror_workspace_id"] = issued["mirror_workspace_id"]
            new_record["mirror_token_id"] = issued["mirror_token_id"]
        keys.append(new_record)
        atomic_write_json(MIRROR_KEYS_PATH, keys)
        return new_key, True
    except OSError as e:
        raise ProvisionError(500, "mirror_key_io_error", f"mirror_keys.json IO: {e}") from e


# -----------------------------------------------------------------------
# Bus token — LOCK-D-1b-bus-token-idempotent
# -----------------------------------------------------------------------
def _load_tokens() -> list[dict]:
    try:
        return load_tokens(TOKENS_PATH)
    except (json.JSONDecodeError, OSError):
        return []


def _find_tenant_admin_token(tokens: list[dict], slug: str) -> Optional[dict]:
    """Scan for active tenant-admin token. Returns record or None.

    Match shape: agent == "{slug}-admin" AND scope == "tenant".
    """
    expected_agent = f"{slug}-admin"
    return find_active(
        tokens,
        lambda t: t.get("agent") == expected_agent and t.get("scope") == "tenant",
    )


def mint_or_get_bus_token(slug: str, display_name: str) -> tuple[str, bool]:
    """Idempotent bus-token mint for tenant admin.

    Returns (token, minted). minted=True if newly created, False if returned existing plaintext.
    Raises ProvisionError on filesystem error or if existing record lacks plaintext field.
    """
    try:
        tokens = _load_tokens()
        existing = _find_tenant_admin_token(tokens, slug)
        if existing:
            existing_token = existing.get("token", "")
            if existing_token:
                return existing_token, False
            # Record exists but plaintext field empty — historic record without plaintext
            # cannot be returned. Fail loud rather than mint a duplicate (which would
            # diverge identity).
            raise ProvisionError(
                500,
                "bus_token_plaintext_missing",
                f"existing tenant-admin token for {slug} has no plaintext; "
                "manual remint required (cannot return hash-only record)",
            )

        agent_name = f"{slug}-admin"
        raw_token = f"sk-{agent_name}-{secrets.token_hex(16)}"
        new_record = {
            "token": raw_token,
            "token_hash": hash_token(raw_token),
            "project": slug,
            "label": f"{display_name} tenant admin",
            "active": True,
            "created_at": now_iso(),
            "agent": agent_name,
            "scope": "tenant",
            "role": "owner",
            "scopes": ["tenant:*", "bus:*"],
        }
        tokens.append(new_record)
        write_tokens_atomic(TOKENS_PATH, tokens)
        return raw_token, True
    except OSError as e:
        raise ProvisionError(500, "bus_token_io_error", f"tokens.json IO: {e}") from e


# -----------------------------------------------------------------------
# Scaffold — LOCK-D-1b-scaffold-idempotent
# -----------------------------------------------------------------------
def _render_template(content: str, slug: str, display_name: str, industry: str) -> str:
    return (
        content
        .replace("{{TENANT_SLUG}}", slug)
        .replace("{{DISPLAY_NAME}}", display_name)
        .replace("{{INDUSTRY}}", industry)
    )


def scaffold_or_skip(slug: str, display_name: str, industry: str) -> tuple[Path, bool]:
    """Idempotent tenant home scaffold.

    Creates ~/.mumega/customers/{slug}/ + renders template files if missing.
    Returns (path, created). created=True if mkdir actually happened, False if dir already existed.
    Per-file write is also idempotent: existing files are NOT overwritten.

    Raises ProvisionError on IO failure.
    """
    try:
        if not TEMPLATES_DIR.exists():
            raise ProvisionError(
                500,
                "templates_missing",
                f"templates dir missing: {TEMPLATES_DIR} — substrate misconfigured",
            )

        target = CUSTOMERS_DIR / slug
        created = not target.exists()
        target.mkdir(parents=True, exist_ok=True)

        for tpl_path in sorted(TEMPLATES_DIR.iterdir()):
            if not tpl_path.is_file():
                continue
            dest = target / tpl_path.name
            if dest.exists():
                continue  # idempotent — do not overwrite per-tenant edits
            content = tpl_path.read_text(encoding="utf-8")
            rendered = _render_template(content, slug, display_name, industry)
            dest.write_text(rendered, encoding="utf-8")

        # S028 A5 P2-B — emit .tenant.json so D-2b _resolve_tenant_metadata()
        # reads {display_name, industry} for agent-fork scaffold rendering
        # instead of falling through to (slug, "general"). Idempotent: skip if
        # operator has hand-edited the file.
        tenant_meta_path = target / ".tenant.json"
        if not tenant_meta_path.exists():
            tenant_meta_path.write_text(
                json.dumps(
                    {"slug": slug, "display_name": display_name, "industry": industry},
                    indent=2,
                ),
                encoding="utf-8",
            )

        return target, created
    except OSError as e:
        raise ProvisionError(500, "scaffold_io_error", f"scaffold IO: {e}") from e


# -----------------------------------------------------------------------
# Orchestrator — top-level provisioning
# -----------------------------------------------------------------------
def provision_tenant(body: dict) -> dict:
    """Orchestrate the full tenant provisioning flow.

    Steps (in order):
      1. Validate body
      2. Mirror key — mint or get
      3. Bus token — mint or get
      4. Scaffold — mkdir + render-or-skip

    Returns dict with mirror_key, bus_token, scaffold_path, idempotency flags.
    Raises ProvisionError on any structural failure. Atomic writes mean partial state
    on failure is bounded to the last successful step.
    """
    sanitized = validate_provision_body(body)
    slug = sanitized["slug"]
    display_name = sanitized["display_name"]
    industry = sanitized["industry"]

    mirror_key, mirror_minted = mint_or_get_mirror_key(slug, display_name)
    bus_token, token_minted = mint_or_get_bus_token(slug, display_name)
    scaffold_path, scaffold_created = scaffold_or_skip(slug, display_name, industry)

    return {
        "tenant_id": sanitized["tenant_id"],
        "slug": slug,
        "mirror_key": mirror_key,
        "bus_token": bus_token,
        "scaffold_path": str(scaffold_path),
        "idempotency": {
            "mirror_minted": mirror_minted,
            "token_minted": token_minted,
            "scaffold_created": scaffold_created,
        },
    }
