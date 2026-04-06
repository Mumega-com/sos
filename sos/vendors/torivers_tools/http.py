"""
Controlled HTTP client for automations.

This module provides a controlled HTTP client that enforces network
policies and domain restrictions in sandboxed environments.

Security restrictions:
- Only HTTPS allowed (HTTP requests rejected)
- Private/internal IP ranges blocked
- Cloud metadata endpoints blocked
- Request/response size limits enforced
- Configurable timeouts
"""

from __future__ import annotations

import base64
import ipaddress
import json as json_module
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

# =============================================================================
# Constants
# =============================================================================

# Blocked network ranges (CIDR notation)
# These prevent SSRF attacks and access to internal resources
BLOCKED_NETWORKS: list[str] = [
    "10.0.0.0/8",  # Private network (Class A)
    "172.16.0.0/12",  # Private network (Class B)
    "192.168.0.0/16",  # Private network (Class C)
    "169.254.0.0/16",  # Link-local addresses
    "127.0.0.0/8",  # Loopback
    "169.254.169.254/32",  # Cloud metadata (AWS/GCP/Azure)
    "::1/128",  # IPv6 loopback
    "fc00::/7",  # IPv6 unique local addresses
    "fe80::/10",  # IPv6 link-local
]

# Size limits
MAX_REQUEST_SIZE: int = 1 * 1024 * 1024  # 1 MB
MAX_RESPONSE_SIZE: int = 10 * 1024 * 1024  # 10 MB

# Timeout settings
DEFAULT_TIMEOUT: float = 30.0
MIN_TIMEOUT: float = 1.0
MAX_TIMEOUT: float = 300.0  # 5 minutes

# Protocol requirements
ALLOWED_SCHEMES: set[str] = {"https"}

# Common HTTP methods
HTTP_METHODS: set[str] = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}


# =============================================================================
# Helper Functions
# =============================================================================


def is_blocked_ip(ip_str: str) -> bool:
    """
    Check if an IP address is in blocked networks.

    This prevents SSRF attacks by blocking access to:
    - Private network ranges (10.x.x.x, 172.16-31.x.x, 192.168.x.x)
    - Loopback addresses (127.x.x.x)
    - Link-local addresses (169.254.x.x)
    - Cloud metadata endpoints (169.254.169.254)
    - IPv6 equivalents

    Args:
        ip_str: IP address as string

    Returns:
        True if IP is blocked, False otherwise
    """
    try:
        ip_addr = ipaddress.ip_address(ip_str)
        for network_str in BLOCKED_NETWORKS:
            network = ipaddress.ip_network(network_str, strict=False)
            if ip_addr in network:
                return True
        return False
    except ValueError:
        # Invalid IP address format - treat as blocked for safety
        return True


def validate_url(url: str) -> tuple[str, str, int]:
    """
    Validate a URL for security requirements.

    Checks:
    - URL is well-formed
    - Scheme is HTTPS (or allowed scheme)
    - Host is not empty

    Args:
        url: URL to validate

    Returns:
        Tuple of (scheme, host, port)

    Raises:
        HttpInvalidUrlError: If URL is malformed
        HttpSchemeNotAllowedError: If scheme is not HTTPS
    """
    if not url or not isinstance(url, str):
        raise HttpInvalidUrlError("URL must be a non-empty string")

    try:
        parsed = urlparse(url)
    except Exception as e:
        raise HttpInvalidUrlError(f"Failed to parse URL: {e}")

    if not parsed.scheme:
        raise HttpInvalidUrlError("URL must include a scheme (e.g., https://)")

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise HttpSchemeNotAllowedError(
            url=url,
            scheme=parsed.scheme,
            allowed_schemes=list(ALLOWED_SCHEMES),
        )

    if not parsed.netloc:
        raise HttpInvalidUrlError("URL must include a host")

    # Extract port (default to 443 for HTTPS)
    port = parsed.port if parsed.port else 443

    return parsed.scheme.lower(), parsed.hostname or "", port


def validate_timeout(timeout: float) -> float:
    """
    Validate and clamp timeout value.

    Args:
        timeout: Requested timeout in seconds

    Returns:
        Valid timeout value within allowed range
    """
    if timeout < MIN_TIMEOUT:
        return MIN_TIMEOUT
    if timeout > MAX_TIMEOUT:
        return MAX_TIMEOUT
    return timeout


# =============================================================================
# Exceptions
# =============================================================================


class HttpError(Exception):
    """Base error for HTTP operations."""

    def __init__(
        self,
        message: str,
        code: str | None = None,
        url: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.url = url
        self.status_code = status_code


class HttpInvalidUrlError(HttpError):
    """Raised when URL is malformed or invalid."""

    def __init__(self, message: str, url: str | None = None) -> None:
        super().__init__(
            message,
            code="invalid_url",
            url=url,
        )


class HttpSchemeNotAllowedError(HttpError):
    """Raised when URL scheme is not allowed (e.g., http:// instead of https://)."""

    def __init__(
        self,
        url: str,
        scheme: str,
        allowed_schemes: list[str] | None = None,
    ) -> None:
        allowed = allowed_schemes or list(ALLOWED_SCHEMES)
        message = (
            f"Scheme '{scheme}' is not allowed. "
            f"Only {', '.join(allowed)} URLs are permitted."
        )
        super().__init__(
            message,
            code="scheme_not_allowed",
            url=url,
        )
        self.scheme = scheme
        self.allowed_schemes = allowed


class HttpBlockedIPError(HttpError):
    """Raised when request targets a blocked IP address."""

    def __init__(self, url: str, ip: str) -> None:
        message = (
            f"Access to IP address '{ip}' is blocked. "
            "Private networks, loopback, and cloud metadata endpoints are not allowed."
        )
        super().__init__(
            message,
            code="blocked_ip",
            url=url,
        )
        self.ip = ip


class HttpRequestTooLargeError(HttpError):
    """Raised when request body exceeds size limit."""

    def __init__(
        self,
        size_bytes: int,
        max_size_bytes: int = MAX_REQUEST_SIZE,
        url: str | None = None,
    ) -> None:
        size_kb = size_bytes / 1024
        max_kb = max_size_bytes / 1024
        message = (
            f"Request body too large ({size_kb:.2f} KB). "
            f"Maximum allowed size is {max_kb:.2f} KB."
        )
        super().__init__(
            message,
            code="request_too_large",
            url=url,
        )
        self.size_bytes = size_bytes
        self.max_size_bytes = max_size_bytes


class HttpResponseTooLargeError(HttpError):
    """Raised when response body exceeds size limit."""

    def __init__(
        self,
        size_bytes: int,
        max_size_bytes: int = MAX_RESPONSE_SIZE,
        url: str | None = None,
    ) -> None:
        size_mb = size_bytes / (1024 * 1024)
        max_mb = max_size_bytes / (1024 * 1024)
        message = (
            f"Response body too large ({size_mb:.2f} MB). "
            f"Maximum allowed size is {max_mb:.2f} MB."
        )
        super().__init__(
            message,
            code="response_too_large",
            url=url,
        )
        self.size_bytes = size_bytes
        self.max_size_bytes = max_size_bytes


class HttpTimeoutError(HttpError):
    """Raised when request times out."""

    def __init__(
        self,
        url: str,
        timeout: float,
    ) -> None:
        message = f"Request timed out after {timeout} seconds"
        super().__init__(
            message,
            code="timeout",
            url=url,
        )
        self.timeout = timeout


class HttpConnectionError(HttpError):
    """Raised when connection to server fails."""

    def __init__(
        self,
        url: str,
        reason: str | None = None,
    ) -> None:
        message = "Failed to connect to server"
        if reason:
            message = f"{message}: {reason}"
        super().__init__(
            message,
            code="connection_error",
            url=url,
        )
        self.reason = reason


class HttpStatusError(HttpError):
    """Raised for HTTP error status codes (4xx, 5xx)."""

    def __init__(
        self,
        message: str,
        status_code: int,
        url: str | None = None,
    ) -> None:
        super().__init__(
            message,
            code="status_error",
            url=url,
            status_code=status_code,
        )

    @property
    def is_client_error(self) -> bool:
        """Check if status indicates client error (4xx)."""
        return self.status_code is not None and 400 <= self.status_code < 500

    @property
    def is_server_error(self) -> bool:
        """Check if status indicates server error (5xx)."""
        return self.status_code is not None and 500 <= self.status_code < 600


class HttpRateLimitError(HttpError):
    """Raised when rate limit is exceeded."""

    def __init__(
        self,
        url: str,
        retry_after: float | None = None,
    ) -> None:
        message = "Rate limit exceeded"
        if retry_after:
            message = f"{message}. Retry after {retry_after} seconds."
        super().__init__(
            message,
            code="rate_limit",
            url=url,
            status_code=429,
        )
        self.retry_after = retry_after


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class HttpResponse:
    """HTTP response wrapper."""

    status_code: int
    headers: dict[str, str]
    body: bytes
    url: str
    elapsed_ms: float = 0.0
    request_method: str = "GET"

    @property
    def text(self) -> str:
        """Get response body as text."""
        return self.body.decode("utf-8")

    def json(self) -> Any:
        """Parse response body as JSON."""
        import json

        return json.loads(self.body)

    @property
    def ok(self) -> bool:
        """Check if response indicates success (2xx status)."""
        return 200 <= self.status_code < 300

    @property
    def is_redirect(self) -> bool:
        """Check if response is a redirect (3xx status)."""
        return 300 <= self.status_code < 400

    @property
    def is_client_error(self) -> bool:
        """Check if response indicates client error (4xx status)."""
        return 400 <= self.status_code < 500

    @property
    def is_server_error(self) -> bool:
        """Check if response indicates server error (5xx status)."""
        return 500 <= self.status_code < 600

    @property
    def content_type(self) -> str:
        """Get content type from headers."""
        return self.headers.get("content-type", self.headers.get("Content-Type", ""))

    @property
    def content_length(self) -> int:
        """Get content length from headers or body."""
        length = self.headers.get("content-length", self.headers.get("Content-Length"))
        if length:
            try:
                return int(length)
            except ValueError:
                pass
        return len(self.body)

    def raise_for_status(self) -> None:
        """
        Raise HttpStatusError if response indicates an error.

        Raises:
            HttpStatusError: If status code indicates error (4xx or 5xx)
        """
        if self.is_client_error or self.is_server_error:
            raise HttpStatusError(
                f"HTTP {self.status_code}",
                status_code=self.status_code,
                url=self.url,
            )


class HttpClient(ABC):
    """
    Controlled HTTP client for external requests.

    Automations use this client to make HTTP requests. In sandboxed
    environments, requests are routed through a proxy that enforces:
    - Domain allowlist/blocklist
    - Rate limiting
    - Request/response logging
    - Content filtering

    The actual implementation is injected by the runtime.

    Example:
        async def fetch_data(self, state: MyState) -> dict:
            http = HttpClient.get_default()

            response = await http.get(
                "https://api.example.com/data",
                headers={"Accept": "application/json"}
            )

            if response.ok:
                data = response.json()
                return {
                    "output_data": {
                        "_version": 1,
                        "_blocks": [
                            {
                                "type": "json_data",
                                "label": "HTTP Response",
                                "data": data,
                                "display_hint": "raw",
                            }
                        ],
                    }
                }
            else:
                raise HttpError(f"Request failed: {response.status_code}")
    """

    @abstractmethod
    async def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse:
        """
        Make a GET request.

        Args:
            url: Request URL
            headers: Optional headers
            params: Optional query parameters
            timeout: Request timeout in seconds

        Returns:
            HTTP response

        Raises:
            HttpError: If request fails
        """
        pass

    @abstractmethod
    async def post(
        self,
        url: str,
        data: bytes | dict[str, Any] | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse:
        """
        Make a POST request.

        Args:
            url: Request URL
            data: Request body (bytes or form data)
            json: JSON body (will be serialized)
            headers: Optional headers
            timeout: Request timeout in seconds

        Returns:
            HTTP response

        Raises:
            HttpError: If request fails
        """
        pass

    @abstractmethod
    async def put(
        self,
        url: str,
        data: bytes | dict[str, Any] | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse:
        """
        Make a PUT request.

        Args:
            url: Request URL
            data: Request body
            json: JSON body
            headers: Optional headers
            timeout: Request timeout in seconds

        Returns:
            HTTP response

        Raises:
            HttpError: If request fails
        """
        pass

    @abstractmethod
    async def delete(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse:
        """
        Make a DELETE request.

        Args:
            url: Request URL
            headers: Optional headers
            timeout: Request timeout in seconds

        Returns:
            HTTP response

        Raises:
            HttpError: If request fails
        """
        pass

    @abstractmethod
    async def patch(
        self,
        url: str,
        data: bytes | dict[str, Any] | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse:
        """
        Make a PATCH request.

        Args:
            url: Request URL
            data: Request body
            json: JSON body
            headers: Optional headers
            timeout: Request timeout in seconds

        Returns:
            HTTP response

        Raises:
            HttpError: If request fails
        """
        pass

    @staticmethod
    def get_default() -> "HttpClient":
        """
        Get the default HTTP client.

        When ``TORIVERS_HTTP_PROXY_URL`` and ``TORIVERS_HTTP_TOKEN`` environment
        variables are set, returns an :class:`HttpProxyClient` backed by the HTTP
        proxy. For testing, use :class:`torivers_sdk.testing.mocks.MockHttpClient`.

        Returns:
            Default HTTP client instance

        Raises:
            RuntimeError: If called outside runtime environment
        """
        base_url = os.environ.get("TORIVERS_HTTP_PROXY_URL")
        token = os.environ.get("TORIVERS_HTTP_TOKEN")
        if base_url and token:
            return HttpProxyClient(base_url=base_url, token=token)
        raise RuntimeError(
            "HttpClient.get_default() can only be called in the runtime environment. "
            "Set TORIVERS_HTTP_PROXY_URL and TORIVERS_HTTP_TOKEN, or "
            "use torivers_sdk.testing.mocks.MockHttpClient for testing."
        )


# =============================================================================
# HTTP Proxy Client (concrete implementation)
# =============================================================================

# Mapping from proxy error codes to SDK exceptions.
_ERROR_CODE_MAP: dict[str, type[HttpError]] = {
    "blocked_ip": HttpBlockedIPError,
    "scheme_not_allowed": HttpSchemeNotAllowedError,
    "request_too_large": HttpRequestTooLargeError,
    "response_too_large": HttpResponseTooLargeError,
    "timeout": HttpTimeoutError,
    "connection_error": HttpConnectionError,
    "rate_limit_exceeded": HttpRateLimitError,
}
_AUTH_ERROR_CODES = {"token_invalid", "token_expired"}


def _extract_proxy_error(response: httpx.Response) -> tuple[str, str]:
    """
    Extract stable ``(error_code, message)`` from proxy error responses.

    Supports both:
    - Direct payload: ``{"code":"...", "error":"..."}``
    - FastAPI HTTPException payload: ``{"detail":"..."} | {"detail":{"code":"..."}}``
    """
    default_msg = f"HTTP proxy error (status {response.status_code})"
    try:
        body = response.json()
    except Exception:
        text = response.text.strip()
        return "proxy_error", text or default_msg

    if not isinstance(body, dict):
        text = response.text.strip()
        return "proxy_error", text or default_msg

    code = body.get("code")
    message = body.get("error") or body.get("message")
    if isinstance(code, str) and code:
        return code, str(message or code)

    detail = body.get("detail")
    if isinstance(detail, dict):
        detail_code = detail.get("code")
        detail_message = detail.get("error") or detail.get("message")
        if isinstance(detail_code, str) and detail_code:
            return detail_code, str(detail_message or detail_code)
    elif isinstance(detail, str) and detail:
        return detail, detail

    if isinstance(message, str) and message:
        return "proxy_error", message

    text = response.text.strip()
    return "proxy_error", text or default_msg


class HttpProxyClient(HttpClient):
    """
    HTTP client backed by the HTTP proxy REST API.

    Sends authenticated requests to the proxy's ``/api/http/request`` endpoint
    using short-lived JWT tokens for execution-scoped HTTP operations.

    Args:
        base_url: HTTP proxy base URL (e.g. ``http://localhost:8001``).
        token: JWT token for authentication.
    """

    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            # Timeout must exceed MAX_TIMEOUT so the proxy-to-target request
            # can run its full duration without the SDK-to-proxy hop timing out.
            self._client = httpx.AsyncClient(timeout=MAX_TIMEOUT + 30)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "HttpProxyClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        data: bytes | dict[str, Any] | None = None,
        json: Any | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> HttpResponse:
        # Client-side validation: HTTPS-only check
        validate_url(url)
        timeout = validate_timeout(timeout)

        # Serialize body to base64
        body_b64: str | None = None
        if json is not None:
            body_bytes = json_module.dumps(json).encode("utf-8")
            body_b64 = base64.b64encode(body_bytes).decode("ascii")
            if headers is None:
                headers = {}
            headers.setdefault("Content-Type", "application/json")
        elif data is not None:
            if isinstance(data, bytes):
                body_bytes = data
            elif isinstance(data, dict):
                body_bytes = json_module.dumps(data).encode("utf-8")
                if headers is None:
                    headers = {}
                headers.setdefault("Content-Type", "application/json")
            else:
                body_bytes = str(data).encode("utf-8")
            body_b64 = base64.b64encode(body_bytes).decode("ascii")

        # Stringify param values so they match the proxy's dict[str, str] schema.
        str_params = {k: str(v) for k, v in params.items()} if params else {}

        payload = {
            "method": method.upper(),
            "url": url,
            "headers": headers or {},
            "params": str_params,
            "body": body_b64,
            "timeout": timeout,
        }

        client = self._get_client()
        try:
            resp = await client.post(
                f"{self._base_url}/api/http/request",
                json=payload,
                headers={"Authorization": f"Bearer {self._token}"},
            )
        except httpx.TimeoutException:
            raise HttpTimeoutError(url=url, timeout=timeout)
        except httpx.HTTPError as exc:
            raise HttpConnectionError(url=url, reason=str(exc))

        if resp.is_success:
            try:
                resp_data = resp.json()
            except ValueError as exc:
                raise HttpError(
                    "Invalid HTTP proxy response payload",
                    code="invalid_proxy_response",
                    url=url,
                    status_code=resp.status_code,
                ) from exc
            if not isinstance(resp_data, dict):
                raise HttpError(
                    "Invalid HTTP proxy response payload",
                    code="invalid_proxy_response",
                    url=url,
                    status_code=resp.status_code,
                )
            status_code = resp_data.get("status_code")
            if not isinstance(status_code, int):
                raise HttpError(
                    "Missing status_code in HTTP proxy response",
                    code="invalid_proxy_response",
                    url=url,
                    status_code=resp.status_code,
                )
            body_bytes = b""
            if resp_data.get("body"):
                try:
                    body_bytes = base64.b64decode(resp_data["body"], validate=True)
                except Exception as exc:
                    raise HttpError(
                        "Invalid response body encoding from HTTP proxy",
                        code="invalid_proxy_response",
                        url=url,
                        status_code=resp.status_code,
                    ) from exc
            return HttpResponse(
                status_code=status_code,
                headers=resp_data.get("headers", {}),
                body=body_bytes,
                url=resp_data.get("url", url),
                elapsed_ms=resp_data.get("elapsed_ms", 0.0),
                request_method=method.upper(),
            )

        error_code, error_msg = _extract_proxy_error(resp)
        if error_code in _AUTH_ERROR_CODES:
            raise RuntimeError(f"HTTP proxy authentication failed: {error_msg}")

        exc_cls = _ERROR_CODE_MAP.get(error_code)
        if exc_cls is HttpBlockedIPError:
            raise HttpBlockedIPError(url=url, ip=error_msg)
        if error_code == "domain_not_allowed":
            raise HttpError(
                error_msg,
                code=error_code,
                url=url,
                status_code=resp.status_code,
            )
        if exc_cls is HttpSchemeNotAllowedError:
            scheme = urlparse(url).scheme or "unknown"
            raise HttpSchemeNotAllowedError(
                url=url,
                scheme=scheme,
                allowed_schemes=["https"],
            )
        if exc_cls is HttpRequestTooLargeError:
            raise HttpRequestTooLargeError(size_bytes=MAX_REQUEST_SIZE + 1, url=url)
        if exc_cls is HttpResponseTooLargeError:
            raise HttpResponseTooLargeError(size_bytes=MAX_RESPONSE_SIZE + 1, url=url)
        if exc_cls is HttpTimeoutError:
            raise HttpTimeoutError(url=url, timeout=timeout)
        if exc_cls is HttpConnectionError:
            raise HttpConnectionError(url=url, reason=error_msg)
        if exc_cls is HttpRateLimitError:
            raise HttpRateLimitError(url=url)

        raise HttpError(
            error_msg,
            code=error_code or "proxy_error",
            url=url,
            status_code=resp.status_code,
        )

    async def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse:
        return await self._request(
            "GET", url, headers=headers, params=params, timeout=timeout
        )

    async def post(
        self,
        url: str,
        data: bytes | dict[str, Any] | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse:
        return await self._request(
            "POST", url, headers=headers, data=data, json=json, timeout=timeout
        )

    async def put(
        self,
        url: str,
        data: bytes | dict[str, Any] | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse:
        return await self._request(
            "PUT", url, headers=headers, data=data, json=json, timeout=timeout
        )

    async def delete(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse:
        return await self._request("DELETE", url, headers=headers, timeout=timeout)

    async def patch(
        self,
        url: str,
        data: bytes | dict[str, Any] | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse:
        return await self._request(
            "PATCH", url, headers=headers, data=data, json=json, timeout=timeout
        )
