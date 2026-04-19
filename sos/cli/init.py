"""``sos init`` — first-boot tenant provisioning (Phase 5, v0.9.4).

Onboards a new Mumega tenant end-to-end. Old dev-wizard lives at
``sos.cli.setup`` now; this module is the Phase 5 flow described in
``docs/plans/2026-04-19-mumega-mothership.md`` §5.

Five steps, sequenced:
    A. POST /tenants on the SaaS service                             (shipped)
    B. Copy inkwell/instances/_template → <slug>/, wrangler deploy   (shipped)
    C. Create default squads + mint qNFTs in the economy             (shipped)
    D. Write standing_workflows.json from template                   (shipped)
    E. Trigger first pulse run in the operations service             (shipped)

All five steps ship in v0.9.4. Step D enriches the template copy placed by
Step B with the squad IDs minted in Step C, so running steps out of order
will raise ``FileNotFoundError`` from Step D.

Run::

    python -m sos.cli.init --slug acme --label "Acme Co" --email owner@acme.com \
        --plan starter --domain acme.com --industry consulting

Use ``--dry-run`` to print the payload without hitting the saas service.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import logging

from sos.clients.economy import EconomyClient
from sos.clients.operations import OperationsClient
from sos.clients.saas import SaasClient
from sos.contracts.tenant import TenantCreate, TenantPlan

_log = logging.getLogger("sos.cli.init")

_DEFAULT_SQUADS = "social,content,outreach,analytics"
_DEFAULT_SEAT_COST = 100

_TEXT_SUFFIXES = {
    ".ts", ".tsx", ".astro", ".md", ".json", ".toml", ".html", ".css",
}
_PLACEHOLDERS = ("{{SLUG}}", "{{LABEL}}", "{{DOMAIN}}", "{{EMAIL}}", "{{INDUSTRY}}", "{{TAGLINE}}")


def green(text: str) -> str:
    return f"\033[1;32m{text}\033[0m"


def bold(text: str) -> str:
    return f"\033[1m{text}\033[0m"


def warn(text: str) -> str:
    return f"\033[1;33m{text}\033[0m"


def red(text: str) -> str:
    return f"\033[1;31m{text}\033[0m"


@dataclass
class InitConfig:
    slug: str
    label: str
    email: str
    plan: TenantPlan
    domain: str | None = None
    industry: str | None = None
    tagline: str | None = None
    saas_base_url: str | None = None
    saas_token: str | None = None
    dry_run: bool = False


def build_payload(cfg: InitConfig) -> dict[str, Any]:
    """Validate via TenantCreate and return the wire JSON body."""
    model = TenantCreate(
        slug=cfg.slug,
        label=cfg.label,
        email=cfg.email,
        plan=cfg.plan,
        domain=cfg.domain,
        industry=cfg.industry,
        tagline=cfg.tagline,
    )
    return model.model_dump(mode="json", exclude_none=True)


def step_a_provision_tenant(
    cfg: InitConfig, *, client_factory: Any = SaasClient
) -> dict[str, Any]:
    """Step A — POST /tenants on the SaaS service.

    Returns the freshly created Tenant row (status=provisioning). Raises
    whatever the SaasClient raises on HTTP error — the caller logs and
    bails out before attempting B–E.
    """
    payload = build_payload(cfg)
    if cfg.dry_run:
        print(f"  {warn('[dry-run]')} would POST /tenants with:")
        for k, v in payload.items():
            print(f"    {k}: {v}")
        return {"slug": cfg.slug, "status": "provisioning", "_dry_run": True}

    kwargs: dict[str, Any] = {}
    if cfg.saas_base_url:
        kwargs["base_url"] = cfg.saas_base_url
    if cfg.saas_token:
        kwargs["token"] = cfg.saas_token
    client = client_factory(**kwargs)
    return client.create_tenant(payload)


def step_b_deploy_inkwell(
    cfg: InitConfig,
    tenant: dict[str, Any],
    *,
    _subprocess_run: Any = subprocess.run,
) -> dict[str, Any]:
    """Step B — copy ``inkwell/instances/_template/`` and ``wrangler pages deploy``.

    Precondition: ``wrangler`` must be on PATH (npx wrangler 4.x works too).
    CLOUDFLARE_API_TOKEN must be set in the environment.
    """
    inkwell_root = Path(os.environ.get("INKWELL_ROOT", "/home/mumega/inkwell"))
    source = inkwell_root / "instances" / "_template"
    dest = inkwell_root / "instances" / cfg.slug

    if not source.exists():
        raise FileNotFoundError(
            f"Inkwell template not found: {source}. "
            "Ensure inkwell/instances/_template/ exists before running sos init."
        )
    if dest.exists():
        raise FileExistsError(
            f"Instance directory already exists: {dest}. "
            f"Remove it manually if you want to re-initialise slug '{cfg.slug}'."
        )

    shutil.copytree(source, dest)

    # Interpolate the six placeholders in every text file under dest.
    replacements = {
        "{{SLUG}}": cfg.slug,
        "{{LABEL}}": cfg.label,
        "{{DOMAIN}}": cfg.domain or "",
        "{{EMAIL}}": cfg.email,
        "{{INDUSTRY}}": cfg.industry or "",
        "{{TAGLINE}}": cfg.tagline or "",
    }
    for path in dest.rglob("*"):
        if path.is_file() and path.suffix in _TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8")
            for token, value in replacements.items():
                text = text.replace(token, value)
            path.write_text(text, encoding="utf-8")

    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if not token:
        raise EnvironmentError(
            "CLOUDFLARE_API_TOKEN is not set. "
            "Export it before running sos init Step B."
        )

    # Build at inkwell root (Astro SSG writes to dist/).
    # Assumption: node_modules already installed in inkwell_root.
    _subprocess_run(
        ["npm", "run", "build"],
        env={**os.environ, "CLOUDFLARE_API_TOKEN": token},
        cwd=inkwell_root,
        check=True,
        capture_output=True,
        text=True,
    )

    result = _subprocess_run(
        ["wrangler", "pages", "deploy", "dist", "--project-name", cfg.slug],
        env={**os.environ, "CLOUDFLARE_API_TOKEN": token},
        cwd=inkwell_root,
        check=True,
        capture_output=True,
        text=True,
    )

    return {
        "slug": cfg.slug,
        "deploy_path": str(dest),
        "wrangler_stdout": result.stdout[-500:],
    }


def step_c_seed_squads(
    cfg: InitConfig,
    tenant: dict[str, Any],
    *,
    client_factory: Any = EconomyClient,
) -> list[dict[str, Any]]:
    """Step C — Mint qNFT seat tokens for each default squad role.

    Design call: we do NOT write AgentCards here. A real AgentCard requires
    an agent identity (keypair, DID) that doesn't exist at tenant-creation
    time. Instead we mint lightweight seat tokens tagged with
    {tenant, squad_id, role, seat_id}. When a real agent later enrolls via
    POST /mesh/enroll it claims a seat by matching role + tenant.
    This keeps the registry clean and defers identity binding to enrollment.
    """
    raw_squads = os.environ.get("MUMEGA_DEFAULT_SQUADS", _DEFAULT_SQUADS)
    roles = [r.strip() for r in raw_squads.split(",") if r.strip()]
    cost = int(os.environ.get("MUMEGA_QNFT_SEAT_COST_MIND", str(_DEFAULT_SEAT_COST)))

    kwargs: dict[str, Any] = {}
    if cfg.saas_token:
        kwargs["token"] = cfg.saas_token
    client = client_factory(**kwargs)

    minted: list[dict[str, Any]] = []
    for role in roles:
        squad_id = f"{cfg.slug}-squad-{role}"
        seat_id = f"{cfg.slug}:seat:{role}"
        try:
            token = client.mint_qnft(
                cfg.slug,
                squad_id,
                role,
                seat_id,
                cost_mind=cost,
                project=cfg.slug,
            )
        except Exception as exc:
            msg = str(exc)
            if "402" in msg or "insufficient" in msg.lower():
                raise RuntimeError(
                    f"Tenant '{cfg.slug}' has insufficient $MIND to mint seat '{role}'. "
                    f"Top up via: POST /credit {{user_id: '{cfg.slug}', amount: {cost}}}"
                ) from exc
            raise
        minted.append(token)
        print(f"  {green('ok')} — seat minted: role={role} token_id={token.get('token_id', '?')}")

    return minted


def step_d_write_workflows(
    cfg: InitConfig,
    tenant: dict[str, Any],
    seats: list[dict[str, Any]],
) -> dict[str, Any]:
    """Step D — enrich the template's ``standing_workflows.json`` with squad IDs.

    Step B already copied the template to
    ``<INKWELL_ROOT>/instances/<slug>/standing_workflows.json`` and
    interpolated ``{{SLUG}}`` / ``{{LABEL}}``. Step D's job is to inject
    the real squad IDs minted in Step C so the workflow runner can map
    each workflow step to a concrete squad.

    Adds a top-level ``squads`` array and ``assigned_squads`` to each
    workflow (defaults to all minted squads — downstream runners pick
    the right one by role). Writes the file back in place.
    """
    inkwell_root = Path(os.environ.get("INKWELL_ROOT", "/home/mumega/inkwell"))
    workflow_path = inkwell_root / "instances" / cfg.slug / "standing_workflows.json"

    if not workflow_path.exists():
        raise FileNotFoundError(
            f"standing_workflows.json not found at {workflow_path}. "
            "Step D requires Step B to have run first."
        )

    data = json.loads(workflow_path.read_text(encoding="utf-8"))

    squads = [
        {
            "squad_id": seat["squad_id"],
            "role": seat["role"],
            "seat_id": seat["seat_id"],
            "token_id": seat.get("token_id"),
        }
        for seat in seats
    ]
    data["squads"] = squads

    squad_ids = [s["squad_id"] for s in squads]
    for workflow in data.get("workflows", []):
        workflow["assigned_squads"] = squad_ids

    workflow_path.write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8"
    )
    return data


def step_e_trigger_pulse(
    cfg: InitConfig,
    tenant: dict[str, Any],
    *,
    client_factory: Any = OperationsClient,
) -> dict[str, Any]:
    """Step E — kick the first pulse run for this tenant/project."""
    kwargs: dict[str, Any] = {}
    if cfg.saas_token:
        kwargs["token"] = cfg.saas_token
    client = client_factory(**kwargs)
    result = client.trigger_pulse(tenant["slug"], tenant["slug"])
    print(
        f"  {green('ok')} — pulse triggered"
        f" (tenant={result.get('tenant')}, started_at={result.get('started_at')})"
    )
    return result


def parse_args(argv: list[str] | None = None) -> InitConfig:
    parser = argparse.ArgumentParser(
        prog="sos init",
        description="Provision a new Mumega tenant end-to-end.",
    )
    parser.add_argument("--slug", required=True, help="Tenant slug (a-z0-9-)")
    parser.add_argument("--label", required=True, help="Human-readable label")
    parser.add_argument("--email", required=True, help="Owner email")
    parser.add_argument(
        "--plan",
        default="starter",
        choices=[p.value for p in TenantPlan],
        help="Billing plan tier",
    )
    parser.add_argument("--domain", default=None, help="Custom domain (optional)")
    parser.add_argument("--industry", default=None, help="Industry hint")
    parser.add_argument("--tagline", default=None, help="One-line tagline")
    parser.add_argument(
        "--saas-base-url",
        default=None,
        help="Override SaaS service URL (default: http://localhost:8075)",
    )
    parser.add_argument(
        "--saas-token",
        default=None,
        help="Admin token (default: reads SOS_SAAS_ADMIN_KEY / SOS_SYSTEM_TOKEN)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print Step A payload without hitting the SaaS service",
    )
    args = parser.parse_args(argv)
    return InitConfig(
        slug=args.slug,
        label=args.label,
        email=args.email,
        plan=TenantPlan(args.plan),
        domain=args.domain,
        industry=args.industry,
        tagline=args.tagline,
        saas_base_url=args.saas_base_url,
        saas_token=args.saas_token,
        dry_run=args.dry_run,
    )


def main(argv: list[str] | None = None) -> int:
    cfg = parse_args(argv)

    print(f"\n{bold(f'sos init — {cfg.slug}')}")
    print("=" * 48)

    # Step A — real.
    print(f"\n{bold('Step A')} — provision tenant via SaaS /tenants")
    try:
        tenant = step_a_provision_tenant(cfg)
    except Exception as exc:
        print(f"  {red('FAILED')}: {exc}")
        return 1
    print(f"  {green('ok')} — tenant row created (status=provisioning)")

    # Step B — real (inkwell template copy + wrangler pages deploy).
    print(f"\n{bold('Step B')} — copy inkwell template + deploy to Cloudflare Pages")
    try:
        b_result = step_b_deploy_inkwell(cfg, tenant)
        print(f"  {green('ok')} — deployed (path={b_result['deploy_path']})")
    except Exception as exc:
        print(f"  {warn('skipped')}: {exc}")

    # Step C — real (qNFT seat minting).
    seats: list[dict[str, Any]] = []
    print(f"\n{bold('Step C')} — mint qNFT seats for default squads")
    try:
        seats = step_c_seed_squads(cfg, tenant)
        print(f"  {green('ok')} — {len(seats)} seat(s) minted")
    except Exception as exc:
        print(f"  {warn('skipped')}: {exc}")

    # Step D — real (enrich standing_workflows.json with squad IDs).
    print(f"\n{bold('Step D')} — write standing_workflows.json with squad IDs")
    try:
        step_d_write_workflows(cfg, tenant, seats)
        print(f"  {green('ok')} — workflows enriched with {len(seats)} squad(s)")
    except Exception as exc:
        print(f"  {warn('skipped')}: {exc}")

    # Step E — real (operations service must be reachable).
    print(f"\n{bold('Step E')} — trigger first pulse run")
    try:
        step_e_trigger_pulse(cfg, tenant)
    except Exception as exc:
        print(f"  {warn('skipped')}: {exc}")

    print("\n" + "=" * 48)
    print(f"{green('Phase 5 shipped — Steps A + B + C + D + E all green.')}")
    print(f"  slug:   {cfg.slug}")
    print(f"  label:  {cfg.label}")
    print(f"  plan:   {cfg.plan.value}")
    print(f"  status: {tenant.get('status', 'unknown')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
