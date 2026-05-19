"""Traces dashboard HTML — inline templates for /sos/traces/html and
/sos/traces/{trace_id}/html.

Same inline-string pattern as login.py and brain.py — no Jinja2 dep, no
separate asset directory. The index page renders the TraceIndexResponse
as a sortable-by-time table; the detail page renders one trace's events
in chronological order.

Escaping contract: the route handler is responsible for running every
dynamic field through ``html.escape`` (or equivalent) before substitution.
These templates assume that has happened.
"""
from __future__ import annotations

_SHARED_CSS = """
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0F172A;color:#E2E8F0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;padding:32px;min-height:100vh}}
h1{{font-size:1.5rem;color:#F8FAFC;margin-bottom:4px}}
.subtitle{{color:#94A3B8;font-size:0.875rem;margin-bottom:24px}}
.backlink{{color:#94A3B8;font-size:0.85rem;text-decoration:none;margin-bottom:12px;display:inline-block}}
.backlink:hover{{color:#E2E8F0}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;margin-bottom:24px}}
.card{{background:#1E293B;border:1px solid #334155;border-radius:12px;padding:20px}}
.card h2{{font-size:0.75rem;color:#94A3B8;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:8px}}
.card .value{{font-size:1.5rem;color:#F8FAFC;font-weight:600}}
.card .sub{{font-size:0.8rem;color:#94A3B8;margin-top:4px}}
section{{background:#1E293B;border:1px solid #334155;border-radius:12px;padding:20px;margin-bottom:16px}}
section h2{{font-size:0.9rem;color:#CBD5E1;margin-bottom:12px}}
table{{width:100%;border-collapse:collapse;font-size:0.85rem}}
th,td{{padding:8px 10px;text-align:left;border-bottom:1px solid #334155;vertical-align:top}}
th{{color:#94A3B8;font-weight:500;text-transform:uppercase;font-size:0.7rem;letter-spacing:0.05em}}
td{{color:#E2E8F0}}
td a{{color:#93C5FD;text-decoration:none}}
td a:hover{{color:#DBEAFE;text-decoration:underline}}
code{{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;font-size:0.82rem;color:#CBD5E1}}
.pill{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:0.7rem;background:#334155;color:#CBD5E1;text-transform:uppercase;letter-spacing:0.03em}}
.kind-intent{{background:#1E3A5F;color:#BFDBFE}}
.kind-policy_decision{{background:#3F2F5C;color:#DDD6FE}}
.kind-action_completed{{background:#14532D;color:#BBF7D0}}
.kind-action_failed{{background:#5B1F1F;color:#FECACA}}
.kind-arbitration{{background:#4D3A0F;color:#FDE68A}}
.decision-allow{{color:#4ADE80}}
.decision-deny{{color:#F87171}}
.decision-require_approval{{color:#FBBF24}}
.decision-n\\/a{{color:#64748B}}
.empty{{color:#64748B;font-style:italic;padding:8px 0}}
details{{background:#0F172A;border:1px solid #334155;border-radius:6px;padding:6px 10px;margin-top:4px}}
details summary{{cursor:pointer;color:#94A3B8;font-size:0.75rem}}
details pre{{margin-top:6px;overflow-x:auto;color:#CBD5E1;font-size:0.75rem;white-space:pre-wrap;word-break:break-word}}
.footer{{color:#64748B;font-size:0.75rem;margin-top:32px;text-align:center}}
"""


TRACES_INDEX_HTML = (
    """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mumega — Traces</title>
<style>"""
    + _SHARED_CSS
    + """</style>
</head>
<body>
<h1>Traces</h1>
<p class="subtitle">Audit events grouped by <code>trace_id</code> · scanning last <code>{days}</code> day(s) · showing {shown}/{total}</p>

<div class="grid">
  <div class="card"><h2>Traces</h2><div class="value">{total}</div><div class="sub">distinct trace_ids</div></div>
  <div class="card"><h2>Events</h2><div class="value">{event_total}</div><div class="sub">across shown traces</div></div>
  <div class="card"><h2>Tenants</h2><div class="value">{tenant_total}</div><div class="sub">touched</div></div>
  <div class="card"><h2>Agents</h2><div class="value">{agent_total}</div><div class="sub">involved</div></div>
</div>

<section>
  <h2>Recent traces</h2>
  <table>
    <thead>
      <tr>
        <th>Trace</th>
        <th>First</th>
        <th>Last</th>
        <th>Events</th>
        <th>Tenants</th>
        <th>Agents</th>
        <th>Kinds</th>
      </tr>
    </thead>
    <tbody>{trace_rows}</tbody>
  </table>
</section>

<p class="footer">Mumega SOS · disk audit sink is authoritative · newest last_ts first</p>
</body>
</html>"""
)


TRACES_DETAIL_HTML = (
    """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mumega — Trace {trace_id_short}</title>
<style>"""
    + _SHARED_CSS
    + """</style>
</head>
<body>
<a class="backlink" href="/sos/traces/html">← All traces</a>
<h1>Trace</h1>
<p class="subtitle"><code>{trace_id}</code> · {event_count} event(s) · {first_ts} → {last_ts}</p>

<section>
  <h2>Events (oldest first)</h2>
  <table>
    <thead>
      <tr>
        <th>#</th>
        <th>Timestamp</th>
        <th>Kind</th>
        <th>Agent / Tenant</th>
        <th>Action</th>
        <th>Target</th>
        <th>Decision</th>
        <th>Cost</th>
        <th>Payload</th>
      </tr>
    </thead>
    <tbody>{event_rows}</tbody>
  </table>
</section>

<p class="footer">Mumega SOS · events are frozen at emit time · inputs/outputs/metadata already sanitised</p>
</body>
</html>"""
)
