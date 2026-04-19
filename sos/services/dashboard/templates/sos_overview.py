"""SOS /sos/overview page helpers — base CSS and nav generator."""

from __future__ import annotations

_SOS_BASE_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0F172A;color:#E2E8F0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;padding:24px;max-width:1400px;margin:0 auto;min-height:100vh}
header{display:flex;align-items:center;justify-content:space-between;margin-bottom:32px;padding-bottom:16px;border-bottom:1px solid #1E293B}
header h1{font-size:1.4rem;color:#F8FAFC;font-weight:600}
header .meta{color:#94A3B8;font-size:0.8rem;margin-top:4px}
.logout{color:#94A3B8;text-decoration:none;font-size:0.85rem;border:1px solid #334155;padding:6px 14px;border-radius:6px}
.logout:hover{color:#F8FAFC;border-color:#6366F1}
nav a{padding:6px 14px;border-radius:6px;color:#94A3B8;text-decoration:none;border:1px solid #1E293B}
nav a.active{background:#1E293B;color:#F8FAFC;border-color:#334155}
nav a:hover{color:#F8FAFC}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:20px;margin-bottom:24px}
.card{background:#1E293B;border:1px solid #334155;border-radius:12px;padding:24px}
.card h3{font-size:0.75rem;color:#94A3B8;text-transform:uppercase;letter-spacing:0.07em;margin-bottom:16px}
.val{font-size:2rem;font-weight:700;color:#F8FAFC;margin-bottom:4px}
.val-sm{font-size:1.1rem;font-weight:600;color:#F8FAFC}
.muted{color:#64748B;font-size:0.82rem}
.row{display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid #0F172A;font-size:0.85rem}
.row:last-child{border:none}
.dot-green{color:#34D399}
.dot-red{color:#F87171}
.dot-amber{color:#FBBF24}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:0.7rem;font-weight:600;margin-left:6px}
.badge-green{background:#064E3B;color:#34D399}
.badge-red{background:#450A0A;color:#F87171}
.badge-amber{background:#451A03;color:#FBBF24}
"""


def _sos_nav(active: str) -> str:
    pages = [
        ("/sos", "Flow Map"),
        ("/sos/overview", "Overview"),
        ("/sos/agents", "Agents"),
        ("/sos/mesh", "Mesh"),
        ("/sos/bus", "Bus"),
        ("/sos/money", "Money"),
        ("/sos/skills", "Skills"),
        ("/sos/incidents", "Incidents"),
        ("/sos/contracts", "Contracts"),
        ("/sos/providers", "Providers"),
    ]
    links = ""
    for href, label in pages:
        cls = ' class="active"' if label == active else ""
        links += f'<a href="{href}"{cls}>{label}</a>'
    return f'<nav style="display:flex;gap:8px;margin-bottom:24px;font-size:0.85rem">{links}</nav>'
