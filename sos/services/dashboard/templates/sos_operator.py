"""SOS operator flow-map template + SVG loader."""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("dashboard")

_SERVICE_MAP_PATH = Path(__file__).resolve().parent.parent / "service_map.svg"
_service_map_cache: str | None = None


def _load_service_map_svg() -> str:
    """Read the service-map SVG once, cache in-process. Empty string on failure."""
    global _service_map_cache
    if _service_map_cache is None:
        try:
            _service_map_cache = _SERVICE_MAP_PATH.read_text(encoding="utf-8")
        except Exception:
            logger.exception("Failed to load service map SVG")
            _service_map_cache = ""
    return _service_map_cache


_SOS_FLOW_MAP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SOS Engine — Operator Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0F172A;color:#E2E8F0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;padding:24px;max-width:1400px;margin:0 auto;min-height:100vh}
header{display:flex;align-items:center;justify-content:space-between;margin-bottom:32px;padding-bottom:16px;border-bottom:1px solid #1E293B}
header h1{font-size:1.4rem;color:#F8FAFC;font-weight:600}
header .meta{color:#94A3B8;font-size:0.8rem;margin-top:4px}
.logout{color:#94A3B8;text-decoration:none;font-size:0.85rem;border:1px solid #334155;padding:6px 14px;border-radius:6px}
.logout:hover{color:#F8FAFC;border-color:#6366F1}
nav{display:flex;gap:8px;margin-bottom:24px;font-size:0.85rem}
nav a{padding:6px 14px;border-radius:6px;color:#94A3B8;text-decoration:none;border:1px solid #1E293B}
nav a.active{background:#1E293B;color:#F8FAFC;border-color:#334155}
nav a:hover{color:#F8FAFC}
.subtitle{color:#64748B;font-size:0.9rem;margin-bottom:24px;line-height:1.5}
.flow-map{background:#1E293B;border:1px solid #334155;border-radius:12px;padding:32px;margin-bottom:24px;overflow-x:auto}
.legend{display:flex;flex-wrap:wrap;gap:16px;margin-top:16px;padding-top:16px;border-top:1px solid #0F172A}
.legend-item{display:flex;align-items:center;gap:8px;font-size:0.8rem;color:#94A3B8}
.legend-box{width:14px;height:14px;border-radius:3px}
.info-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}
.info-card{background:#1E293B;border:1px solid #334155;border-radius:10px;padding:20px}
.info-card h3{font-size:0.8rem;color:#94A3B8;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:12px}
.info-card p{color:#CBD5E1;font-size:0.85rem;line-height:1.6}
.info-card ul{list-style:none;padding:0;margin-top:8px}
.info-card li{padding:4px 0;color:#CBD5E1;font-size:0.85rem}
.info-card code{color:#A78BFA;font-family:'SF Mono',Monaco,monospace;font-size:0.8rem}
svg text{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
.phase-tag{display:inline-block;background:#6366F1;color:#fff;padding:2px 10px;border-radius:999px;font-size:0.7rem;margin-left:8px;font-weight:500}
</style>
</head>
<body>
<header>
  <div>
    <h1>&#9670; SOS Engine <span class="phase-tag">Phase 0</span></h1>
    <div class="meta">Operator view of the organism &middot; {timestamp}</div>
  </div>
  <a href="/logout" class="logout">Sign Out</a>
</header>

<nav>
  <a href="/sos" class="active">Flow Map</a>
  <a href="/sos/agents" title="Phase 1">Agents</a>
  <a href="/sos/bus" title="Phase 1">Bus</a>
  <a href="/sos/money">Money</a>
  <a href="/sos/skills">Skills</a>
  <a href="/sos/incidents" title="Phase 2">Incidents</a>
  <a href="/sos/contracts" title="Phase 1">Contracts</a>
  <a href="/sos/providers" title="Phase 3">Providers</a>
</nav>

<p class="subtitle">
  The data-flow map: how messages, money, and memory move through the system.
  Each arrow is labeled with the primitive that carries it. Everything is
  read-only. Write operations happen through <code>sos status</code> CLI.
</p>

<div class="flow-map">
  {service_map_svg}
  <div class="legend">
    <div class="legend-item"><div class="legend-box" style="background:#6366F1"></div>Bus / v1 messages</div>
    <div class="legend-item"><div class="legend-box" style="background:#34D399"></div>Money / settlement / $MIND</div>
    <div class="legend-item"><div class="legend-box" style="background:#A78BFA"></div>Memory / telemetry</div>
    <div class="legend-item"><div class="legend-box" style="background:#FBBF24"></div>Squad / task coordination</div>
    <div class="legend-item"><div class="legend-box" style="background:#64748B"></div>External / HTTP</div>
    <div class="legend-item"><div class="legend-box" style="background:#F87171;border:1px dashed #F87171"></div>Debt / broken</div>
  </div>
  <div class="legend" style="margin-top:8px">
    <div class="legend-item"><div style="display:inline-block;width:14px;height:14px;border-radius:50%;background:#22D3EE;color:#0F172A;font-size:9px;font-weight:700;text-align:center;line-height:14px">C</div>&nbsp;Community (sos-community, Apache 2.0 — proposed)</div>
    <div class="legend-item"><div style="display:inline-block;width:14px;height:14px;border-radius:50%;background:#EAB308;color:#0F172A;font-size:9px;font-weight:700;text-align:center;line-height:14px">P</div>&nbsp;Proprietary core (Mumega commercial)</div>
    <div class="legend-item"><div style="display:inline-block;width:14px;height:14px;border-radius:50%;background:#64748B;color:#F8FAFC;font-size:9px;font-weight:700;text-align:center;line-height:14px">X</div>&nbsp;External / third-party</div>
  </div>
</div>
<div class="info-grid">
  <div class="info-card">
    <h3>Contracts (v0.4.0)</h3>
    <p>All 8 bus message types are JSON-Schema-validated at construction:</p>
    <ul>
      <li><code>announce</code>, <code>send</code>, <code>wake</code>, <code>ask</code></li>
      <li><code>task_created</code>, <code>task_claimed</code>, <code>task_completed</code></li>
      <li><code>agent_joined</code></li>
    </ul>
    <p style="margin-top:12px;">Strict enforcement rejects unknown types (<code>SOS-4004</code>).</p>
  </div>

  <div class="info-card">
    <h3>Economy primitives</h3>
    <p>Currency-agnostic ledger supports USD, $MIND, or operator-defined units:</p>
    <ul>
      <li><code>POST /usage</code> — tenant-scoped event ingest</li>
      <li><code>POST /credit</code>, <code>/debit</code>, <code>/transmute</code></li>
      <li><code>PricingEntry</code> — per-token + flat-per-call</li>
    </ul>
  </div>

  <div class="info-card">
    <h3>Not in Phase 0 yet</h3>
    <p>These panels ship in Phase 1-3 as plugins (same microkernel pattern as Inkwell):</p>
    <ul>
      <li>Live agent grid with heartbeat</li>
      <li>Bus pulse (msgs/sec, SSE sessions)</li>
      <li>Money pulse (MRR, settlements)</li>
      <li>Skill-moat panel (earnings per skill)</li>
      <li>Competitor release feed</li>
    </ul>
  </div>

  <div class="info-card">
    <h3>Boundary note</h3>
    <p>This dashboard shows what SOS is, not what Mumega sells on top.</p>
    <p style="margin-top:8px">Commercial overlays (Stripe USD invoicing, volume tiers, per-customer P&L) layer on the ledger without touching kernel code.</p>
    <p style="margin-top:8px;color:#64748B">The kernel stays substrate-agnostic — same code on RPi, VPS, Workers.</p>
  </div>
</div>

</body>
</html>"""
