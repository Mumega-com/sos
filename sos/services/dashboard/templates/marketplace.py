"""Public /marketplace template body — CSS and card helpers."""
from __future__ import annotations

from typing import Any

_MARKETPLACE_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0F172A;color:#E2E8F0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;padding:24px;max-width:1400px;margin:0 auto;min-height:100vh}
header{margin-bottom:40px;padding-bottom:20px;border-bottom:1px solid #1E293B}
header h1{font-size:1.8rem;color:#F8FAFC;font-weight:700;margin-bottom:8px}
header .tagline{color:#94A3B8;font-size:1rem;line-height:1.6;max-width:700px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:20px;margin-bottom:48px}
.card{background:#1E293B;border:1px solid #334155;border-radius:12px;padding:24px;display:flex;flex-direction:column;gap:12px}
.card-name{font-size:1.1rem;font-weight:600;color:#F8FAFC}
.card-desc{color:#CBD5E1;font-size:0.88rem;line-height:1.6;flex:1}
.card-author{color:#94A3B8;font-size:0.8rem;font-family:'SF Mono',Monaco,monospace}
.card-price{color:#A5B4FC;font-size:0.85rem;font-family:'SF Mono',Monaco,monospace}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:0.72rem;font-weight:600}
.badge-human{background:#064E3B;color:#34D399}
.badge-auto{background:#451A03;color:#FBBF24}
.badge-unverified{background:#1E293B;color:#64748B;border:1px solid #334155}
.badge-disputed{background:#450A0A;color:#F87171}
.earnings{color:#64748B;font-size:0.8rem;line-height:1.5}
.view-link{display:inline-block;margin-top:4px;color:#6366F1;font-size:0.85rem;text-decoration:none}
.view-link:hover{color:#A5B4FC;text-decoration:underline}
footer{border-top:1px solid #1E293B;padding-top:24px;color:#64748B;font-size:0.85rem;display:flex;gap:16px;flex-wrap:wrap}
footer a{color:#94A3B8;text-decoration:none}
footer a:hover{color:#F8FAFC}
"""


def _fmt_price(micros: int) -> str:
    """Format micros (1e-6 USD) as $N.NN per call."""
    cents = micros / 10000
    return f"${cents:.2f} per call"


def _earnings_line(earnings: Any | None) -> str:
    if earnings is None:
        return "No earnings data yet"
    total = (earnings.total_earned_micros or 0) / 10000
    invocations = earnings.total_invocations or 0
    tenants = len(earnings.invocations_by_tenant or {})
    return f"Earned ${total:.2f} across {invocations} invocations across {tenants} tenant{'s' if tenants != 1 else ''}"


def _verification_badge(verification: Any | None) -> str:
    if verification is None:
        return '<span class="badge badge-unverified">unverified</span>'
    status = verification.status
    if status == "human_verified":
        return '<span class="badge badge-human">human verified</span>'
    if status == "auto_verified":
        return '<span class="badge badge-auto">auto verified</span>'
    if status == "disputed":
        return '<span class="badge badge-disputed">disputed</span>'
    return '<span class="badge badge-unverified">unverified</span>'
