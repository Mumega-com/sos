"""Per-tenant OAuth connections.

Each tenant connects their own:
- GoHighLevel (CRM, leads, SMS)
- Google Analytics (GA4)
- Google Search Console
- Microsoft Clarity
- Facebook Ads (future)
- Google Ads (future)

OAuth tokens stored per-tenant at ~/.sos/integrations/{tenant}/{provider}.json
Encrypted at rest (future). Refreshed automatically.

=============================================================================
HTTP-BOUNDARY FOLLOW-UP — NOT PRODUCTION-READY — task #222 (flagged v0.9.2.2)
=============================================================================
Three methods on :class:`TenantIntegrations` are **stubs** that fabricate
tokens instead of calling the real OAuth2 token endpoints:

    - handle_ghl_callback        → MUST POST https://services.leadconnectorhq.com/oauth/token
    - handle_google_callback     → MUST POST https://oauth2.googleapis.com/token
    - refresh_token              → MUST POST the matching refresh_token grant

The current stubs let the connect-flow UI round-trip during development and
demos, but any downstream caller that actually tries to use the stored
access_token against a live provider will get 401/403. Do not deploy this
module in a customer-facing tenant without first replacing the three stubs
with real :mod:`httpx` calls (see the TODO block inside each method for the
exact endpoint + request body). Live integration testing requires provisioned
client_id/client_secret per provider — that credential provisioning is the
blocker, not the code.

Tracked by task #222 — to be picked up after v0.9.2.1 before the first real
TROP tenant connects GHL or Google in production.
=============================================================================
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlencode

logger = logging.getLogger("sos.integrations.oauth")

STORAGE_ROOT = Path.home() / ".sos" / "integrations"

GoogleService = Literal["analytics", "search_console", "ads"]

GOOGLE_SCOPES: dict[GoogleService, str] = {
    "analytics": "https://www.googleapis.com/auth/analytics.readonly",
    "search_console": "https://www.googleapis.com/auth/webmasters.readonly",
    "ads": "https://www.googleapis.com/auth/adwords",
}

GHL_SCOPES = [
    "contacts.readonly",
    "contacts.write",
    "locations.readonly",
    "opportunities.readonly",
]


class TenantIntegrations:
    """Manage OAuth connections for a single tenant."""

    def __init__(self, tenant_name: str) -> None:
        self.tenant = tenant_name
        self.storage_dir = STORAGE_ROOT / tenant_name
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # GoHighLevel
    # ------------------------------------------------------------------

    async def connect_ghl(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> str:
        """Generate GHL OAuth authorization URL.

        Returns the URL the tenant owner visits to grant access.
        """
        params = urlencode({
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(GHL_SCOPES),
        })
        url = f"https://marketplace.gohighlevel.com/oauth/chooselocation?{params}"
        logger.info("GHL OAuth URL generated for tenant %s", self.tenant)
        return url

    async def handle_ghl_callback(self, code: str) -> dict[str, str]:
        """Exchange authorization code for GHL tokens and store them.

        TODO(#222): Implement real token exchange when GHL client credentials are configured.
        POST https://services.leadconnectorhq.com/oauth/token
        Body: client_id, client_secret, grant_type=authorization_code, code, redirect_uri
        Response: access_token, refresh_token, expires_in, locationId, scope
        """
        # --- STUB-NOT-PRODUCTION (task #222) — fabricated tokens, not real ---
        now = datetime.now(timezone.utc)
        credentials = {
            "provider": "ghl",
            "access_token": f"stub_access_{code[:8]}",
            "refresh_token": f"stub_refresh_{code[:8]}",
            "expires_at": now.isoformat(),
            "location_id": "stub_location",
            "scopes": GHL_SCOPES,
            "connected_at": now.isoformat(),
        }
        self._store_credentials("ghl", credentials)
        logger.warning(
            "GHL OAuth callback returning STUB tokens for tenant %s — task #222",
            self.tenant,
        )
        return credentials

    # ------------------------------------------------------------------
    # Google (Analytics, Search Console, Ads)
    # ------------------------------------------------------------------

    async def connect_google(
        self,
        service: GoogleService,
        client_id: str,
        client_secret: str,
        redirect_uri: str = "https://mcp.mumega.com/oauth/google/callback",
    ) -> str:
        """Generate Google OAuth authorization URL for a specific service.

        Returns the URL the tenant owner visits to grant access.
        """
        scope = GOOGLE_SCOPES.get(service)
        if not scope:
            raise ValueError(f"Unknown Google service: {service}")

        params = urlencode({
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent",
            "state": f"{self.tenant}:{service}",
        })
        url = f"https://accounts.google.com/o/oauth2/v2/auth?{params}"
        logger.info("Google OAuth URL generated for tenant %s service %s", self.tenant, service)
        return url

    async def handle_google_callback(self, code: str, service: GoogleService) -> dict[str, str]:
        """Exchange authorization code for Google tokens and store them.

        TODO(#222): Implement real token exchange when Google client credentials are configured.
        POST https://oauth2.googleapis.com/token
        Body: code, client_id, client_secret, redirect_uri, grant_type=authorization_code
        Response: access_token, refresh_token, expires_in, scope, token_type
        """
        # --- STUB-NOT-PRODUCTION (task #222) — fabricated tokens, not real ---
        now = datetime.now(timezone.utc)
        credentials = {
            "provider": f"google_{service}",
            "access_token": f"stub_access_{code[:8]}",
            "refresh_token": f"stub_refresh_{code[:8]}",
            "expires_at": now.isoformat(),
            "scopes": [GOOGLE_SCOPES[service]],
            "connected_at": now.isoformat(),
        }
        self._store_credentials(f"google_{service}", credentials)
        logger.warning(
            "Google %s OAuth callback returning STUB tokens for tenant %s — task #222",
            service,
            self.tenant,
        )
        return credentials

    # ------------------------------------------------------------------
    # Microsoft Clarity (API key, not OAuth)
    # ------------------------------------------------------------------

    async def connect_clarity(self, api_key: str, project_id: str = "") -> dict[str, str]:
        """Store Clarity API key for this tenant.

        Clarity uses a simple API key, not OAuth.
        """
        now = datetime.now(timezone.utc)
        credentials = {
            "provider": "clarity",
            "api_key": api_key,
            "project_id": project_id,
            "connected_at": now.isoformat(),
        }
        self._store_credentials("clarity", credentials)
        logger.info("Clarity credentials stored for tenant %s", self.tenant)
        return credentials

    # ------------------------------------------------------------------
    # Credential management
    # ------------------------------------------------------------------

    def get_credentials(self, provider: str) -> dict[str, str] | None:
        """Read stored credentials for a provider.

        Checks expiry and triggers refresh if needed (stub).
        Returns credentials dict or None if not connected.
        """
        cred_path = self.storage_dir / f"{provider}.json"
        if not cred_path.exists():
            return None

        try:
            credentials = json.loads(cred_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read credentials for %s/%s: %s", self.tenant, provider, exc)
            return None

        # Check expiry for OAuth providers (not Clarity which uses API key)
        expires_at = credentials.get("expires_at")
        if expires_at:
            try:
                expiry = datetime.fromisoformat(expires_at)
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if expiry < datetime.now(timezone.utc):
                    logger.info("Token expired for %s/%s, refresh needed", self.tenant, provider)
                    # TODO: Auto-refresh when token exchange is implemented
            except ValueError:
                pass

        return credentials

    def list_connections(self) -> list[dict[str, str]]:
        """List all connected providers for this tenant.

        Returns a list of connection summaries.
        """
        connections: list[dict[str, str]] = []
        if not self.storage_dir.exists():
            return connections

        for cred_file in sorted(self.storage_dir.glob("*.json")):
            try:
                data = json.loads(cred_file.read_text())
                connections.append({
                    "provider": data.get("provider", cred_file.stem),
                    "connected_at": data.get("connected_at", "unknown"),
                    "status": "active",
                })
            except (json.JSONDecodeError, OSError):
                connections.append({
                    "provider": cred_file.stem,
                    "connected_at": "unknown",
                    "status": "error",
                })

        return connections

    async def refresh_token(self, provider: str) -> bool:
        """Refresh an expired OAuth token using the stored refresh_token.

        TODO(#222): Implement real token refresh for each provider.
        - GHL: POST https://services.leadconnectorhq.com/oauth/token
               Body: client_id, client_secret, grant_type=refresh_token, refresh_token
        - Google: POST https://oauth2.googleapis.com/token
                  Body: client_id, client_secret, grant_type=refresh_token, refresh_token
        """
        credentials = self.get_credentials(provider)
        if not credentials:
            logger.warning("No credentials to refresh for %s/%s", self.tenant, provider)
            return False

        refresh_token_value = credentials.get("refresh_token")
        if not refresh_token_value:
            logger.warning("No refresh_token for %s/%s", self.tenant, provider)
            return False

        # --- STUB-NOT-PRODUCTION (task #222) — bumps timestamp, no real refresh ---
        credentials["expires_at"] = datetime.now(timezone.utc).isoformat()
        self._store_credentials(provider, credentials)
        logger.warning(
            "Token refresh STUBBED for %s/%s — task #222 (no real HTTP call)",
            self.tenant,
            provider,
        )
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _store_credentials(self, provider: str, credentials: dict[str, str]) -> None:
        """Write credentials to disk."""
        cred_path = self.storage_dir / f"{provider}.json"
        cred_path.write_text(json.dumps(credentials, indent=2, default=str))
        # Restrict file permissions (owner-only read/write)
        cred_path.chmod(0o600)
        logger.info("Credentials saved: %s", cred_path)
