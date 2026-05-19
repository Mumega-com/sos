#!/usr/bin/env python3
"""
OAuth Router for SOS Identity Service

Exposes endpoints for Cloudflare OAuth flow.
"""

import os
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from sos.services.identity.cloudflare_oauth import get_cloudflare_oauth
from sos.observability.logging import get_logger

log = get_logger("oauth_router")

router = APIRouter(prefix="/oauth", tags=["oauth"])


class OAuthStartRequest(BaseModel):
    user_id: str
    company_id: Optional[str] = None
    redirect_after: Optional[str] = None


class OAuthStartResponse(BaseModel):
    auth_url: str
    state: str


class WranglerEnvResponse(BaseModel):
    connected: bool
    account_id: Optional[str] = None
    token_preview: Optional[str] = None


@router.post("/cloudflare/start", response_model=OAuthStartResponse)
async def start_cloudflare_oauth(request: OAuthStartRequest):
    """
    Start Cloudflare OAuth flow.

    Returns the authorization URL to redirect the user to.
    """
    import secrets

    cf_oauth = get_cloudflare_oauth()

    # Generate state token
    state = secrets.token_urlsafe(32)

    # Store state -> user_id mapping (in production, use Redis/DB with TTL)
    # For now, encode user_id in state
    state_with_user = f"{state}:{request.user_id}"

    auth_url = cf_oauth.generate_auth_url(
        state=state_with_user,
        company_id=request.company_id,
    )

    return OAuthStartResponse(auth_url=auth_url, state=state)


@router.get("/cloudflare/callback")
async def cloudflare_oauth_callback(
    code: str = Query(..., description="Authorization code from Cloudflare"),
    state: str = Query(..., description="State parameter with user_id"),
):
    """
    Handle Cloudflare OAuth callback.

    Exchanges authorization code for tokens and stores the connection.
    """
    # Parse state to get user_id
    parts = state.split(":")
    if len(parts) < 2:
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    # state format: random_token:user_id or random_token:user_id:company_id
    user_id = parts[1]
    company_id = parts[2] if len(parts) > 2 else None

    cf_oauth = get_cloudflare_oauth()

    # Exchange code for tokens
    token_response = await cf_oauth.exchange_code(code)
    if not token_response:
        raise HTTPException(status_code=400, detail="Failed to exchange authorization code")

    # Store the connection
    connection = cf_oauth.store_connection(
        user_id=user_id,
        token_response=token_response,
        company_id=company_id,
    )

    log.info(f"Cloudflare connected for user: {user_id}, account: {connection.account_id}")

    # Redirect to success page
    success_url = os.getenv(
        "CLOUDFLARE_CONNECT_SUCCESS_URL",
        "https://mumega.com/dashboard/settings?connected=cloudflare"
    )

    return RedirectResponse(url=success_url)


@router.get("/cloudflare/env/{user_id}", response_model=WranglerEnvResponse)
async def get_cloudflare_env(user_id: str):
    """
    Get Cloudflare environment variables for wrangler CLI.

    Returns masked token preview for security.
    """
    cf_oauth = get_cloudflare_oauth()
    env = cf_oauth.get_wrangler_env(user_id)

    if not env:
        return WranglerEnvResponse(connected=False)

    token = env.get("CLOUDFLARE_API_TOKEN", "")
    return WranglerEnvResponse(
        connected=True,
        account_id=env.get("CLOUDFLARE_ACCOUNT_ID"),
        token_preview=f"{token[:10]}..." if len(token) > 10 else None,
    )


@router.delete("/cloudflare/{user_id}")
async def revoke_cloudflare_connection(user_id: str):
    """
    Revoke Cloudflare connection and delete stored tokens.
    """
    cf_oauth = get_cloudflare_oauth()
    success = await cf_oauth.revoke_token(user_id)

    if not success:
        raise HTTPException(status_code=404, detail="Connection not found")

    return {"status": "revoked", "user_id": user_id}


@router.get("/cloudflare/status/{user_id}")
async def get_cloudflare_status(user_id: str):
    """
    Get Cloudflare connection status for a user.
    """
    cf_oauth = get_cloudflare_oauth()
    connection = cf_oauth.get_connection(user_id)

    if not connection:
        return {
            "connected": False,
            "user_id": user_id,
        }

    return {
        "connected": True,
        "user_id": user_id,
        "company_id": connection.company_id,
        "account_id": connection.account_id,
        "connected_at": connection.connected_at,
        "status": connection.status,
    }
