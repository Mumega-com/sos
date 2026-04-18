"""Brain dashboard HTML — inline template for /sos/brain/html.

Renders the BrainSnapshot JSON plus the live ProviderMatrix state (tier,
circuit-breaker state per card). The template follows the same inline-string
pattern as login.py; no Jinja2 dep, no separate asset directory.

Uses simple ``str.format`` substitution — fields are pre-escaped in the route
handler before being passed in.
"""
from __future__ import annotations

BRAIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mumega — Brain</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0F172A;color:#E2E8F0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;padding:32px;min-height:100vh}}
h1{{font-size:1.5rem;color:#F8FAFC;margin-bottom:4px}}
.subtitle{{color:#94A3B8;font-size:0.875rem;margin-bottom:24px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;margin-bottom:24px}}
.card{{background:#1E293B;border:1px solid #334155;border-radius:12px;padding:20px}}
.card h2{{font-size:0.75rem;color:#94A3B8;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:8px}}
.card .value{{font-size:1.75rem;color:#F8FAFC;font-weight:600}}
.card .sub{{font-size:0.8rem;color:#94A3B8;margin-top:4px}}
section{{background:#1E293B;border:1px solid #334155;border-radius:12px;padding:20px;margin-bottom:16px}}
section h2{{font-size:0.9rem;color:#CBD5E1;margin-bottom:12px}}
table{{width:100%;border-collapse:collapse;font-size:0.85rem}}
th,td{{padding:8px 10px;text-align:left;border-bottom:1px solid #334155}}
th{{color:#94A3B8;font-weight:500;text-transform:uppercase;font-size:0.7rem;letter-spacing:0.05em}}
td{{color:#E2E8F0}}
.state-closed{{color:#4ADE80}}
.state-half_open{{color:#FBBF24}}
.state-open{{color:#F87171}}
.tier{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:0.7rem;background:#334155;color:#CBD5E1;text-transform:uppercase}}
.empty{{color:#64748B;font-style:italic;padding:8px 0}}
.footer{{color:#64748B;font-size:0.75rem;margin-top:32px;text-align:center}}
</style>
</head>
<body>
<h1>Brain</h1>
<p class="subtitle">Snapshot as of <code>{last_update_ts}</code> · service started <code>{service_started_at}</code></p>

<div class="grid">
  <div class="card"><h2>Queue</h2><div class="value">{queue_size}</div><div class="sub">tasks pending</div></div>
  <div class="card"><h2>In-flight</h2><div class="value">{in_flight_count}</div><div class="sub">tasks routing</div></div>
  <div class="card"><h2>Events seen</h2><div class="value">{events_seen}</div><div class="sub">total since start</div></div>
  <div class="card"><h2>Providers</h2><div class="value">{provider_count}</div><div class="sub">{provider_healthy} healthy</div></div>
</div>

<section>
  <h2>Provider Matrix</h2>
  <table>
    <thead><tr><th>ID</th><th>Tier</th><th>Backend</th><th>Model</th><th>Breaker</th><th>Failures</th></tr></thead>
    <tbody>{provider_rows}</tbody>
  </table>
</section>

<section>
  <h2>Recent routes</h2>
  <table>
    <thead><tr><th>Task</th><th>Agent</th><th>Score</th><th>When</th></tr></thead>
    <tbody>{route_rows}</tbody>
  </table>
</section>

<section>
  <h2>Events by type</h2>
  <table>
    <thead><tr><th>Type</th><th>Count</th></tr></thead>
    <tbody>{event_type_rows}</tbody>
  </table>
</section>

<p class="footer">Mumega SOS · BrainService snapshot TTL = 30 s · refresh the page to re-fetch</p>
</body>
</html>"""
