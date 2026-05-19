"""SOS /sos/agents template body + click-to-expand JS."""
from __future__ import annotations

# Re-export shared CSS/nav so routes can import from one place
from .sos_overview import _SOS_BASE_CSS, _sos_nav  # noqa: F401

__all__ = ["_SOS_BASE_CSS", "_sos_nav"]
