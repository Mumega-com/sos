"""Default standing workflows injected by `sos init` Step D.

The Inkwell ``_template/standing_workflows.json`` ships with whatever
workflows Inkwell wants as defaults. `sos init` Step D ensures the
SOS-side growth-intel workflow exists on every new tenant, regardless
of the template state — Inkwell and SOS evolve independently, so SOS
owns its own operational workflows.

Each entry uses ``trigger: "auto"`` so the Inkwell automation plugin
(v8.3) picks the best available provider at runtime:
    CF Workflows > ToRivers > n8n > Zapier > generic webhook.
"""

from __future__ import annotations

from typing import Any


def default_workflows(slug: str) -> list[dict[str, Any]]:
    """Return the SOS-owned workflows every tenant gets by default."""
    return [
        {
            "name": f"{slug}-growth-intel",
            "description": "Daily growth intelligence pull + dossier",
            "schedule": "0 10 * * *",
            "steps": ["trend-finder", "narrative-synth", "dossier-writer"],
            "bounty_mind": 50,
            "trigger": "auto",
            "owner": "sos.growth_intel",
        },
    ]


def ensure_workflows_present(data: dict[str, Any], slug: str) -> dict[str, Any]:
    """Merge the SOS defaults into a standing_workflows payload.

    Pure function: mutates + returns the passed dict so the caller can
    write it back to disk. Matches a workflow by ``name`` — if a default
    with the same name already exists in ``data``, we leave the
    tenant's version alone (tenant customization wins).
    """
    existing = data.setdefault("workflows", [])
    existing_names = {w.get("name") for w in existing if isinstance(w, dict)}
    for workflow in default_workflows(slug):
        if workflow["name"] not in existing_names:
            existing.append(workflow)
    return data
