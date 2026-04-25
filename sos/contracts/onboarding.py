"""sos.contracts.onboarding — tenant onboarding business logic (Sprint 006 E.2 / G68).

Covers the prospect-facing path from GitHub OAuth through Stripe Quote to contract artifact:

  upsert_onboard_principal()  — idempotent: GitHub email → principals row (tenant_id='prospect')
  create_onboard_nonce()      — generate a 32-byte random nonce; store in onboard_nonces
  consume_nonce()             — mark nonce consumed; returns intent JSONB; raises on expired/used
  create_contract()           — INSERT into contracts with Stripe quote metadata
  create_stripe_quote()       — call Stripe Quotes API; returns (quote_id, quote_url)

Adversarial surface (G68 — parallel review required):
  - Nonces are RANDOM (secrets.token_hex(32)), not predictable.
  - consume_nonce() rejects expired nonces (expires_at < now()) and consumed nonces (consumed_at IS NOT NULL).
  - GitHub OAuth code is single-use (GitHub enforces); state= parameter is our nonce so CSRF is
    covered: an attacker can only complete the callback if they possess the nonce (which was sent
    to the browser that initiated the flow).
  - principal upsert uses tenant_id='prospect' — never mixes with production tenant principals.
    Promotion to a real tenant principal happens in E.3 on contract sign.
  - contracts.stripe_quote_id UNIQUE enforces idempotency for Stripe webhook replay (same quote
    triggers only one contract row).
  - No row is written to contracts until the Stripe Quote is successfully created — avoids orphan
    records with no real Stripe artifact.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets

# Module-level import so tests can patch sos.contracts.onboarding.upsert_principal
from sos.contracts.principals import upsert_principal  # noqa: E402
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("sos.contracts.onboarding")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NONCE_BYTES = 32           # 256-bit nonce → 64-char hex string
_PROSPECT_TENANT_ID = "prospect"  # staging tenant for pre-contract principals
_INTENT_HMAC_KEY_ENV = "ONBOARD_INTENT_SECRET"  # HMAC key for signed Discord intent URLs


# ---------------------------------------------------------------------------
# DB helpers (re-uses the same psycopg2 _connect() pattern as principals.py)
# ---------------------------------------------------------------------------

def _connect():  # type: ignore[return]
    """Return a psycopg2 connection via DATABASE_URL."""
    import psycopg2
    import psycopg2.extras

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("MIRROR_DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL / MIRROR_DATABASE_URL not set")
    conn = psycopg2.connect(dsn)
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    return conn


# ---------------------------------------------------------------------------
# Nonce management
# ---------------------------------------------------------------------------


def create_onboard_nonce(intent: dict[str, Any]) -> str:
    """Generate a cryptographically random nonce; persist to onboard_nonces.

    Returns the nonce string (64-char hex).  The caller embeds this as the
    OAuth state= parameter.
    """
    nonce = secrets.token_hex(_NONCE_BYTES)
    import json as _json

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO onboard_nonces (nonce, intent)
                   VALUES (%s, %s::jsonb)""",
                (nonce, _json.dumps(intent)),
            )
        conn.commit()
    log.debug("onboard_nonce created: %s... (intent keys: %s)", nonce[:8], list(intent.keys()))
    return nonce


def consume_nonce(nonce: str) -> dict[str, Any]:
    """Mark nonce consumed; return its intent JSONB.

    Raises ValueError on:
      - nonce not found
      - nonce already consumed (consumed_at IS NOT NULL)
      - nonce expired (expires_at < now())

    Uses a single atomic UPDATE WHERE consumed_at IS NULL AND expires_at > now()
    so concurrent callbacks on the same nonce cannot both succeed — the second
    UPDATE matches 0 rows and raises ValueError without requiring a SELECT lock.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE onboard_nonces
                      SET consumed_at = now()
                    WHERE nonce = %s
                      AND consumed_at IS NULL
                      AND expires_at > now()
                   RETURNING intent""",
                (nonce,),
            )
            row = cur.fetchone()
            if row is None:
                # Distinguish not-found vs consumed/expired with a second read
                cur.execute(
                    "SELECT consumed_at, expires_at FROM onboard_nonces WHERE nonce = %s",
                    (nonce,),
                )
                info = cur.fetchone()
                if info is None:
                    raise ValueError("onboard nonce not found")
                if info["consumed_at"] is not None:
                    raise ValueError("onboard nonce already consumed")
                raise ValueError("onboard nonce expired")
        conn.commit()
    return dict(row["intent"])


# ---------------------------------------------------------------------------
# Principal management for prospects
# ---------------------------------------------------------------------------


def upsert_onboard_principal(
    *,
    github_login: str,
    email: str | None,
    display_name: str | None,
) -> str:
    """Upsert a principal for a prospect (tenant_id='prospect').

    Returns principal_id.  Idempotent on (tenant_id, email) when email is
    present; idempotent on github_login via a separate lookup when email is
    absent (public GitHub profile).

    Uses tenant_id='prospect' to segregate pre-contract principals from
    production principals.  Promotion to a real tenant principal happens in
    E.3 on contract sign.
    """
    # Derive a stable synthetic email for GitHub-only accounts without
    # public email (GitHub username is stable per-account).
    effective_email = email or f"{github_login}@github.invalid"

    principal = upsert_principal(
        email=effective_email,
        display_name=display_name or github_login,
        principal_type="human",
        tenant_id=_PROSPECT_TENANT_ID,
    )
    log.info(
        "onboard principal upserted: %s github_login=%s email=%s",
        principal.id, github_login, effective_email,
    )
    return principal.id


# ---------------------------------------------------------------------------
# Stripe Quote
# ---------------------------------------------------------------------------


def create_stripe_quote(
    *,
    principal_id: str,
    email: str | None,
    display_name: str | None,
    plan: str = "starter",
) -> tuple[str, str]:
    """Create a Stripe Quote and return (quote_id, quote_url).

    If STRIPE_SECRET_KEY is not set, returns a synthetic quote artifact
    suitable for local/test environments:
      quote_id = "test-quote-<principal_id[:8]>"
      quote_url = "https://quote.stripe.com/test/..."

    Raises ValueError if Stripe returns an error.
    """
    stripe_secret = os.environ.get("STRIPE_SECRET_KEY", "")
    if not stripe_secret:
        log.warning("STRIPE_SECRET_KEY not set — returning synthetic quote for principal %s", principal_id)
        synthetic_id = f"qt_test_{principal_id[:8]}"
        synthetic_url = f"https://quote.stripe.com/test/{synthetic_id}"
        return synthetic_id, synthetic_url

    try:
        import stripe as stripe_lib
    except ImportError:
        log.error("stripe library not installed — cannot create Stripe Quote")
        raise ValueError("stripe library not installed")

    stripe_lib.api_key = stripe_secret

    # Resolve or create Stripe customer
    stripe_customer_id = _resolve_stripe_customer(
        stripe_lib=stripe_lib,
        email=email,
        display_name=display_name,
        principal_id=principal_id,
    )

    # Resolve price_id for the requested plan from env vars
    price_id = _plan_to_price_id(plan)
    if not price_id:
        raise ValueError(f"No STRIPE_PRICE_ID_{plan.upper()} env var set — cannot create quote")

    try:
        quote = stripe_lib.Quote.create(
            customer=stripe_customer_id,
            line_items=[{"price": price_id, "quantity": 1}],
            metadata={"principal_id": principal_id, "plan": plan},
        )
        # Finalize moves quote to 'open' state and generates the hosted URL
        quote = stripe_lib.Quote.finalize_quote(quote["id"])
        quote_url = quote.get("hosted_quote_url") or quote.get("pdf")
        if not quote_url:
            raise ValueError("Stripe Quote created but hosted_quote_url not available")
        log.info("Stripe Quote created: %s url=%s", quote["id"], quote_url)
        return quote["id"], quote_url
    except stripe_lib.error.StripeError as exc:
        log.error("Stripe Quote creation failed for principal %s: %s", principal_id, exc)
        raise ValueError(f"Stripe Quote error: {exc}") from exc


def _resolve_stripe_customer(
    *,
    stripe_lib: Any,
    email: str | None,
    display_name: str | None,
    principal_id: str,
) -> str:
    """Create or retrieve Stripe Customer for the given email."""
    if email:
        # Search for existing customer by email
        try:
            customers = stripe_lib.Customer.list(email=email, limit=1)
            if customers.data:
                return customers.data[0]["id"]
        except stripe_lib.error.StripeError:
            pass

    customer = stripe_lib.Customer.create(
        email=email,
        name=display_name,
        metadata={"principal_id": principal_id},
    )
    return customer["id"]


def _plan_to_price_id(plan: str) -> str | None:
    """Return the Stripe Price ID for a plan name from environment."""
    env_key = f"STRIPE_PRICE_ID_{plan.upper()}"
    return os.environ.get(env_key)


# ---------------------------------------------------------------------------
# Contract artifact
# ---------------------------------------------------------------------------


def create_contract(
    *,
    principal_id: str,
    stripe_customer_id: str | None,
    stripe_quote_id: str,
    stripe_quote_url: str,
    status: str = "sent",
) -> str:
    """INSERT a contract row; return contract id (UUID).

    Idempotent: if stripe_quote_id already exists (Stripe webhook replay),
    returns the existing contract id without raising.
    """
    if status not in ("draft", "sent", "accepted", "void"):
        raise ValueError(f"invalid contract status: {status!r}")

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO contracts
                       (principal_id, stripe_customer_id, stripe_quote_id, stripe_quote_url, status)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (stripe_quote_id)
                   DO UPDATE SET status = EXCLUDED.status  -- idempotent: allow re-send
                   RETURNING id""",
                (principal_id, stripe_customer_id, stripe_quote_id, stripe_quote_url, status),
            )
            row = cur.fetchone()
        conn.commit()
    contract_id = str(row["id"])
    log.info(
        "contract upserted: %s principal=%s quote=%s status=%s",
        contract_id, principal_id, stripe_quote_id, status,
    )
    return contract_id


# ---------------------------------------------------------------------------
# Discord intent signing / verification
# ---------------------------------------------------------------------------

# Discord intent tokens are HMAC-SHA256 over a canonical payload so the
# /onboard/start route can verify they were issued by a trusted source
# (admin Discord bot, not an arbitrary caller).  They carry a TTL so
# stale links are rejected.
#
# Format: <hex-timestamp>.<base64url-payload>.<hex-hmac>
# Payload: JSON with {email_hint, plan, source_channel, exp (unix timestamp)}


def sign_intent(intent: dict[str, Any], ttl_seconds: int = 86400) -> str:
    """Return a compact HMAC-signed intent token for the /onboard/start URL."""
    import base64
    import json
    import time

    secret = os.environ.get(_INTENT_HMAC_KEY_ENV, "dev-onboard-secret-change-in-prod")
    payload = intent.copy()
    payload["exp"] = int(time.time()) + ttl_seconds

    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode()
    sig = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def verify_intent(token: str) -> dict[str, Any]:
    """Verify and decode a signed intent token.

    Raises ValueError on:
      - malformed token
      - invalid signature
      - expired token
    """
    import base64
    import json
    import time

    secret = os.environ.get(_INTENT_HMAC_KEY_ENV, "dev-onboard-secret-change-in-prod")

    parts = token.split(".")
    if len(parts) != 2:
        raise ValueError("malformed intent token")

    payload_b64, sig = parts
    expected_sig = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        raise ValueError("intent token signature invalid")

    try:
        payload_json = base64.urlsafe_b64decode(payload_b64 + "==").decode()
        payload = json.loads(payload_json)
    except Exception as exc:
        raise ValueError("intent token decode error") from exc

    if payload.get("exp", 0) < int(time.time()):
        raise ValueError("intent token expired")

    return payload
