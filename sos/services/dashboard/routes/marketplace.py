"""GET /marketplace, GET /marketplace/skill/{id}."""
from __future__ import annotations

import json as _json
import logging
from typing import Any

from fastapi import APIRouter, Response
from fastapi.responses import HTMLResponse, JSONResponse

from ..templates.marketplace import (
    _MARKETPLACE_CSS,
    _earnings_line,
    _fmt_price,
    _verification_badge,
)
from ..templates.marketplace_detail import DETAIL_CSS

router = APIRouter()
logger = logging.getLogger("dashboard")


def _marketplace_cards() -> list[Any]:
    """Return all SkillCards that are marketplace_listed == true."""
    try:
        from sos.skills.registry import Registry
        reg = Registry()
        return [
            c for c in reg.list()
            if c.commerce and c.commerce.marketplace_listed
        ]
    except Exception:
        logger.exception("Failed to load skill registry")
        return []


@router.get("/marketplace", response_class=HTMLResponse)
async def marketplace_index() -> HTMLResponse:
    """Public skill marketplace catalog — no auth required."""
    cards = _marketplace_cards()

    card_html = ""
    for c in cards:
        price_str = _fmt_price(c.commerce.price_per_call_micros) if c.commerce else "—"
        badge = _verification_badge(c.verification)
        earn = _earnings_line(c.earnings)
        card_html += f"""<div class="card">
  <div>
    <div class="card-name">{c.name}</div>
    <div style="margin-top:4px">{badge}</div>
  </div>
  <div class="card-desc">{c.description or ""}</div>
  <div class="card-author">{c.author_agent}</div>
  <div class="card-price">{price_str}</div>
  <div class="earnings">{earn}</div>
  <a href="/marketplace/skill/{c.id}" class="view-link">View card &rarr;</a>
</div>"""

    if not card_html:
        card_html = '<p style="color:#64748B;grid-column:1/-1">No skills listed yet.</p>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mumega Skill Market</title>
<style>{_MARKETPLACE_CSS}</style>
</head>
<body>
<header>
  <h1>&#9670; Mumega Skill Market</h1>
  <p class="tagline">50 skills with receipts beats 18,000 uploads. Every skill here has earnings history, a named author, and verified outputs.</p>
</header>

<div class="grid">
{card_html}
</div>

<footer>
  <a href="/login">Sign In</a>
  <a href="/signup">Get Access</a>
</footer>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/marketplace/skill/{skill_id}", response_class=HTMLResponse)
async def marketplace_skill_detail(skill_id: str, format: str | None = None) -> Response:
    """Public skill detail page — no auth required.

    ?format=json returns the raw SkillCard as JSON.
    """
    try:
        from sos.skills.registry import Registry
        reg = Registry()
        card = reg.get(skill_id)
    except Exception:
        logger.exception("Failed to load registry for skill %s", skill_id)
        card = None

    if card is None:
        return HTMLResponse(
            f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>Skill Not Found</title>
<style>body{{background:#0F172A;color:#E2E8F0;font-family:sans-serif;padding:48px;text-align:center}}</style>
</head><body>
<h1 style="color:#F87171;font-size:2rem">404</h1>
<p style="margin-top:12px;color:#94A3B8">Skill <code style="color:#A5B4FC">{skill_id}</code> not found.</p>
<p style="margin-top:16px"><a href="/marketplace" style="color:#6366F1">Back to marketplace</a></p>
</body></html>""",
            status_code=404,
        )

    # JSON download
    if format == "json":
        return JSONResponse(card.model_dump(exclude_none=True))

    # --- Build detail HTML ---

    def _json_block(obj: Any) -> str:
        escaped = _json.dumps(obj, indent=2, ensure_ascii=False).replace("</", "<\\/")
        return f'<pre style="background:#0F172A;border:1px solid #334155;border-radius:8px;padding:16px;font-size:0.78rem;color:#A5B4FC;overflow-x:auto;white-space:pre-wrap;margin-top:8px">{escaped}</pre>'

    badge = _verification_badge(card.verification)
    tags_html = ""
    if card.tags:
        for t in card.tags:
            tags_html += f'<span style="display:inline-block;background:#1E3A5F;color:#93C5FD;padding:2px 10px;border-radius:999px;font-size:0.72rem;margin:2px">{t}</span>'

    # Earnings section
    earn_section = ""
    if card.earnings:
        e = card.earnings
        total_usd = (e.total_earned_micros or 0) / 10000
        tenants_list = ""
        if e.invocations_by_tenant:
            for tid, cnt in e.invocations_by_tenant.items():
                tenants_list += f'<div style="display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid #0F172A;font-size:0.85rem"><span style="color:#94A3B8">{tid}</span><span>{cnt} calls</span></div>'
        earn_section = f"""<section class="detail-section">
  <h2>Earnings</h2>
  <div class="row-pair"><span>Total earned</span><span style="color:#34D399">${total_usd:.2f} {e.currency}</span></div>
  <div class="row-pair"><span>Invocations</span><span>{e.total_invocations or 0}</span></div>
  <div class="row-pair"><span>Tenants</span><span>{len(e.invocations_by_tenant or {{}})}</span></div>
  {"<div style='margin-top:12px'>" + tenants_list + "</div>" if tenants_list else ""}
</section>"""

    # Lineage section
    lineage_section = ""
    if card.lineage:
        items = ""
        for le in card.lineage:
            items += f'<div style="padding:5px 0;border-bottom:1px solid #0F172A;font-size:0.85rem"><a href="/marketplace/skill/{le.parent_skill_id}" style="color:#6366F1">{le.parent_skill_id}</a> <span style="color:#64748B;margin-left:8px">{le.relation}</span></div>'
        lineage_section = f'<section class="detail-section"><h2>Lineage</h2>{items}</section>'

    # Commerce section
    commerce_section = ""
    if card.commerce:
        co = card.commerce
        price_str = _fmt_price(co.price_per_call_micros)
        split_html = ""
        if co.revenue_split:
            rs = co.revenue_split
            if rs.author is not None:
                split_html += f'<div class="row-pair"><span>Author share</span><span>{int((rs.author or 0)*100)}%</span></div>'
            if rs.operator is not None:
                split_html += f'<div class="row-pair"><span>Operator share</span><span>{int((rs.operator or 0)*100)}%</span></div>'
            if rs.network is not None:
                split_html += f'<div class="row-pair"><span>Network share</span><span>{int((rs.network or 0)*100)}%</span></div>'
        commerce_section = f"""<section class="detail-section">
  <h2>Commerce</h2>
  <div class="row-pair"><span>Price</span><span style="font-family:'SF Mono',Monaco,monospace;color:#A5B4FC">{price_str}</span></div>
  <div class="row-pair"><span>Currency</span><span>{co.currency}</span></div>
  {split_html}
</section>"""

    # Runtime section
    runtime_section = ""
    if card.runtime:
        rt = card.runtime
        runtime_section = f"""<section class="detail-section">
  <h2>Runtime</h2>
  <div class="row-pair"><span>Entry point</span><span style="font-family:'SF Mono',Monaco,monospace;color:#A5B4FC;font-size:0.82rem">{rt.entry_point or "—"}</span></div>
  <div class="row-pair"><span>Backend</span><span>{rt.backend or "—"}</span></div>
  <div class="row-pair"><span>Timeout</span><span>{rt.timeout_seconds or "—"}s</span></div>
  <div class="row-pair"><span>Memory</span><span>{rt.memory_mb or "—"} MB</span></div>
</section>"""

    # Verification section
    verif_section = ""
    if card.verification:
        vf = card.verification
        verified_by_html = ", ".join(
            f'<code style="color:#A5B4FC">{v}</code>' for v in (vf.verified_by or [])
        )
        verif_section = f"""<section class="detail-section">
  <h2>Verification</h2>
  <div class="row-pair"><span>Status</span><span>{badge}</span></div>
  {"<div class='row-pair'><span>Verified by</span><span>" + verified_by_html + "</span></div>" if verified_by_html else ""}
  {"<div class='row-pair'><span>Verified at</span><span>" + (vf.verified_at or "—") + "</span></div>" if vf.verified_at else ""}
</section>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{card.name} — Mumega Skill Market</title>
<style>{DETAIL_CSS}</style>
</head>
<body>
<header>
  <div style="margin-bottom:4px"><a href="/marketplace" style="color:#64748B;font-size:0.85rem;text-decoration:none">&larr; Marketplace</a></div>
  <h1>{card.name}</h1>
  <p class="tagline" style="margin-top:8px">{card.description or ""}</p>
  <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;align-items:center">
    {badge}
    {tags_html}
    <a href="/marketplace/skill/{card.id}?format=json" style="color:#64748B;font-size:0.78rem;border:1px solid #334155;padding:2px 10px;border-radius:6px;text-decoration:none">Download JSON</a>
  </div>
</header>

<section class="detail-section">
  <h2>Identity</h2>
  <div class="row-pair"><span>ID</span><span><code style="color:#A5B4FC;font-size:0.82rem">{card.id}</code></span></div>
  <div class="row-pair"><span>Version</span><span>{card.version}</span></div>
  <div class="row-pair"><span>Author</span><span><code style="color:#A5B4FC">{card.author_agent}</code></span></div>
  <div class="row-pair"><span>Created</span><span style="color:#94A3B8">{card.created_at}</span></div>
  {"<div class='row-pair'><span>Updated</span><span style='color:#94A3B8'>" + (card.updated_at or "—") + "</span></div>" if card.updated_at else ""}
  <div class="row-pair"><span>AI authored</span><span>{"Yes" if card.authored_by_ai else "No"}</span></div>
</section>

{earn_section}
{verif_section}
{lineage_section}
{commerce_section}
{runtime_section}

<section class="detail-section">
  <h2>Input Schema</h2>
  {_json_block(card.input_schema)}
</section>

<section class="detail-section">
  <h2>Output Schema</h2>
  {_json_block(card.output_schema)}
</section>

<footer>
  <a href="/marketplace">&larr; Back to marketplace</a>
  <a href="/login">Sign In</a>
  <a href="/signup">Get Access</a>
</footer>
</body>
</html>"""
    return HTMLResponse(html)
