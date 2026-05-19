"""
Tests for SSRF protection module.
"""

import pytest
from unittest.mock import patch, MagicMock

from sos.kernel.ssrf import (
    SSRFBlockedError,
    SSRFPolicy,
    validate_url,
    validate_url_safe,
    validate_external_url,
    is_private_ip,
    BLOCKED_HOSTNAMES,
)
import ipaddress


class TestIsPrivateIP:
    """Tests for private IP detection."""

    def test_loopback_ipv4(self):
        """127.x.x.x should be private."""
        assert is_private_ip(ipaddress.ip_address("127.0.0.1")) is True
        assert is_private_ip(ipaddress.ip_address("127.255.255.255")) is True

    def test_private_10_network(self):
        """10.x.x.x should be private."""
        assert is_private_ip(ipaddress.ip_address("10.0.0.1")) is True
        assert is_private_ip(ipaddress.ip_address("10.255.255.255")) is True

    def test_private_172_network(self):
        """172.16-31.x.x should be private."""
        assert is_private_ip(ipaddress.ip_address("172.16.0.1")) is True
        assert is_private_ip(ipaddress.ip_address("172.31.255.255")) is True
        # 172.32 is public
        assert is_private_ip(ipaddress.ip_address("172.32.0.1")) is False

    def test_private_192_network(self):
        """192.168.x.x should be private."""
        assert is_private_ip(ipaddress.ip_address("192.168.0.1")) is True
        assert is_private_ip(ipaddress.ip_address("192.168.255.255")) is True

    def test_link_local(self):
        """169.254.x.x should be private (link-local)."""
        assert is_private_ip(ipaddress.ip_address("169.254.169.254")) is True

    def test_public_ipv4(self):
        """Public IPs should not be private."""
        assert is_private_ip(ipaddress.ip_address("8.8.8.8")) is False
        assert is_private_ip(ipaddress.ip_address("1.1.1.1")) is False
        assert is_private_ip(ipaddress.ip_address("142.250.80.46")) is False

    def test_loopback_ipv6(self):
        """::1 should be private."""
        assert is_private_ip(ipaddress.ip_address("::1")) is True

    def test_unique_local_ipv6(self):
        """fc00::/7 should be private."""
        assert is_private_ip(ipaddress.ip_address("fc00::1")) is True
        assert is_private_ip(ipaddress.ip_address("fd00::1")) is True

    def test_public_ipv6(self):
        """Public IPv6 should not be private."""
        assert is_private_ip(ipaddress.ip_address("2607:f8b0:4004:800::200e")) is False


class TestValidateUrl:
    """Tests for URL validation."""

    def test_valid_https_url(self):
        """Valid HTTPS URLs should pass."""
        url = "https://api.example.com/data"
        assert validate_url(url) == url

    def test_valid_http_url(self):
        """Valid HTTP URLs should pass."""
        url = "http://api.example.com/data"
        assert validate_url(url) == url

    def test_blocked_scheme_file(self):
        """file:// scheme should be blocked."""
        with pytest.raises(SSRFBlockedError, match="Blocked scheme"):
            validate_url("file:///etc/passwd")

    def test_blocked_scheme_ftp(self):
        """ftp:// scheme should be blocked."""
        with pytest.raises(SSRFBlockedError, match="Blocked scheme"):
            validate_url("ftp://files.example.com/data")

    def test_blocked_localhost(self):
        """localhost should be blocked."""
        with pytest.raises(SSRFBlockedError, match="Blocked hostname"):
            validate_url("http://localhost/admin")

    def test_blocked_127_0_0_1(self):
        """127.0.0.1 should be blocked."""
        with pytest.raises(SSRFBlockedError, match="Blocked hostname"):
            validate_url("http://127.0.0.1/admin")

    def test_blocked_metadata_aws(self):
        """AWS metadata endpoint should be blocked."""
        with pytest.raises(SSRFBlockedError, match="Blocked hostname"):
            validate_url("http://169.254.169.254/latest/meta-data/")

    def test_blocked_metadata_gcp(self):
        """GCP metadata endpoint should be blocked."""
        with pytest.raises(SSRFBlockedError, match="Blocked hostname"):
            validate_url("http://metadata.google.internal/")

    def test_blocked_private_ip(self):
        """Private IP addresses should be blocked."""
        with pytest.raises(SSRFBlockedError, match="Blocked private IP"):
            validate_url("http://10.0.0.1/internal")

    def test_blocked_private_ip_192(self):
        """192.168.x.x should be blocked."""
        with pytest.raises(SSRFBlockedError, match="Blocked private IP"):
            validate_url("http://192.168.1.1/router")

    def test_missing_scheme(self):
        """URLs without scheme should raise ValueError."""
        with pytest.raises(ValueError, match="missing scheme"):
            validate_url("api.example.com/data")

    def test_missing_hostname(self):
        """URLs without hostname should raise ValueError."""
        with pytest.raises(ValueError, match="missing hostname"):
            validate_url("http:///path")


class TestSSRFPolicy:
    """Tests for SSRF policy configuration."""

    def test_allow_private_policy(self):
        """allow_private=True should allow private IPs."""
        policy = SSRFPolicy(allow_private=True)
        url = "http://192.168.1.1/internal"
        assert validate_url(url, policy) == url

    def test_allowed_hosts_bypass(self):
        """Whitelisted hosts should bypass all checks."""
        policy = SSRFPolicy(allowed_hosts={"internal.corp.com"})
        url = "http://internal.corp.com/api"
        assert validate_url(url, policy) == url

    def test_blocked_hosts_addition(self):
        """Additional blocked hosts should be blocked."""
        policy = SSRFPolicy(blocked_hosts={"evil.com"})
        with pytest.raises(SSRFBlockedError, match="Blocked hostname"):
            validate_url("http://evil.com/malware", policy)

    def test_dns_resolution_blocking(self):
        """DNS resolving to private IP should be blocked."""
        policy = SSRFPolicy(resolve_dns=True)

        # Mock DNS resolution to return private IP
        with patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [
                (2, 1, 6, "", ("10.0.0.1", 80))  # Private IP
            ]
            with pytest.raises(SSRFBlockedError, match="resolves to private IP"):
                validate_url("http://malicious.com/", policy)

    def test_dns_resolution_disabled(self):
        """resolve_dns=False should skip DNS check."""
        policy = SSRFPolicy(resolve_dns=False)

        # Even with mocked private IP resolution, should pass
        with patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [
                (2, 1, 6, "", ("10.0.0.1", 80))
            ]
            url = "http://example.com/"
            assert validate_url(url, policy) == url


class TestValidateUrlSafe:
    """Tests for safe URL validation wrapper."""

    def test_valid_url_returns_true(self):
        """Valid URL should return (True, '')."""
        ok, error = validate_url_safe("https://api.example.com/")
        assert ok is True
        assert error == ""

    def test_blocked_url_returns_false(self):
        """Blocked URL should return (False, error_message)."""
        ok, error = validate_url_safe("http://localhost/")
        assert ok is False
        assert "Blocked hostname" in error

    def test_invalid_url_returns_false(self):
        """Invalid URL should return (False, error_message)."""
        ok, error = validate_url_safe("not-a-url")
        assert ok is False
        assert "missing" in error.lower()


class TestValidateExternalUrl:
    """Tests for default external URL validation."""

    def test_allowed_model_providers(self):
        """Known model provider APIs should be allowed."""
        allowed_urls = [
            "https://generativelanguage.googleapis.com/v1/models",
            "https://api.anthropic.com/v1/messages",
            "https://api.openai.com/v1/chat/completions",
            "https://api.x.ai/v1/chat",
            "https://api.groq.com/v1/chat",
            "https://gateway.mumega.com/api/v1",
        ]
        for url in allowed_urls:
            assert validate_external_url(url) == url

    def test_blocks_internal_endpoints(self):
        """Internal endpoints should be blocked even with default policy."""
        with pytest.raises(SSRFBlockedError):
            validate_external_url("http://localhost:8080/")

    def test_blocks_metadata_endpoints(self):
        """Cloud metadata should be blocked."""
        with pytest.raises(SSRFBlockedError):
            validate_external_url("http://169.254.169.254/")


class TestBlockedHostnames:
    """Tests for the blocked hostname list."""

    def test_contains_localhost_variants(self):
        """Should block all localhost variants."""
        assert "localhost" in BLOCKED_HOSTNAMES
        assert "127.0.0.1" in BLOCKED_HOSTNAMES
        assert "0.0.0.0" in BLOCKED_HOSTNAMES
        assert "::1" in BLOCKED_HOSTNAMES

    def test_contains_cloud_metadata(self):
        """Should block cloud metadata endpoints."""
        assert "169.254.169.254" in BLOCKED_HOSTNAMES
        assert "metadata.google.internal" in BLOCKED_HOSTNAMES
