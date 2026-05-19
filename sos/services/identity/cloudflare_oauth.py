#!/usr/bin/env python3
"""
Cloudflare OAuth2 Integration for SOS

Handles Cloudflare OAuth flow for sovereign deployment onboarding.
Enables one-click Workers/D1/Pages deployment for customers.

Port from CLI to SOS architecture.
"""

import os
import logging
import httpx
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict
from urllib.parse import urlencode
from datetime import datetime
import json
from pathlib import Path

from sos.observability.logging import get_logger

log = get_logger("cloudflare_oauth")

# Cloudflare OAuth2 configuration
CLOUDFLARE_OAUTH_CONFIG = {
    "authorization_url": "https://dash.cloudflare.com/oauth2/auth",
    "token_url": "https://dash.cloudflare.com/oauth2/token",
    "revoke_url": "https://dash.cloudflare.com/oauth2/revoke",
    "client_id": os.getenv("CLOUDFLARE_OAUTH_CLIENT_ID", "54d11594-84e4-41aa-b438-e81b8fa78ee7"),
    "scopes": [
        "account:read",
        "user:read",
        "workers:write",
        "workers_kv:write",
        "workers_scripts:write",
        "workers_routes:write",
        "workers_tail:read",
        "d1:write",
        "pages:write",
        "zone:read",
        "ssl_certs:write",
        "offline_access",
    ],
}


@dataclass
class CloudflareTokenResponse:
    """OAuth token response from Cloudflare"""
    access_token: str
    refresh_token: Optional[str] = None
    expires_in: int = 3600
    token_type: str = "Bearer"
    scope: Optional[str] = None
    account_id: Optional[str] = None
    connected_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CloudflareConnection:
    """Stored Cloudflare connection for a user/company"""
    user_id: str
    company_id: Optional[str]
    access_token: str
    refresh_token: Optional[str]
    account_id: Optional[str]
    expires_at: Optional[str]
    connected_at: str
    status: str = "connected"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CloudflareConnectionStore:
    """
    Simple file-based connection store for SOS.

    Stores connections in ~/.sos/connections/cloudflare/
    In production, replace with Supabase or similar.
    """

    def __init__(self, base_path: Optional[Path] = None):
        self.base_path = base_path or Path.home() / ".sos" / "connections" / "cloudflare"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _user_file(self, user_id: str) -> Path:
        # Sanitize user_id for filesystem
        safe_id = user_id.replace("/", "_").replace("\\", "_")
        return self.base_path / f"{safe_id}.json"

    def save(self, connection: CloudflareConnection) -> None:
        """Save a connection to disk"""
        path = self._user_file(connection.user_id)
        with open(path, "w") as f:
            json.dump(connection.to_dict(), f, indent=2)
        log.info(f"Saved Cloudflare connection for {connection.user_id}")

    def get(self, user_id: str) -> Optional[CloudflareConnection]:
        """Get a connection by user_id"""
        path = self._user_file(user_id)
        if not path.exists():
            return None
        try:
            with open(path) as f:
                data = json.load(f)
            return CloudflareConnection(**data)
        except Exception as e:
            log.error(f"Error loading connection for {user_id}: {e}")
            return None

    def delete(self, user_id: str) -> bool:
        """Delete a connection"""
        path = self._user_file(user_id)
        if path.exists():
            path.unlink()
            log.info(f"Deleted Cloudflare connection for {user_id}")
            return True
        return False

    def list_all(self) -> List[CloudflareConnection]:
        """List all connections"""
        connections = []
        for path in self.base_path.glob("*.json"):
            try:
                with open(path) as f:
                    data = json.load(f)
                connections.append(CloudflareConnection(**data))
            except Exception:
                pass
        return connections


class CloudflareOAuth:
    """
    Cloudflare OAuth2 handler for sovereign deployment onboarding.

    Flow:
    1. generate_auth_url() -> redirect user to Cloudflare
    2. User authorizes in browser
    3. Cloudflare redirects to callback with code
    4. exchange_code() -> get tokens
    5. store_connection() -> save for wrangler use
    """

    def __init__(self, redirect_uri: Optional[str] = None):
        self.config = CLOUDFLARE_OAUTH_CONFIG
        self.redirect_uri = redirect_uri or os.getenv(
            "CLOUDFLARE_REDIRECT_URI",
            "https://mumega.com/connect/cloudflare/callback"
        )
        self.store = CloudflareConnectionStore()

    def generate_auth_url(self, state: str, company_id: Optional[str] = None) -> str:
        """
        Generate Cloudflare OAuth authorization URL.

        Args:
            state: Opaque state value (typically connect intent code)
            company_id: Optional company identifier to embed in state

        Returns:
            URL to redirect user to for authorization
        """
        # Encode company_id in state if provided
        if company_id:
            state = f"{state}:{company_id}"

        params = {
            "response_type": "code",
            "client_id": self.config["client_id"],
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(self.config["scopes"]),
            "state": state,
        }

        auth_url = f"{self.config['authorization_url']}?{urlencode(params)}"
        log.info(f"Generated Cloudflare auth URL for state: {state[:8]}...")
        return auth_url

    async def exchange_code(self, code: str) -> Optional[CloudflareTokenResponse]:
        """
        Exchange authorization code for access/refresh tokens.

        Args:
            code: Authorization code from Cloudflare callback

        Returns:
            Token response or None on failure
        """
        try:
            client_secret = os.getenv("CLOUDFLARE_OAUTH_CLIENT_SECRET")
            if not client_secret:
                log.error("CLOUDFLARE_OAUTH_CLIENT_SECRET not configured")
                return None

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.config["token_url"],
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "client_id": self.config["client_id"],
                        "client_secret": client_secret,
                        "redirect_uri": self.redirect_uri,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )

                if response.status_code != 200:
                    log.error(f"Token exchange failed: {response.status_code} - {response.text}")
                    return None

                data = response.json()

                # Get account ID from user info
                account_id = await self._get_account_id(data.get("access_token"))

                return CloudflareTokenResponse(
                    access_token=data["access_token"],
                    refresh_token=data.get("refresh_token"),
                    expires_in=data.get("expires_in", 3600),
                    token_type=data.get("token_type", "Bearer"),
                    scope=data.get("scope"),
                    account_id=account_id,
                    connected_at=datetime.utcnow().isoformat(),
                )

        except Exception as e:
            log.error(f"Token exchange error: {e}")
            return None

    async def _get_account_id(self, access_token: str) -> Optional[str]:
        """Get primary account ID from Cloudflare API"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.cloudflare.com/client/v4/accounts",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                )

                if response.status_code == 200:
                    data = response.json()
                    if data.get("success") and data.get("result"):
                        # Return first (primary) account ID
                        return data["result"][0]["id"]

        except Exception as e:
            log.warning(f"Could not get account ID: {e}")

        return None

    async def refresh_token(self, refresh_token: str) -> Optional[CloudflareTokenResponse]:
        """
        Refresh an expired access token.

        Args:
            refresh_token: Refresh token from previous auth

        Returns:
            New token response or None on failure
        """
        try:
            client_secret = os.getenv("CLOUDFLARE_OAUTH_CLIENT_SECRET")
            if not client_secret:
                return None

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.config["token_url"],
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": self.config["client_id"],
                        "client_secret": client_secret,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )

                if response.status_code != 200:
                    log.error(f"Token refresh failed: {response.status_code}")
                    return None

                data = response.json()
                account_id = await self._get_account_id(data.get("access_token"))

                return CloudflareTokenResponse(
                    access_token=data["access_token"],
                    refresh_token=data.get("refresh_token", refresh_token),
                    expires_in=data.get("expires_in", 3600),
                    token_type=data.get("token_type", "Bearer"),
                    scope=data.get("scope"),
                    account_id=account_id,
                    connected_at=datetime.utcnow().isoformat(),
                )

        except Exception as e:
            log.error(f"Token refresh error: {e}")
            return None

    def store_connection(
        self,
        user_id: str,
        token_response: CloudflareTokenResponse,
        company_id: Optional[str] = None,
    ) -> CloudflareConnection:
        """
        Store Cloudflare credentials for a user/company.

        Args:
            user_id: User or tenant ID
            token_response: OAuth tokens from exchange
            company_id: Optional company identifier

        Returns:
            Stored connection
        """
        from datetime import timedelta

        expires_at = None
        if token_response.expires_in:
            expires_at = (
                datetime.utcnow() + timedelta(seconds=token_response.expires_in)
            ).isoformat()

        connection = CloudflareConnection(
            user_id=user_id,
            company_id=company_id,
            access_token=token_response.access_token,
            refresh_token=token_response.refresh_token,
            account_id=token_response.account_id,
            expires_at=expires_at,
            connected_at=token_response.connected_at or datetime.utcnow().isoformat(),
            status="connected",
        )

        self.store.save(connection)
        log.info(f"Stored Cloudflare connection for user: {user_id}")

        return connection

    def get_connection(self, user_id: str) -> Optional[CloudflareConnection]:
        """Get stored connection for a user"""
        return self.store.get(user_id)

    def get_wrangler_env(self, user_id: str) -> Optional[Dict[str, str]]:
        """
        Get environment variables for wrangler CLI from stored connection.

        Args:
            user_id: User or tenant ID

        Returns:
            Dict with CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID or None
        """
        connection = self.store.get(user_id)
        if not connection or connection.status != "connected":
            return None

        return {
            "CLOUDFLARE_API_TOKEN": connection.access_token,
            "CLOUDFLARE_ACCOUNT_ID": connection.account_id or "",
        }

    async def revoke_token(self, user_id: str) -> bool:
        """
        Revoke tokens and delete connection.

        Args:
            user_id: User or tenant ID

        Returns:
            True if revoked successfully
        """
        connection = self.store.get(user_id)
        if not connection:
            return False

        try:
            client_secret = os.getenv("CLOUDFLARE_OAUTH_CLIENT_SECRET")
            if client_secret:
                async with httpx.AsyncClient() as client:
                    await client.post(
                        self.config["revoke_url"],
                        data={
                            "token": connection.access_token,
                            "client_id": self.config["client_id"],
                            "client_secret": client_secret,
                        },
                    )
        except Exception as e:
            log.warning(f"Token revoke failed (continuing with delete): {e}")

        self.store.delete(user_id)
        return True


# Singleton for easy access
_cloudflare_oauth: Optional[CloudflareOAuth] = None


def get_cloudflare_oauth() -> CloudflareOAuth:
    """Get or create CloudflareOAuth singleton"""
    global _cloudflare_oauth
    if _cloudflare_oauth is None:
        _cloudflare_oauth = CloudflareOAuth()
    return _cloudflare_oauth
