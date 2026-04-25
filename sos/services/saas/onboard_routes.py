"""sos.services.saas.onboard_routes — GitHub OAuth tenant onboarding flow (Sprint 006 E.2 / G68).

Three routes form the prospect-facing onboarding path:

  POST /onboard/discord-intent   (admin-auth)
      Generate a signed intent token that embeds source channel, prospect email hint,
      and plan preference.  The Discord bot embeds the resulting URL in a welcome message.

  GET  /onboard/start?intent=<token>
      Verify the signed intent token (HMAC-SHA256 + TTL).  If valid, create an onboard_nonce
      and redirect the prospect to GitHub OAuth with state=<nonce>.

  GET  /onboard/github/callback?code=<code>&state=<nonce>
      Exchange the GitHub authorization code for an access token.  Fetch GitHub user profile.
      Consume the nonce (atomic — rejects replays).  Upsert a principal.  Create a Stripe Quote.
      Write a contracts row.  Redirect to the Stripe Quote URL so the prospect can review + sign.

Adversarial surface (parallel review required per AGD protocol):
  - Intent tokens are HMAC-signed; /onboard/start verifies before accepting.
  - state= param is the nonce; callback rejects mismatches.
  - consume_nonce() is atomic (single UPDATE WHERE consumed_at IS NULL AND expires_at > now()).
  - upsert_onboard_principal isolates prospects in tenant_id='prospect' — no production tenant impact.
  - GitHub code exchange uses PKCE-equivalent: code is ephemeral and single-use (GitHub enforces).
  - Rate limiting on /onboard/start: returns 429 if nonce count from same IP > 10 in 60s
    (implemented via onboard_nonces COUNT query — no external dependency).
"""
from __future__ import annotations

import logging
import os
import secrets
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

# Module-level imports so tests can patch these names on this module
from sos.contracts.onboarding import (  # noqa: E402
    consume_nonce,
    create_contract,
    create_onboard_nonce,
    create_stripe_quote,
    sign_intent,
    upsert_onboard_principal,
    verify_intent,
)

log = logging.getLogger("sos.services.saas.onboard_routes")

router = APIRouter(prefix="/onboard", tags=["onboard"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
_GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
_GITHUB_USER_URL = "https://api.github.com/user"
_GITHUB_EMAILS_URL = "https://api.github.com/user/emails"

_NONCE_RATE_LIMIT_WINDOW_S = 60
_NONCE_RATE_LIMIT_MAX = 10


# ---------------------------------------------------------------------------
# Admin auth dependency (re-uses the same pattern as SaaS app.py)
# ---------------------------------------------------------------------------

def _require_admin(request: Request) -> None:
    from sos.services.saas.app import require_admin
    from fastapi.security import HTTPAuthorizationCredentials
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        require_admin(creds)
    else:
        raise HTTPException(401, detail="admin authorization required")


# ---------------------------------------------------------------------------
# Route: POST /onboard/discord-intent
# ---------------------------------------------------------------------------


class IntentRequest(BaseModel):
    email_hint: str | None = None    # prospect email hint from Discord (optional)
    plan: str = "starter"
    source_channel: str | None = None


class IntentResponse(BaseModel):
    intent_token: str
    onboard_url: str


@router.post("/discord-intent", response_model=IntentResponse)
def create_discord_intent(
    req: IntentRequest,
    request: Request,
    _: None = Depends(_require_admin),
) -> IntentResponse:
    """Generate a signed intent token for embedding in Discord onboarding messages.

    Only admin-authed callers (Discord bot, Hadi) can generate intent tokens.
    """
    intent = {
        "email_hint": req.email_hint,
        "plan": req.plan,
        "source_channel": req.source_channel,
    }
    token = sign_intent(intent)
    base_url = os.environ.get("ONBOARD_BASE_URL", "https://api.mumega.com")
    onboard_url = f"{base_url}/onboard/start?intent={token}"
    return IntentResponse(intent_token=token, onboard_url=onboard_url)


# ---------------------------------------------------------------------------
# Route: GET /onboard/start
# ---------------------------------------------------------------------------


@router.get("/start")
def onboard_start(intent: str, request: Request) -> RedirectResponse:
    """Verify signed intent; create nonce; redirect to GitHub OAuth.

    state= in the GitHub redirect URL equals the nonce — consumed by the
    callback to prevent CSRF.
    """
    # 1. Verify intent signature and TTL
    try:
        intent_payload = verify_intent(intent)
    except ValueError as exc:
        raise HTTPException(400, detail=f"invalid intent: {exc}") from exc

    # 2. Rate-limit: reject if too many pending nonces recently (by IP)
    client_ip = request.client.host if request.client else "unknown"
    _check_nonce_rate_limit(client_ip)

    # 3. Create nonce (stored server-side)
    nonce = create_onboard_nonce({**intent_payload, "_ip": client_ip})

    # 4. Build GitHub OAuth redirect
    github_client_id = os.environ.get("GITHUB_CLIENT_ID", "")
    if not github_client_id:
        raise HTTPException(503, detail="GitHub OAuth not configured (GITHUB_CLIENT_ID missing)")

    redirect_uri = _callback_url()
    scope = "read:user,user:email"

    github_url = (
        f"{_GITHUB_AUTH_URL}"
        f"?client_id={github_client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope={scope}"
        f"&state={nonce}"
    )
    log.info("onboard_start: redirect to GitHub OAuth for nonce %s... (ip=%s)", nonce[:8], client_ip)
    return RedirectResponse(url=github_url, status_code=302)


def _check_nonce_rate_limit(client_ip: str) -> None:
    """Raise HTTP 429 if this IP has too many pending nonces in the window."""
    from sos.contracts.onboarding import _connect as _onboarding_connect

    try:
        with _onboarding_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT COUNT(*) AS cnt FROM onboard_nonces
                        WHERE intent->>'_ip' = %s
                          AND created_at > now() - interval %s
                          AND consumed_at IS NULL""",
                    (client_ip, f"{_NONCE_RATE_LIMIT_WINDOW_S} seconds"),
                )
                row = cur.fetchone()
                count = row["cnt"] if row else 0
    except Exception as exc:
        log.warning("nonce rate-limit check failed (non-blocking): %s", exc)
        return

    if count >= _NONCE_RATE_LIMIT_MAX:
        raise HTTPException(429, detail="too many pending onboard requests — please wait")


# ---------------------------------------------------------------------------
# Route: GET /onboard/github/callback
# ---------------------------------------------------------------------------


@router.get("/github/callback")
async def github_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Exchange GitHub authorization code; upsert principal; create contract; redirect to quote URL."""
    # 1. Reject explicit OAuth errors (user denied, app suspended, etc.)
    if error:
        log.warning("github_callback: OAuth error=%s", error)
        raise HTTPException(400, detail=f"GitHub OAuth error: {error}")

    if not code or not state:
        raise HTTPException(400, detail="missing code or state parameter")

    # 2. Consume nonce — atomic; raises on expired/consumed/not-found
    try:
        intent = consume_nonce(state)
    except ValueError as exc:
        log.warning("github_callback: nonce consume failed: %s", exc)
        raise HTTPException(400, detail=f"invalid or expired state: {exc}") from exc

    # 3. Exchange code for access token
    try:
        github_token = await _exchange_github_code(code)
    except ValueError as exc:
        log.error("github_callback: code exchange failed: %s", exc)
        raise HTTPException(502, detail="GitHub code exchange failed") from exc

    # 4. Fetch GitHub user profile
    try:
        profile = await _fetch_github_profile(github_token)
    except ValueError as exc:
        log.error("github_callback: profile fetch failed: %s", exc)
        raise HTTPException(502, detail="GitHub profile fetch failed") from exc

    github_login = profile["login"]
    email = profile.get("email") or intent.get("email_hint")
    display_name = profile.get("name") or github_login
    plan = intent.get("plan", "starter")

    # 5. Upsert principal (tenant_id='prospect', idempotent on email)
    try:
        principal_id = upsert_onboard_principal(
            github_login=github_login,
            email=email,
            display_name=display_name,
        )
    except Exception as exc:
        log.error("github_callback: principal upsert failed: %s", exc)
        raise HTTPException(500, detail="could not create prospect profile") from exc

    # 6. Create Stripe Quote
    try:
        quote_id, quote_url = create_stripe_quote(
            principal_id=principal_id,
            email=email,
            display_name=display_name,
            plan=plan,
        )
    except ValueError as exc:
        log.error("github_callback: Stripe Quote creation failed: %s", exc)
        raise HTTPException(502, detail=f"quote creation failed: {exc}") from exc

    # 7. Write contract row
    try:
        contract_id = create_contract(
            principal_id=principal_id,
            stripe_customer_id=None,  # resolved inside create_stripe_quote; stored in Stripe itself
            stripe_quote_id=quote_id,
            stripe_quote_url=quote_url,
            status="sent",
        )
    except Exception as exc:
        log.error("github_callback: contract creation failed: %s", exc)
        raise HTTPException(500, detail="could not record contract artifact") from exc

    log.info(
        "github_callback: onboarding complete principal=%s contract=%s quote=%s",
        principal_id, contract_id, quote_id,
    )

    # 8. Redirect prospect to Stripe Quote hosted URL
    return RedirectResponse(url=quote_url, status_code=302)


# ---------------------------------------------------------------------------
# GitHub OAuth helpers
# ---------------------------------------------------------------------------


async def _exchange_github_code(code: str) -> str:
    """Exchange GitHub OAuth code for access token.  Returns the access token string."""
    client_id = os.environ.get("GITHUB_CLIENT_ID", "")
    client_secret = os.environ.get("GITHUB_CLIENT_SECRET", "")
    redirect_uri = _callback_url()

    if not client_id or not client_secret:
        raise ValueError("GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET not configured")

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            _GITHUB_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
        )

    if resp.status_code != 200:
        raise ValueError(f"GitHub token endpoint returned {resp.status_code}")

    data = resp.json()
    if "error" in data:
        raise ValueError(f"GitHub token error: {data['error']} — {data.get('error_description', '')}")

    access_token = data.get("access_token", "")
    if not access_token:
        raise ValueError("GitHub token response missing access_token")

    return access_token


async def _fetch_github_profile(access_token: str) -> dict[str, Any]:
    """Fetch GitHub user profile (login, name, email)."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        user_resp = await client.get(_GITHUB_USER_URL, headers=headers)

    if user_resp.status_code != 200:
        raise ValueError(f"GitHub user API returned {user_resp.status_code}")

    profile = user_resp.json()

    # GitHub returns email=null for accounts with private email setting.
    # Fall back to fetching the primary verified email from /user/emails.
    if not profile.get("email"):
        async with httpx.AsyncClient(timeout=10.0) as client:
            emails_resp = await client.get(_GITHUB_EMAILS_URL, headers=headers)
        if emails_resp.status_code == 200:
            for entry in emails_resp.json():
                if entry.get("primary") and entry.get("verified"):
                    profile["email"] = entry["email"]
                    break

    return profile


def _callback_url() -> str:
    base = os.environ.get("ONBOARD_BASE_URL", "https://api.mumega.com")
    return f"{base}/onboard/github/callback"
