"""Customer /dashboard HTML template and moat helpers."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..tenants import _fmt_micros


def _moat_skills_invoked_html(skills: list[dict[str, Any]]) -> str:
    if not skills:
        return '<p class="muted" style="margin-top:8px;font-size:0.85rem">No skill invocations yet. Browse the marketplace to start.</p>'
    rows = ""
    for s in skills[:5]:
        ver = s.get("verification", "unverified")
        ver_color = {"human_verified": "#34D399", "auto_verified": "#FBBF24", "disputed": "#F87171"}.get(ver, "#64748B")
        rows += f'<li><span style="color:{ver_color}">&#9679;</span> <strong>{s["name"]}</strong> <span class="muted">× {s["invocations"]}</span></li>'
    return f'<ul style="margin-top:12px">{rows}</ul>'


def _moat_skills_authored_html(skills: list[dict[str, Any]]) -> str:
    if not skills:
        return '<p class="muted" style="margin-top:8px;font-size:0.85rem">You haven\'t authored any skills yet. Publish to the marketplace to earn.</p>'
    rows = ""
    for s in skills[:5]:
        listed = "📢" if s.get("marketplace_listed") else ""
        rows += (
            f'<li><strong>{s["name"]}</strong> {listed} '
            f'<span class="muted">&middot; {s["total_invocations"]} calls '
            f'across {s["unique_tenants"]} tenants</span></li>'
        )
    return f'<ul style="margin-top:12px">{rows}</ul>'


def _moat_recent_usage_html(events: list[dict[str, Any]]) -> str:
    if not events:
        return '<p class="muted" style="margin-top:8px;font-size:0.85rem">No calls yet. Your first invocation shows up here.</p>'
    rows = ""
    for e in events[:10]:
        # parse occurred_at to friendly
        ts = e.get("occurred_at", "")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            ts = dt.strftime("%b %d, %H:%M")
        except Exception:
            pass
        cost = _fmt_micros(e.get("cost_micros", 0))
        model = e.get("model", "?")
        # truncate long model names
        if len(model) > 40:
            model = model[:37] + "..."
        rows += (
            f'<li style="font-size:0.8rem"><span class="muted">{ts}</span> '
            f'<code style="color:#A78BFA;font-size:0.75rem">{model}</code> '
            f'<strong style="color:#34D399">{cost}</strong></li>'
        )
    return f'<ul style="margin-top:8px">{rows}</ul>'


def _dashboard_html(
    tenant: dict[str, Any],
    agents: dict[str, Any],
    tasks: list[dict[str, Any]],
    memory: dict[str, Any],
    moat: dict[str, Any] | None = None,
) -> str:
    label = tenant.get("label", tenant.get("project", "Tenant"))
    project = tenant.get("project", "")
    moat = moat or {"skills_invoked": [], "skills_authored": [], "recent_usage": [], "total_spent_micros": 0, "total_earned_micros": 0}

    # Agent cards
    agent_rows = ""
    if agents["agents"]:
        for a in agents["agents"]:
            status_dot = '<span style="color:#34D399">&#9679;</span>' if a.get("status") == "online" else '<span style="color:#F87171">&#9679;</span>'
            last = a.get("last_seen", "—")
            if last and last != "—":
                try:
                    dt = datetime.fromisoformat(last)
                    last = dt.strftime("%b %d, %H:%M")
                except Exception:
                    pass
            agent_rows += f'<div class="agent-row">{status_dot} <strong>{a["name"]}</strong> <span class="muted">Last seen: {last}</span></div>'
    else:
        agent_rows = '<p class="muted">No agents registered yet</p>'

    # Task list
    task_rows = ""
    if tasks:
        for t in tasks:
            title = t.get("title", t.get("name", "Untitled"))
            status = t.get("status", "unknown")
            badge_color = {"done": "#34D399", "claimed": "#FBBF24", "open": "#60A5FA"}.get(status, "#94A3B8")
            task_rows += f'<li><span class="badge" style="background:{badge_color}">{status}</span> {title}</li>'
    else:
        task_rows = '<li class="muted">No tasks yet</li>'

    # Memory
    mem_count = memory.get("count", 0)
    mem_latest = memory.get("latest")
    mem_preview = "No entries yet"
    if mem_latest:
        content = mem_latest.get("content", mem_latest.get("text", ""))
        mem_preview = (content[:120] + "...") if len(content) > 120 else content

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{label} — Mumega Dashboard</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0F172A;color:#E2E8F0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;padding:24px;max-width:1200px;margin:0 auto}}
header{{display:flex;align-items:center;justify-content:space-between;margin-bottom:32px;padding-bottom:16px;border-bottom:1px solid #1E293B}}
header h1{{font-size:1.4rem;color:#F8FAFC}}
header .meta{{color:#94A3B8;font-size:0.8rem}}
.logout{{color:#94A3B8;text-decoration:none;font-size:0.85rem;border:1px solid #334155;padding:6px 14px;border-radius:6px}}
.logout:hover{{color:#F8FAFC;border-color:#6366F1}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px}}
.card{{background:#1E293B;border:1px solid #334155;border-radius:12px;padding:24px}}
.card h3{{font-size:0.95rem;color:#94A3B8;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:16px}}
.card ul{{list-style:none;padding:0}}
.card li{{padding:6px 0;border-bottom:1px solid #0F172A;font-size:0.9rem}}
.card li:last-child{{border:none}}
.badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:0.7rem;color:#0F172A;font-weight:600;margin-right:8px;text-transform:uppercase}}
.agent-row{{padding:8px 0;border-bottom:1px solid #0F172A;font-size:0.9rem}}
.agent-row:last-child{{border:none}}
.muted{{color:#64748B;font-size:0.85rem}}
.stat{{font-size:2rem;font-weight:700;color:#F8FAFC;margin-bottom:4px}}
.preview{{color:#CBD5E1;font-size:0.85rem;margin-top:8px;line-height:1.5}}
.pill{{display:inline-block;background:#6366F1;color:#fff;padding:2px 10px;border-radius:999px;font-size:0.75rem;margin-left:8px}}
</style>
</head>
<body>
<header>
  <div>
    <h1>&#9670; {label}</h1>
    <span class="meta">{project or 'admin'} &middot; {datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC")}</span>
  </div>
  <a href="/logout" class="logout">Sign Out</a>
</header>

<div class="grid">
  <div class="card">
    <h3>Agents</h3>
    <div class="stat">{agents['online']}<span class="pill">online</span></div>
    {agent_rows}
  </div>

  <div class="card">
    <h3>Recent Tasks</h3>
    <ul>
      {task_rows}
    </ul>
  </div>

  <div class="card">
    <h3>Memory</h3>
    <div class="stat">{mem_count}</div>
    <p class="muted">entries stored</p>
    <p class="preview">{mem_preview}</p>
  </div>

  <div class="card">
    <h3>Analytics</h3>
    <p class="muted">Latest report</p>
    <div class="stat" style="font-size:1.2rem">Coming soon</div>
    <p class="preview">Analytics dashboard will show task throughput, memory growth, and agent uptime.</p>
  </div>

  <div class="card">
    <h3>Billing</h3>
    <p class="muted">Current plan</p>
    <div class="stat" style="font-size:1.2rem">Community</div>
    <p class="preview">$30/mo &middot; Includes agent access, task management, and memory storage.</p>
  </div>
</div>

<!-- Moat panels (SkillCard v1 + UsageLog — P1.1 customer surface) -->
<h2 style="font-size:1.1rem;color:#94A3B8;margin:32px 0 16px 0;text-transform:uppercase;letter-spacing:0.05em;font-weight:500">
  Your activity on Agent OS
</h2>
<div class="grid">

  <div class="card">
    <h3>Your Skills</h3>
    <p class="muted">Skills you've invoked</p>
    <div class="stat">{len(moat['skills_invoked'])}</div>
    {_moat_skills_invoked_html(moat['skills_invoked'])}
    <p class="muted" style="margin-top:12px;font-size:0.8rem">
      Total spent: <strong style="color:#F8FAFC">{_fmt_micros(moat['total_spent_micros'])}</strong>
    </p>
    <p class="preview" style="margin-top:8px;font-size:0.8rem">
      <a href="/marketplace" style="color:#A5B4FC;text-decoration:none">Browse more skills →</a>
    </p>
  </div>

  <div class="card">
    <h3>Your Earnings</h3>
    <p class="muted">Skills you've authored</p>
    <div class="stat" style="color:#34D399">{_fmt_micros(moat['total_earned_micros'])}</div>
    {_moat_skills_authored_html(moat['skills_authored'])}
  </div>

  <div class="card">
    <h3>Recent Usage</h3>
    <p class="muted">Last 10 calls</p>
    {_moat_recent_usage_html(moat['recent_usage'])}
  </div>

</div>

</body>
</html>"""
