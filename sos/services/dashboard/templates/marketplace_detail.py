"""Marketplace skill detail page CSS."""
from __future__ import annotations

from .marketplace import _MARKETPLACE_CSS

DETAIL_CSS = _MARKETPLACE_CSS + """
.detail-section{background:#1E293B;border:1px solid #334155;border-radius:12px;padding:24px;margin-bottom:20px}
.detail-section h2{font-size:0.75rem;color:#64748B;text-transform:uppercase;letter-spacing:0.07em;margin-bottom:14px;font-weight:600}
.row-pair{display:flex;justify-content:space-between;align-items:flex-start;padding:6px 0;border-bottom:1px solid #0F172A;font-size:0.88rem;gap:16px}
.row-pair:last-child{border:none}
.row-pair>span:first-child{color:#94A3B8;flex-shrink:0}
"""
