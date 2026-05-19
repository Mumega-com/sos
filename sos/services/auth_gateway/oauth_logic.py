import httpx
from datetime import datetime, timedelta
from .vault import encrypt_token, decrypt_token

class OAuthHandler:
    def __init__(self, provider: str, client_id: str, client_secret: str, redirect_uri: str = None):
        self.provider = provider
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    async def exchange_code(self, code: str) -> dict:
        """Exchanges authorization code for access tokens."""
        if self.provider == "google":
            return await self._exchange_google(code)
        elif self.provider == "facebook":
            return await self._exchange_facebook(code)
        elif self.provider == "github":
            return await self._exchange_github(code)
        return {"error": "Unsupported provider"}

    async def refresh_token(self, refresh_token: str) -> dict:
        """Refreshes an expired access token."""
        if self.provider == "google":
            return await self._refresh_google(refresh_token)
        elif self.provider == "facebook":
            return await self._refresh_facebook(refresh_token)
        elif self.provider == "github":
            return await self._refresh_github(refresh_token)
        return {}

    # --- Google ---
    async def _exchange_google(self, code: str) -> dict:
        url = "https://oauth2.googleapis.com/token"
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, data=data)
            return resp.json()

    async def _refresh_google(self, refresh_token: str) -> dict:
        url = "https://oauth2.googleapis.com/token"
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": decrypt_token(refresh_token),
            "grant_type": "refresh_token",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, data=data)
            return resp.json()

    # --- Facebook ---
    async def _exchange_facebook(self, code: str) -> dict:
        url = "https://graph.facebook.com/v18.0/oauth/access_token"
        params = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "code": code,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params)
            return resp.json()

    async def _refresh_facebook(self, refresh_token: str) -> dict:
        # Facebook uses long-lived tokens instead of standard refresh tokens
        # Implementation depends on specific requirements
        return {}

    # --- GitHub ---
    async def _exchange_github(self, code: str) -> dict:
        url = "https://github.com/login/oauth/access_token"
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "redirect_uri": self.redirect_uri,
        }
        headers = {"Accept": "application/json"}
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, data=data, headers=headers)
            return resp.json()

    async def _refresh_github(self, refresh_token: str) -> dict:
        # GitHub Apps use refresh tokens if enabled, otherwise they don't expire easily
        url = "https://github.com/login/oauth/access_token"
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": decrypt_token(refresh_token),
            "grant_type": "refresh_token",
        }
        headers = {"Accept": "application/json"}
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, data=data, headers=headers)
            return resp.json()
