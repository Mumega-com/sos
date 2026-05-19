"""
SOS Identity Service

Manages sovereign identity, Guild passes, and OAuth integrations.
"""

from sos.services.identity.core import IdentityCore
from sos.services.identity.qnft import QNFTMinter
from sos.services.identity.cloudflare_oauth import (
    CloudflareOAuth,
    CloudflareConnection,
    CloudflareConnectionStore,
    CloudflareTokenResponse,
    get_cloudflare_oauth,
)
from sos.services.identity.oauth_router import router as oauth_router

__all__ = [
    # Core identity
    "IdentityCore",
    "GuildPass",
    "QNFTMinter",
    # Cloudflare OAuth
    "CloudflareOAuth",
    "CloudflareConnection",
    "CloudflareConnectionStore",
    "CloudflareTokenResponse",
    "get_cloudflare_oauth",
    # Router
    "oauth_router",
]
