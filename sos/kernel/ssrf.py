"""
SSRF (Server-Side Request Forgery) Protection

Validates URLs and prevents requests to internal/private networks.

Usage:
    from sos.kernel.ssrf import validate_url, SSRFBlockedError

    try:
        validate_url("https://api.example.com/data")  # OK
        validate_url("http://169.254.169.254/")  # Raises SSRFBlockedError
    except SSRFBlockedError as e:
        print(f"Blocked: {e}")
"""

import ipaddress
import socket
from urllib.parse import urlparse
from typing import Set, Optional
from dataclasses import dataclass, field


class SSRFBlockedError(Exception):
    """Raised when a URL is blocked due to SSRF protection."""
    pass


# Hostnames that should always be blocked
BLOCKED_HOSTNAMES: Set[str] = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    # Cloud metadata endpoints
    "metadata.google.internal",
    "metadata.goog",
    "169.254.169.254",  # AWS/GCP/Azure metadata
    "100.100.100.200",  # Alibaba metadata
    "fd00:ec2::254",    # AWS IPv6 metadata
}

# Private IPv4 ranges (RFC 1918 + others)
PRIVATE_IPV4_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),       # "This" network
    ipaddress.ip_network("10.0.0.0/8"),      # Private
    ipaddress.ip_network("127.0.0.0/8"),     # Loopback
    ipaddress.ip_network("169.254.0.0/16"),  # Link-local
    ipaddress.ip_network("172.16.0.0/12"),   # Private
    ipaddress.ip_network("192.168.0.0/16"),  # Private
    ipaddress.ip_network("224.0.0.0/4"),     # Multicast
    ipaddress.ip_network("240.0.0.0/4"),     # Reserved
]

# Private IPv6 ranges
PRIVATE_IPV6_NETWORKS = [
    ipaddress.ip_network("::1/128"),         # Loopback
    ipaddress.ip_network("fc00::/7"),        # Unique local
    ipaddress.ip_network("fe80::/10"),       # Link-local
    ipaddress.ip_network("ff00::/8"),        # Multicast
]


@dataclass
class SSRFPolicy:
    """
    SSRF protection policy configuration.

    Attributes:
        allow_private: Allow requests to private networks (default: False)
        allowed_hosts: Whitelist of allowed hostnames (bypass all checks)
        blocked_hosts: Additional hostnames to block
        resolve_dns: Resolve DNS and check IP (default: True)
    """
    allow_private: bool = False
    allowed_hosts: Set[str] = field(default_factory=set)
    blocked_hosts: Set[str] = field(default_factory=set)
    resolve_dns: bool = True


def is_private_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check if an IP address is in a private/reserved range."""
    if isinstance(ip, ipaddress.IPv4Address):
        return any(ip in network for network in PRIVATE_IPV4_NETWORKS)
    else:
        return any(ip in network for network in PRIVATE_IPV6_NETWORKS)


def validate_url(
    url: str,
    policy: Optional[SSRFPolicy] = None
) -> str:
    """
    Validate a URL for SSRF attacks.

    Args:
        url: The URL to validate
        policy: Optional SSRF policy (uses default if None)

    Returns:
        The validated URL (normalized)

    Raises:
        SSRFBlockedError: If the URL is blocked
        ValueError: If the URL is malformed
    """
    policy = policy or SSRFPolicy()

    # Parse URL
    try:
        parsed = urlparse(url)
    except Exception as e:
        raise ValueError(f"Malformed URL: {url}") from e

    # Must have scheme
    if not parsed.scheme:
        raise ValueError(f"URL missing scheme: {url}")

    # Only allow http/https (check before hostname since file:// has no host)
    if parsed.scheme not in ("http", "https"):
        raise SSRFBlockedError(f"Blocked scheme: {parsed.scheme}")

    # Must have hostname
    if not parsed.hostname:
        raise ValueError(f"URL missing hostname: {url}")

    hostname = parsed.hostname.lower()

    # Check whitelist first (bypass all other checks)
    if hostname in policy.allowed_hosts:
        return url

    # Check blocked hostnames
    all_blocked = BLOCKED_HOSTNAMES | policy.blocked_hosts
    if hostname in all_blocked:
        raise SSRFBlockedError(f"Blocked hostname: {hostname}")

    # Check if hostname is an IP address
    try:
        ip = ipaddress.ip_address(hostname)
        if not policy.allow_private and is_private_ip(ip):
            raise SSRFBlockedError(f"Blocked private IP: {ip}")
        return url
    except ValueError:
        pass  # Not an IP address, continue with DNS resolution

    # Resolve DNS and check the IP
    if policy.resolve_dns:
        try:
            # Get all IPs for the hostname
            infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC)
            for info in infos:
                ip_str = info[4][0]
                ip = ipaddress.ip_address(ip_str)
                if not policy.allow_private and is_private_ip(ip):
                    raise SSRFBlockedError(
                        f"Hostname {hostname} resolves to private IP: {ip}"
                    )
        except socket.gaierror:
            # DNS resolution failed - might be intentional for non-existent hosts
            pass
        except SSRFBlockedError:
            raise
        except Exception:
            # Other errors - allow to proceed (will fail at request time)
            pass

    return url


def validate_url_safe(url: str, policy: Optional[SSRFPolicy] = None) -> tuple[bool, str]:
    """
    Safe version of validate_url that returns (ok, error_message).

    Args:
        url: The URL to validate
        policy: Optional SSRF policy

    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        validate_url(url, policy)
        return True, ""
    except (SSRFBlockedError, ValueError) as e:
        return False, str(e)


# Default policy for external API calls
DEFAULT_POLICY = SSRFPolicy(
    allow_private=False,
    allowed_hosts={
        # Known safe model provider APIs
        "generativelanguage.googleapis.com",
        "api.anthropic.com",
        "api.openai.com",
        "api.x.ai",
        "api.groq.com",
        # Mumega services
        "gateway.mumega.com",
        "api.mumega.com",
    },
    resolve_dns=True,
)


def validate_external_url(url: str) -> str:
    """
    Validate a URL for external API calls using default policy.

    This is the recommended function for validating URLs in:
    - Model provider HTTP clients
    - Webhook delivery
    - Tool execution
    - Any external requests
    """
    return validate_url(url, DEFAULT_POLICY)
