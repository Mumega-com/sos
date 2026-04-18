"""GET /sos, /sos/overview, /sos/agents, /sos/money, /sos/skills,
/sos/service_map.svg, /sos/api/health."""
from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..auth import _is_admin, _tenant_from_cookie
from ..config import COOKIE_NAME
from ..redis_helper import _get_redis
from ..templates.sos_operator import _SOS_FLOW_MAP_HTML, _load_service_map_svg
from ..templates.sos_overview import _SOS_BASE_CSS, _sos_nav

router = APIRouter()
logger = logging.getLogger("dashboard")

# ---------------------------------------------------------------------------
# Phase 1 helpers (system info)
# ---------------------------------------------------------------------------

_SOS_KNOWN_UNITS = [
    "sos-mcp-sse",
    "bus-bridge",
    "dashboard",
    "sos-saas",
    "sos-squad",
    "calcifer",
    "agent-wake-daemon",
    "agent-lifecycle",
]


def _systemctl_status(unit: str) -> str:
    """Return 'active' or 'inactive' for a systemd user unit."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return result.stdout.strip() or "unknown"
    except subprocess.TimeoutExpired:
        return "timeout"
    except Exception:
        return "error"


def _proc_loadavg() -> str:
    try:
        text = Path("/proc/loadavg").read_text()
        parts = text.split()
        return " ".join(parts[:3])
    except Exception:
        return "—"


def _disk_usage() -> str:
    try:
        result = subprocess.run(
            ["df", "-h", "/"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        lines = result.stdout.strip().splitlines()
        if len(lines) >= 2:
            cols = lines[1].split()
            # cols: Filesystem Size Used Avail Use% Mounted
            if len(cols) >= 5:
                return cols[4]  # Use%
    except Exception:
        pass
    return "—"


def _relative_time(ts_str: str) -> str:
    """Convert an ISO timestamp string to a human-readable relative time."""
    if not ts_str:
        return "—"
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        secs = int(delta.total_seconds())
        if secs < 0:
            return "just now"
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return ts_str


def _fmt_micros(micros: int) -> str:
    """Format integer micros (1e-6 USD) as $X,XXX.XX string."""
    dollars = micros / 1_000_000
    return f"${dollars:,.2f}"


def _event_ts(ts_str: str) -> float:
    """Parse ISO timestamp to unix float; returns 0 on error."""
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/sos", response_class=HTMLResponse)
async def sos_dashboard_root(request: Request) -> Response:
    """SOS operator dashboard — admin-scoped only."""
    cookie_val = request.cookies.get(COOKIE_NAME)
    tenant = _tenant_from_cookie(cookie_val)
    if not tenant:
        return RedirectResponse(url="/login?next=/sos", status_code=302)
    if not _is_admin(tenant):
        return HTMLResponse(
            "<h1>403 — admin scope required</h1>"
            "<p>The SOS operator dashboard requires a system-scoped token "
            "(any token without a <code>project</code> field).</p>"
            "<p><a href=\"/dashboard\">Return to customer dashboard</a></p>",
            status_code=403,
        )
    html = _SOS_FLOW_MAP_HTML.replace(
        "{timestamp}",
        datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC"),
    ).replace("{service_map_svg}", _load_service_map_svg())
    return HTMLResponse(html)


@router.get("/sos/service_map.svg")
async def sos_service_map_svg() -> Response:
    """Serve the raw service-map SVG for sharing / downloads (no auth)."""
    svg = _load_service_map_svg()
    if not svg:
        return Response(status_code=404, content="service map unavailable")
    return Response(content=svg, media_type="image/svg+xml")


@router.get("/sos/api/health")
async def sos_api_health() -> JSONResponse:
    """Public health endpoint for the operator dashboard."""
    return JSONResponse({
        "status": "ok",
        "service": "sos-engine-dashboard",
        "phase": 0,
        "version": __import__("sos").__version__ if hasattr(__import__("sos"), "__version__") else "unknown",
    })


@router.get("/sos/overview", response_class=HTMLResponse)
async def sos_overview(request: Request) -> Response:
    """SOS operator overview — live heartbeat tiles. Admin-scoped only."""
    cookie_val = request.cookies.get(COOKIE_NAME)
    tenant = _tenant_from_cookie(cookie_val)
    if not tenant:
        return RedirectResponse(url="/login?next=/sos/overview", status_code=302)
    if not _is_admin(tenant):
        return HTMLResponse("<h1>403 — admin scope required</h1>", status_code=403)

    now_str = datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC")

    # --- Bus health ---
    bus_stream_count = 0
    bus_total_msgs = 0
    bus_top_streams: list[tuple[str, int, str]] = []  # (key, count, last_ts)
    try:
        r = _get_redis()
        stream_keys = r.keys("sos:stream:*")
        bus_stream_count = len(stream_keys)
        stream_data: list[tuple[str, int, str]] = []
        for key in stream_keys:
            try:
                length = r.xlen(key)
                bus_total_msgs += length
                last_ts = "—"
                last_entries = r.xrevrange(key, count=1)
                if last_entries:
                    raw_id = last_entries[0][0]
                    ts_ms = int(raw_id.split("-")[0])
                    last_ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%H:%M:%S")
                stream_data.append((key, length, last_ts))
            except Exception:
                stream_data.append((key, 0, "—"))
        stream_data.sort(key=lambda x: x[1], reverse=True)
        bus_top_streams = stream_data[:5]
    except Exception:
        pass

    # --- Registry ---
    reg_total = 0
    reg_active = 0
    try:
        r = _get_redis()
        reg_keys = r.keys("sos:registry:*")
        reg_total = len(reg_keys)
        cutoff = datetime.now(timezone.utc).timestamp() - 300
        for key in reg_keys:
            data = r.hgetall(key)
            ls = data.get("last_seen", "")
            if ls:
                try:
                    dt = datetime.fromisoformat(ls)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt.timestamp() >= cutoff:
                        reg_active += 1
                except Exception:
                    pass
    except Exception:
        pass

    # --- Redis INFO ---
    redis_mem = "—"
    redis_clients = "—"
    redis_keys = "—"
    try:
        r = _get_redis()
        info_mem = r.info("memory")
        redis_mem = info_mem.get("used_memory_human", "—")
        info_clients = r.info("clients")
        redis_clients = str(info_clients.get("connected_clients", "—"))
        info_ks = r.info("keyspace")
        db0 = info_ks.get("db0", {})
        redis_keys = str(db0.get("keys", "—")) if isinstance(db0, dict) else "—"
    except Exception:
        pass

    # --- Services ---
    svc_statuses: list[tuple[str, str]] = []
    for unit in _SOS_KNOWN_UNITS:
        status = _systemctl_status(unit)
        svc_statuses.append((unit, status))

    # --- Disk ---
    disk_used = _disk_usage()

    # --- Load ---
    load_avg = _proc_loadavg()

    # --- Build HTML ---
    # Bus card rows
    bus_stream_rows = ""
    for key, count, last_ts in bus_top_streams:
        short = key.replace("sos:stream:", "")
        bus_stream_rows += f'<div class="row"><span style="color:#A5B4FC;font-size:0.78rem;max-width:180px;overflow:hidden;text-overflow:ellipsis">{short}</span><span>{count} msgs &middot; {last_ts}</span></div>'
    if not bus_stream_rows:
        bus_stream_rows = '<div class="muted">No streams found</div>'

    # Services card rows
    svc_rows = ""
    for unit, status in svc_statuses:
        if status == "active":
            dot = '<span class="dot-green">&#9679;</span>'
            badge = '<span class="badge badge-green">active</span>'
        elif status in ("inactive", "failed"):
            dot = '<span class="dot-red">&#9679;</span>'
            badge = f'<span class="badge badge-red">{status}</span>'
        else:
            dot = '<span class="dot-amber">&#9679;</span>'
            badge = f'<span class="badge badge-amber">{status}</span>'
        svc_rows += f'<div class="row">{dot} {unit}{badge}</div>'

    nav_html = _sos_nav("Overview")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SOS Overview — Operator Dashboard</title>
<style>{_SOS_BASE_CSS}</style>
</head>
<body>
<header>
  <div>
    <h1>&#9670; SOS Engine &mdash; Overview</h1>
    <div class="meta">Live system heartbeat &middot; {now_str}</div>
  </div>
  <a href="/logout" class="logout">Sign Out</a>
</header>

{nav_html}

<div class="grid">

  <div class="card">
    <h3>Bus Health</h3>
    <div class="val">{bus_stream_count}</div>
    <div class="muted">streams &middot; {bus_total_msgs} total messages</div>
    <div style="margin-top:16px">{bus_stream_rows}</div>
  </div>

  <div class="card">
    <h3>Registry</h3>
    <div class="val">{reg_active}</div>
    <div class="muted">active (last 5 min) of {reg_total} registered</div>
  </div>

  <div class="card">
    <h3>Redis</h3>
    <div class="row"><span class="muted">Memory</span><span class="val-sm">{redis_mem}</span></div>
    <div class="row"><span class="muted">Clients</span><span class="val-sm">{redis_clients}</span></div>
    <div class="row"><span class="muted">Keys (db0)</span><span class="val-sm">{redis_keys}</span></div>
  </div>

  <div class="card">
    <h3>Services</h3>
    {svc_rows}
  </div>

  <div class="card">
    <h3>Disk</h3>
    <div class="val">{disk_used}</div>
    <div class="muted">used on /</div>
  </div>

  <div class="card">
    <h3>Load Average</h3>
    <div class="val-sm" style="font-size:1.6rem;margin-top:8px">{load_avg}</div>
    <div class="muted" style="margin-top:8px">1m &middot; 5m &middot; 15m</div>
  </div>

</div>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/sos/agents", response_class=HTMLResponse)
async def sos_agents(request: Request) -> Response:
    """SOS operator agents page — full registry table. Admin-scoped only."""
    cookie_val = request.cookies.get(COOKIE_NAME)
    tenant = _tenant_from_cookie(cookie_val)
    if not tenant:
        return RedirectResponse(url="/login?next=/sos/agents", status_code=302)
    if not _is_admin(tenant):
        return HTMLResponse("<h1>403 — admin scope required</h1>", status_code=403)

    now_str = datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC")
    cutoff = datetime.now(timezone.utc).timestamp() - 300

    from sos.clients.registry import RegistryClient
    import os

    agents: list[dict[str, Any]] = []
    try:
        client = RegistryClient(base_url=os.environ.get("SOS_REGISTRY_URL", "http://localhost:6067"))
        idents = client.list_agents()
        for ident in idents:
            ls = ident.metadata.get("last_seen", "")
            rel_time = _relative_time(ls)

            # Determine status: prefer explicit, then infer from last_seen
            explicit_status = ident.metadata.get("status", "")
            if explicit_status == "online":
                status = "online"
            elif explicit_status in ("offline", "stopped"):
                status = "offline"
            elif ls:
                try:
                    dt = datetime.fromisoformat(ls)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    status = "online" if dt.timestamp() >= cutoff else "stale"
                except Exception:
                    status = "unknown"
            else:
                status = "unknown"

            # Public key fingerprint (first 8 chars of sha256)
            pk_fingerprint = "—"
            if ident.public_key:
                import hashlib
                pk_fingerprint = hashlib.sha256(ident.public_key.encode()).hexdigest()[:8]

            # DNA.physics.C (coherence)
            coherence = "—"
            if ident.dna and ident.dna.physics:
                c_val = ident.dna.physics.C
                if c_val is not None:
                    coherence = f"{c_val:.2f}"

            agents.append({
                "name": ident.name,
                "role": ident.metadata.get("role") or ident.metadata.get("agent_type") or "—",
                "status": status,
                "last_seen_rel": rel_time,
                "project": ident.metadata.get("project") or "admin",
                "squads": ident.squad_id or "",
                "raw": json.dumps(ident.to_dict(), indent=2, default=str),
                # New typed fields from AgentIdentity
                "pk_fingerprint": pk_fingerprint,
                "verification": ident.verification_status.value,
                "coherence": coherence,
                "agent_type": ident.metadata.get("agent_type") or "—",
                "edition": ident.edition or "—",
            })
    except Exception:
        logger.debug("Failed to load agent registry", exc_info=True)

    # Sort: online first, then stale, then offline/unknown
    _order = {"online": 0, "stale": 1, "offline": 2, "unknown": 3}
    agents.sort(key=lambda a: (_order.get(a["status"], 9), a["name"]))

    def _status_badge(s: str) -> str:
        if s == "online":
            return '<span class="badge badge-green">online</span>'
        if s == "stale":
            return '<span class="badge badge-amber">stale</span>'
        if s == "offline":
            return '<span class="badge badge-red">offline</span>'
        return f'<span class="badge" style="background:#1E293B;color:#94A3B8">{s}</span>'

    def _verify_badge(v: str) -> str:
        colors = {
            "verified": "#34D399",
            "pending": "#FBBF24",
            "unverified": "#64748B",
            "revoked": "#F87171",
        }
        color = colors.get(v, "#64748B")
        return f'<span style="font-size:0.72rem;color:{color}">{v}</span>'

    rows = ""
    for i, ag in enumerate(agents):
        badge = _status_badge(ag["status"])
        verify_badge = _verify_badge(ag["verification"])
        squads_cell = ag["squads"] if ag["squads"] else '<span class="muted">—</span>'
        raw_escaped = ag["raw"].replace("</", "<\\/")
        rows += f"""<tr onclick="toggle({i})" style="cursor:pointer">
  <td style="padding:10px 12px;font-weight:500;color:#F8FAFC">{ag['name']}<br><span style="font-size:0.72rem;color:#475569">{ag['pk_fingerprint']}</span></td>
  <td style="padding:10px 12px;color:#CBD5E1">{ag['role']}<br><span style="font-size:0.72rem;color:#6366F1;border:1px solid #312E81;padding:1px 5px;border-radius:4px">{ag['agent_type']}</span></td>
  <td style="padding:10px 12px">{badge}<br>{verify_badge}</td>
  <td style="padding:10px 12px;color:#94A3B8">{ag['last_seen_rel']}</td>
  <td style="padding:10px 12px;color:#94A3B8">{ag['project']}<br><span style="font-size:0.72rem;color:#475569">{ag['edition']}</span></td>
  <td style="padding:10px 12px;color:#94A3B8">{squads_cell}<br><span style="font-size:0.72rem;color:#94A3B8">C={ag['coherence']}</span></td>
</tr>
<tr id="expand-{i}" style="display:none">
  <td colspan="6" style="padding:0 12px 12px">
    <pre style="background:#0F172A;border:1px solid #334155;border-radius:8px;padding:16px;font-size:0.78rem;color:#A5B4FC;overflow-x:auto;white-space:pre-wrap">{raw_escaped}</pre>
  </td>
</tr>"""

    if not rows:
        rows = '<tr><td colspan="6" style="padding:24px;text-align:center;color:#64748B">No agents registered in sos:registry:*</td></tr>'

    nav_html = _sos_nav("Agents")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SOS Agents — Operator Dashboard</title>
<style>
{_SOS_BASE_CSS}
table{{width:100%;border-collapse:collapse;background:#1E293B;border:1px solid #334155;border-radius:12px;overflow:hidden}}
thead tr{{background:#0F172A}}
th{{padding:10px 12px;text-align:left;font-size:0.72rem;color:#64748B;text-transform:uppercase;letter-spacing:0.06em;font-weight:600;border-bottom:1px solid #334155}}
tbody tr:hover td{{background:#263449}}
</style>
</head>
<body>
<header>
  <div>
    <h1>&#9670; SOS Engine &mdash; Agents</h1>
    <div class="meta">Registry snapshot &middot; {now_str} &middot; {len(agents)} agents</div>
  </div>
  <a href="/logout" class="logout">Sign Out</a>
</header>

{nav_html}

<table>
  <thead>
    <tr>
      <th>Name / Key</th>
      <th>Role / Type</th>
      <th>Status / Verify</th>
      <th>Last Seen</th>
      <th>Project / Edition</th>
      <th>Squads / C</th>
    </tr>
  </thead>
  <tbody>
    {rows}
  </tbody>
</table>

<p class="muted" style="margin-top:16px">Click any row to expand the full registry payload.</p>

<script>
function toggle(i) {{
  var el = document.getElementById('expand-' + i);
  if (el) el.style.display = el.style.display === 'none' ? 'table-row' : 'none';
}}
</script>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/sos/money", response_class=HTMLResponse)
async def sos_money(request: Request) -> Response:
    """SOS money pulse panel — admin-scoped only."""
    cookie_val = request.cookies.get(COOKIE_NAME)
    tenant = _tenant_from_cookie(cookie_val)
    if not tenant:
        return RedirectResponse(url="/login?next=/sos/money", status_code=302)
    if not _is_admin(tenant):
        return HTMLResponse("<h1>403 — admin scope required</h1>", status_code=403)

    now_str = datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC")
    now_ts = datetime.now(timezone.utc).timestamp()

    # --- Load all events ---
    try:
        from sos.clients.economy import EconomyClient
        import os
        client = EconomyClient(base_url=os.environ.get("SOS_ECONOMY_URL", "http://localhost:6062"))
        all_events = client.list_usage(limit=1000)
    except Exception:
        all_events = []

    nav_html = _sos_nav("Money")

    if not all_events:
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SOS Money Pulse — Operator Dashboard</title>
<style>{_SOS_BASE_CSS}</style>
</head>
<body>
<header>
  <div>
    <h1>&#9670; SOS Engine &mdash; Money Pulse</h1>
    <div class="meta">Moat metric panel &middot; {now_str}</div>
  </div>
  <a href="/logout" class="logout">Sign Out</a>
</header>
{nav_html}
<div class="card"><h3>Usage Summary</h3><p class="muted">No data yet — run some model calls to populate this panel.</p></div>
</body>
</html>"""
        return HTMLResponse(html)

    # --- Section 1: Usage summary ---
    total_events = len(all_events)
    events_24h = sum(1 for e in all_events if (now_ts - _event_ts(e.received_at)) < 86400)
    events_7d = sum(1 for e in all_events if (now_ts - _event_ts(e.received_at)) < 86400 * 7)
    total_cost = sum(e.cost_micros for e in all_events)

    # Top 5 tenants by spend
    tenant_spend: dict[str, tuple[int, int]] = {}  # tenant -> (count, cost_micros)
    for ev in all_events:
        t = ev.tenant or "(none)"
        cnt, cost = tenant_spend.get(t, (0, 0))
        tenant_spend[t] = (cnt + 1, cost + ev.cost_micros)
    top_tenants = sorted(tenant_spend.items(), key=lambda x: x[1][1], reverse=True)[:5]

    # Top 5 models by count
    model_count: dict[str, int] = {}
    for ev in all_events:
        m = ev.model or "(none)"
        model_count[m] = model_count.get(m, 0) + 1
    top_models = sorted(model_count.items(), key=lambda x: x[1], reverse=True)[:5]

    # --- Section 2: AI-to-AI commerce ---
    ai2ai = [ev for ev in all_events if ev.metadata.get("ai_to_ai_commerce") is True]
    ai2ai_count = len(ai2ai)
    ai2ai_mind = sum(ev.cost_micros for ev in ai2ai)
    recent_ai2ai = sorted(ai2ai, key=lambda ev: ev.received_at, reverse=True)[:5]

    # --- Section 3: Settlement split totals ---
    total_author = 0
    total_operator_split = 0
    total_network = 0
    for ev in all_events:
        s = ev.metadata.get("settlement", {})
        if isinstance(s, dict):
            total_author += int(s.get("author", 0) or 0)
            total_operator_split += int(s.get("operator", 0) or 0)
            total_network += int(s.get("network", 0) or 0)

    # --- Build rows ---
    tenant_rows = ""
    for t_name, (t_cnt, t_cost) in top_tenants:
        tenant_rows += f'<div class="row"><span style="color:#A5B4FC">{t_name}</span><span>{t_cnt} events</span><span style="color:#34D399">{_fmt_micros(t_cost)}</span></div>'

    model_rows = ""
    for m_name, m_cnt in top_models:
        model_rows += f'<div class="row"><span style="color:#A5B4FC">{m_name}</span><span>{m_cnt}</span></div>'

    ai2ai_rows = ""
    if recent_ai2ai:
        for ev in recent_ai2ai:
            seller = ev.metadata.get("seller_skill", ev.endpoint or "—")
            buyer = ev.tenant or "—"
            payout = _fmt_micros(int(ev.metadata.get("author_payout_micros", 0) or 0))
            when = _relative_time(ev.received_at)
            ai2ai_rows += f'<div class="row"><span style="color:#A5B4FC;max-width:160px;overflow:hidden;text-overflow:ellipsis">{seller}</span><span>{buyer}</span><span style="color:#34D399">{payout}</span><span class="muted">{when}</span></div>'
    else:
        ai2ai_rows = '<div class="muted" style="padding:12px 0">No AI-to-AI transactions yet</div>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SOS Money Pulse — Operator Dashboard</title>
<style>{_SOS_BASE_CSS}</style>
</head>
<body>
<header>
  <div>
    <h1>&#9670; SOS Engine &mdash; Money Pulse</h1>
    <div class="meta">Moat metric panel &middot; {now_str}</div>
  </div>
  <a href="/logout" class="logout">Sign Out</a>
</header>

{nav_html}

<div class="grid">

  <div class="card">
    <h3>All-Time Events</h3>
    <div class="val">{total_events:,}</div>
    <div class="muted">{events_24h:,} last 24h &middot; {events_7d:,} last 7d</div>
  </div>

  <div class="card">
    <h3>Total Spend (all tenants)</h3>
    <div class="val">{_fmt_micros(total_cost)}</div>
    <div class="muted">all-time &middot; cost_micros summed</div>
  </div>

  <div class="card">
    <h3>AI-to-AI Commerce</h3>
    <div class="val">{ai2ai_count:,}</div>
    <div class="muted">transactions &middot; <span style="color:#34D399">{_fmt_micros(ai2ai_mind)}</span> $MIND flowed</div>
  </div>

</div>

<div class="grid">

  <div class="card">
    <h3>Top 5 Tenants by Spend</h3>
    <div style="margin-top:8px">
      <div class="row" style="color:#64748B;font-size:0.75rem"><span>Tenant</span><span>Events</span><span>Spend</span></div>
      {tenant_rows}
    </div>
  </div>

  <div class="card">
    <h3>Top 5 Models by Usage</h3>
    <div style="margin-top:8px">
      <div class="row" style="color:#64748B;font-size:0.75rem"><span>Model</span><span>Calls</span></div>
      {model_rows}
    </div>
  </div>

  <div class="card">
    <h3>Settlement Split Totals</h3>
    <div class="row"><span class="muted">Authors</span><span style="color:#34D399">{_fmt_micros(total_author)}</span></div>
    <div class="row"><span class="muted">Operator</span><span style="color:#FBBF24">{_fmt_micros(total_operator_split)}</span></div>
    <div class="row"><span class="muted">Network / Pool</span><span style="color:#A5B4FC">{_fmt_micros(total_network)}</span></div>
  </div>

</div>

<div class="card" style="margin-bottom:24px">
  <h3>Recent AI-to-AI Transactions</h3>
  <div style="margin-top:8px">
    <div class="row" style="color:#64748B;font-size:0.75rem"><span>Seller Skill</span><span>Buyer Tenant</span><span>Author Payout</span><span>When</span></div>
    {ai2ai_rows}
  </div>
</div>

</body>
</html>"""
    return HTMLResponse(html)


@router.get("/sos/skills", response_class=HTMLResponse)
async def sos_skills(request: Request) -> Response:
    """SOS skill-moat panel — admin-scoped only."""
    cookie_val = request.cookies.get(COOKIE_NAME)
    tenant = _tenant_from_cookie(cookie_val)
    if not tenant:
        return RedirectResponse(url="/login?next=/sos/skills", status_code=302)
    if not _is_admin(tenant):
        return HTMLResponse("<h1>403 — admin scope required</h1>", status_code=403)

    now_str = datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC")

    # --- Load skill cards ---
    try:
        from sos.skills.registry import Registry
        reg = Registry()
        cards = reg.list()
    except Exception:
        cards = []

    # Sort by total_earned_micros desc
    def _earned(c: Any) -> int:
        return (c.earnings.total_earned_micros or 0) if c.earnings else 0

    cards_sorted = sorted(cards, key=_earned, reverse=True)
    nav_html = _sos_nav("Skills")

    if not cards_sorted:
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SOS Skills — Operator Dashboard</title>
<style>{_SOS_BASE_CSS}</style>
</head>
<body>
<header>
  <div>
    <h1>&#9670; SOS Engine &mdash; Skill Moat</h1>
    <div class="meta">Moat metric panel &middot; {now_str}</div>
  </div>
  <a href="/logout" class="logout">Sign Out</a>
</header>
{nav_html}
<div class="card"><h3>Skill Earnings Leaderboard</h3><p class="muted">No data yet — register skills via the Registry to populate this panel.</p></div>
</body>
</html>"""
        return HTMLResponse(html)

    # --- Leaderboard table rows ---
    lb_rows = ""
    for card in cards_sorted:
        e = card.earnings
        invocations = (e.total_invocations or 0) if e else 0
        earned = _fmt_micros((e.total_earned_micros or 0) if e else 0)
        by_tenant: dict[str, int] = (e.invocations_by_tenant or {}) if e else {}
        unique_t = len(by_tenant)
        ver_status = card.verification.status if card.verification else "unverified"
        ver_color = {
            "human_verified": "#34D399",
            "auto_verified": "#FBBF24",
            "unverified": "#64748B",
            "disputed": "#F87171",
        }.get(ver_status, "#64748B")
        mkt = "Y" if (card.commerce and card.commerce.marketplace_listed) else "N"
        mkt_color = "#34D399" if mkt == "Y" else "#64748B"
        author = card.author_agent.replace("agent:", "")
        lb_rows += f"""<tr>
  <td style="padding:10px 12px;font-weight:500;color:#F8FAFC">{card.name}</td>
  <td style="padding:10px 12px;color:#94A3B8">{author}</td>
  <td style="padding:10px 12px;text-align:right">{invocations:,}</td>
  <td style="padding:10px 12px;text-align:right;color:#34D399">{earned}</td>
  <td style="padding:10px 12px;text-align:right">{unique_t}</td>
  <td style="padding:10px 12px"><span style="color:{ver_color}">{ver_status}</span></td>
  <td style="padding:10px 12px;text-align:center;color:{mkt_color}">{mkt}</td>
</tr>"""

    # --- Cross-tenant reuse panel ---
    multi_tenant = [c for c in cards_sorted if c.earnings and c.earnings.invocations_by_tenant and len(c.earnings.invocations_by_tenant) > 1]
    moat_score = len(multi_tenant)
    total_skills = len(cards_sorted)

    # Bar charts — top 10 skills with tenant data
    bar_colors = ["#6366F1", "#34D399", "#FBBF24", "#F87171", "#A78BFA", "#60A5FA"]
    cards_with_tenants = sorted(
        [c for c in cards_sorted if c.earnings and c.earnings.invocations_by_tenant],
        key=lambda c: len((c.earnings.invocations_by_tenant or {}) if c.earnings else {}),  # type: ignore[union-attr]
        reverse=True,
    )[:10]

    reuse_rows = ""
    for card in cards_with_tenants:
        by_tenant = (card.earnings.invocations_by_tenant or {}) if card.earnings else {}  # type: ignore[union-attr]
        total_inv = sum(by_tenant.values()) or 1
        bar_cells = ""
        for i, (t_name, t_cnt) in enumerate(sorted(by_tenant.items(), key=lambda x: x[1], reverse=True)):
            pct = max(4, int(t_cnt / total_inv * 100))
            color = bar_colors[i % len(bar_colors)]
            bar_cells += f'<div title="{t_name}: {t_cnt}" style="height:20px;width:{pct}%;background:{color};display:inline-block;margin-right:2px;border-radius:3px"></div>'
        tenant_labels = " &middot; ".join(
            f'<span style="color:#94A3B8">{t}</span>({c})'
            for t, c in sorted(by_tenant.items(), key=lambda x: x[1], reverse=True)
        )
        reuse_rows += f"""<div style="padding:12px 0;border-bottom:1px solid #0F172A">
  <div style="display:flex;justify-content:space-between;margin-bottom:6px">
    <span style="font-weight:500;color:#F8FAFC">{card.name}</span>
    <span class="muted">{len(by_tenant)} tenants &middot; {sum(by_tenant.values())} total calls</span>
  </div>
  <div style="display:flex;align-items:center;gap:2px;margin-bottom:4px">{bar_cells}</div>
  <div style="font-size:0.78rem">{tenant_labels}</div>
</div>"""

    if not reuse_rows:
        reuse_rows = '<p class="muted">No cross-tenant invocation data yet.</p>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SOS Skills — Operator Dashboard</title>
<style>
{_SOS_BASE_CSS}
table{{width:100%;border-collapse:collapse;background:#1E293B;border:1px solid #334155;border-radius:12px;overflow:hidden}}
thead tr{{background:#0F172A}}
th{{padding:10px 12px;text-align:left;font-size:0.72rem;color:#64748B;text-transform:uppercase;letter-spacing:0.06em;font-weight:600;border-bottom:1px solid #334155}}
th.num{{text-align:right}}
tbody tr:hover td{{background:#263449}}
</style>
</head>
<body>
<header>
  <div>
    <h1>&#9670; SOS Engine &mdash; Skill Moat</h1>
    <div class="meta">Moat metric panel &middot; {now_str} &middot; {total_skills} skills</div>
  </div>
  <a href="/logout" class="logout">Sign Out</a>
</header>

{nav_html}

<div class="grid" style="margin-bottom:24px">
  <div class="card">
    <h3>Total Skills</h3>
    <div class="val">{total_skills}</div>
    <div class="muted">in registry</div>
  </div>
  <div class="card">
    <h3>Multi-Tenant Skills</h3>
    <div class="val" style="color:#34D399">{moat_score}</div>
    <div class="muted">used by &gt;1 tenant &mdash; THE moat metric</div>
  </div>
</div>

<table style="margin-bottom:24px">
  <thead>
    <tr>
      <th>Name</th>
      <th>Author</th>
      <th class="num">Invocations</th>
      <th class="num">Earned</th>
      <th class="num">Unique Tenants</th>
      <th>Verification</th>
      <th style="text-align:center">Marketplace</th>
    </tr>
  </thead>
  <tbody>
    {lb_rows}
  </tbody>
</table>

<div class="card">
  <h3>Cross-Tenant Reuse (top 10 skills)</h3>
  <p class="muted" style="margin-bottom:16px">Bar width = fraction of invocations per tenant. Skills with wider spread have deeper moat.</p>
  {reuse_rows}
</div>

</body>
</html>"""
    return HTMLResponse(html)
