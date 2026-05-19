"""HTML fragments for the /sos/mesh dashboard page.

All inline CSS and HTML strings live here so the route file stays
under the ruff E501 line-length limit.
"""

from __future__ import annotations

# Extra table CSS appended to _SOS_BASE_CSS in the mesh page.
MESH_TABLE_CSS = (
    "table{width:100%;border-collapse:collapse;background:#1E293B;"
    "border:1px solid #334155;border-radius:12px;overflow:hidden;margin-bottom:8px}"
    "thead tr{background:#0F172A}"
    "th{padding:10px 12px;text-align:left;font-size:0.72rem;color:#64748B;"
    "text-transform:uppercase;letter-spacing:0.06em;font-weight:600;"
    "border-bottom:1px solid #334155}"
    "tbody tr:hover td{background:#263449}"
)

_TABLE_HEADER = (
    "<tr>"
    "<th>Name</th><th>Role</th><th>Status</th>"
    "<th>Age</th><th>Heartbeat URL</th><th>Project</th>"
    "</tr>"
)

_EMPTY_ROW = (
    '<tr><td colspan="6" style="padding:16px;text-align:center;'
    'color:#64748B">no agents</td></tr>'
)


def squad_section_html(slug: str, rows_html: str, display: str) -> str:
    """Return HTML for one squad section (heading + table)."""
    header_color = "#94A3B8" if slug == "__unsquadded__" else "#A5B4FC"
    h3_style = (
        f"font-size:0.85rem;color:{header_color};"
        "text-transform:uppercase;letter-spacing:0.07em;margin:28px 0 10px"
    )
    body = rows_html if rows_html else _EMPTY_ROW
    return (
        f'<h3 style="{h3_style}">{display}</h3>\n'
        f"<table>\n"
        f"  <thead>\n    {_TABLE_HEADER}\n  </thead>\n"
        f"  <tbody>\n    {body}\n  </tbody>\n"
        f"</table>"
    )


def heartbeat_cell(url: str | None) -> str:
    """Return the heartbeat URL table cell value."""
    if url:
        return f'<a href="{url}" style="color:#818CF8;font-size:0.78rem">{url}</a>'
    return '<span class="muted">\u2014</span>'


def page_html(
    base_css: str,
    nav_html: str,
    now_str: str,
    total: int,
    sections: str,
) -> str:
    """Render the full /sos/mesh HTML page."""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>SOS Mesh \u2014 Operator Dashboard</title>\n"
        f"<style>\n{base_css}\n{MESH_TABLE_CSS}\n</style>\n"
        "</head>\n"
        "<body>\n"
        "<header>\n"
        "  <div>\n"
        "    <h1>&#9670; SOS Engine &mdash; Mesh</h1>\n"
        f'    <div class="meta">Last tick &middot; {now_str}'
        f" &middot; {total} agent(s) enrolled</div>\n"
        "  </div>\n"
        '  <a href="/logout" class="logout">Sign Out</a>\n'
        "</header>\n\n"
        f"{nav_html}\n\n"
        f"{sections}\n\n"
        '<p class="muted" style="margin-top:16px">Auto-refreshes every 30 s.</p>\n\n'
        "<script>setTimeout(() => location.reload(), 30000);</script>\n"
        "</body>\n"
        "</html>"
    )
