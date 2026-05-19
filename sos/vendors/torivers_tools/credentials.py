"""
Secure credential proxy for automations.

This module provides the CredentialProxy class that allows automations
to access user credentials securely without exposing raw tokens.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from torivers_sdk.context.clients import ServiceClient


class CredentialError(Exception):
    """Base exception for credential-related errors."""

    pass


class CredentialNotAllowedError(CredentialError):
    """Raised when accessing a credential not declared in manifest."""

    pass


class CredentialNotConfiguredError(CredentialError):
    """Raised when a required credential is not configured by the user."""

    pass


class CredentialProxy:
    """
    Secure proxy for accessing user credentials.

    Developers NEVER see raw tokens. The proxy:
    - Validates credential access against manifest
    - Handles token refresh automatically
    - Provides service-specific clients

    This is a placeholder implementation. The actual implementation
    is injected by the ToRivers runtime with access to the credential
    proxy service.

    Example:
        def send_notification(self, state: MyState) -> dict:
            credentials = state["credentials"]

            if credentials.has_credential("slack"):
                slack = credentials.get_client("slack")
                slack.send_message(channel="#general", text="Hello!")

            return {
                "output_data": {
                    "_version": 1,
                    "_blocks": [
                        {
                            "type": "json_data",
                            "label": "Notification Result",
                            "data": {"notified": True},
                            "display_hint": "key_value",
                        }
                    ],
                }
            }
    """

    def __init__(
        self,
        allowed_services: list[str],
        _internal_context: dict[str, Any] | None = None,
    ) -> None:
        """
        Initialize credential proxy.

        Args:
            allowed_services: Services declared in manifest
            _internal_context: Internal context (not exposed to developers)
        """
        self._allowed = set(allowed_services)
        self._context = _internal_context or {}
        self._clients: dict[str, "ServiceClient"] = {}

    def _get_runtime_context(self) -> dict[str, str] | None:
        """
        Resolve proxy runtime context (token + base URL).

        Runtime injects these via _internal_context. For compatibility with
        sandbox containers, we also allow environment variables.
        """
        token = (
            self._context.get("token")
            or os.environ.get("TORIVERS_CREDENTIAL_TOKEN")
            or os.environ.get("CREDENTIALS_TOKEN")
        )
        proxy_url = (
            self._context.get("proxy_url")
            or os.environ.get("TORIVERS_CREDENTIAL_PROXY_URL")
            or os.environ.get("CREDENTIAL_PROXY_URL")
        )

        if not token or not proxy_url:
            return None

        return {"token": str(token), "proxy_url": str(proxy_url)}

    def get_client(self, service: str) -> "ServiceClient":
        """
        Get a pre-configured client for the service.

        The returned client is already authenticated and ready to use.
        Token refresh is handled automatically.

        Args:
            service: Service name (must be in required/optional credentials)

        Returns:
            Service-specific client (e.g., GmailClient, SlackClient)

        Raises:
            CredentialNotAllowedError: If service not in manifest
            CredentialNotConfiguredError: If user hasn't configured credential
        """
        if service not in self._allowed:
            raise CredentialNotAllowedError(
                f"Service '{service}' not declared in manifest. "
                f"Add it to required_credentials or optional_credentials."
            )

        if service in self._clients:
            return self._clients[service]

        runtime_ctx = self._get_runtime_context()
        if not runtime_ctx:
            raise CredentialNotConfiguredError(
                f"Credential for '{service}' is not configured. "
                "This is expected during local testing. "
                "Use MockCredentialProxy for testing."
            )

        # Build an HTTP-backed client that talks to the AI Engine credential proxy.
        from torivers_sdk.context.clients import get_client_class

        client_cls = get_client_class(service)
        if client_cls is None:
            raise CredentialError(
                f"Service '{service}' is not supported by the SDK client registry."
            )

        client = client_cls(
            _auth_context={
                "token": runtime_ctx["token"],
                "proxy_url": runtime_ctx["proxy_url"],
            }
        )
        self._clients[service] = client
        return client

    def has_credential(self, service: str) -> bool:
        """
        Check if user has configured credential for service.

        Use this to check optional credentials before attempting to use them.

        Args:
            service: Service name

        Returns:
            True if credential exists and is valid
        """
        if service not in self._allowed:
            return False

        # Best-effort: if we already created the client, consider it available.
        # For optional credentials, the definitive check happens on first use
        # (the proxy will return a 404 if the user hasn't configured it).
        return service in self._clients or self._get_runtime_context() is not None

    def list_configured_credentials(self) -> list[str]:
        """
        List all credentials the user has configured.

        Returns:
            List of service names with valid credentials
        """
        return list(self._clients.keys())

    def list_allowed_credentials(self) -> list[str]:
        """
        List all credentials declared in the manifest.

        Returns:
            List of allowed service names (both required and optional)
        """
        return list(self._allowed)

    def is_service_allowed(self, service: str) -> bool:
        """
        Check if a service is declared in the manifest.

        Args:
            service: Service name to check

        Returns:
            True if service is in allowed list
        """
        return service in self._allowed

    def _register_client(self, service: str, client: "ServiceClient") -> None:
        """
        Register a service client (internal use only).

        This is used by the runtime to inject authenticated clients.

        Args:
            service: Service name
            client: Authenticated client instance
        """
        if service not in self._allowed:
            raise CredentialNotAllowedError(
                f"Cannot register client for '{service}': not in allowed list"
            )
        self._clients[service] = client

    def _unregister_client(self, service: str) -> None:
        """
        Unregister a service client (internal use only).

        Args:
            service: Service name to unregister
        """
        self._clients.pop(service, None)

    def _clear_all_clients(self) -> None:
        """Clear all registered clients (internal use only)."""
        self._clients.clear()
