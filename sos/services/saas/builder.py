"""Build orchestrator — renders tenant content and uploads to Cloudflare KV.

Flow:
1. Receive build request (tenant_slug, trigger: content_changed | config_changed | manual)
2. Fetch tenant config from TenantRegistry
3. Create temp build directory with tenant-specific content
4. Run Astro build with tenant config injected
5. Upload dist/ output to Cloudflare KV under tenant prefix
6. Clean up temp directory

For MVP, this is a synchronous process. At scale, it becomes a queue worker.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from sos.services.saas.models import Tenant
from sos.services.saas.registry import TenantRegistry

log = logging.getLogger("sos.saas.builder")

INKWELL_SOURCE = Path.home() / "inkwell"
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "")
CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "")

# Directories to skip when copying the Inkwell source tree
_COPY_EXCLUDE = {"node_modules", "dist", ".git", ".astro", "graphify-out", ".cache"}


class BuildOrchestrator:
    """Manages per-tenant Astro builds and KV uploads."""

    def __init__(self, registry: Optional[TenantRegistry] = None) -> None:
        self.registry = registry or TenantRegistry()
        self._build_queue: list[dict] = []

    async def build_tenant(self, tenant_slug: str, trigger: str = "manual") -> dict:
        """Build and deploy content for a single tenant.

        Args:
            tenant_slug: Unique tenant identifier.
            trigger: One of "content_changed", "config_changed", "manual".

        Returns:
            Result dict with success flag, file count, elapsed time, or error.
        """
        tenant = self.registry.get(tenant_slug)
        if not tenant:
            return {"error": f"Tenant {tenant_slug} not found", "success": False}

        log.info("Starting build for tenant %s (trigger: %s)", tenant_slug, trigger)
        start = datetime.now(timezone.utc)

        with tempfile.TemporaryDirectory(prefix=f"inkwell-{tenant_slug}-") as tmpdir:
            build_dir = Path(tmpdir)

            try:
                # 1. Copy Inkwell source (excluding heavy dirs)
                self._copy_source(build_dir)

                # 2. Write tenant-specific config
                self._write_tenant_config(build_dir, tenant)

                # 3. Run Astro build
                build_ok, build_output = await self._run_build(build_dir)
                if not build_ok:
                    log.error("Build failed for %s: %s", tenant_slug, build_output[:500])
                    return {
                        "error": "build_failed",
                        "output": build_output[:500],
                        "success": False,
                    }

                # 4. Upload dist/ to KV
                dist_dir = build_dir / "dist"
                if not dist_dir.exists():
                    return {"error": "no dist directory after build", "success": False}

                uploaded = await self._upload_to_kv(tenant_slug, dist_dir)

                elapsed = (datetime.now(timezone.utc) - start).total_seconds()
                log.info(
                    "Build complete for %s: %d files in %.1fs",
                    tenant_slug,
                    uploaded,
                    elapsed,
                )

                return {
                    "success": True,
                    "tenant": tenant_slug,
                    "files_uploaded": uploaded,
                    "elapsed_seconds": round(elapsed, 1),
                    "trigger": trigger,
                }

            except Exception as exc:
                log.error("Build crashed for %s: %s", tenant_slug, exc, exc_info=True)
                return {"error": str(exc), "success": False}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _copy_source(self, dest: Path) -> None:
        """Copy Inkwell source to build directory, excluding heavy dirs."""
        for item in INKWELL_SOURCE.iterdir():
            if item.name in _COPY_EXCLUDE:
                continue
            target = dest / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)

        # Symlink node_modules from source to avoid re-installing (~30s saved)
        nm_source = INKWELL_SOURCE / "node_modules"
        if nm_source.exists():
            (dest / "node_modules").symlink_to(nm_source)

    def _write_tenant_config(self, build_dir: Path, tenant: Tenant) -> None:
        """Write tenant-specific inkwell.config.ts into the build directory."""
        cfg = tenant.inkwell_config or {}

        name = cfg.get("name", tenant.label)
        domain = cfg.get("domain", tenant.subdomain)
        tagline = cfg.get("tagline", f"{name} — powered by Mumega")

        theme_colors = cfg.get("theme", {}).get("colors", {})
        primary = theme_colors.get("primary", "#D4A017")

        config_ts = f"""\
export const config = {{
  name: '{_esc(name)}',
  domain: '{_esc(domain)}',
  tagline: '{_esc(tagline)}',

  theme: {{
    colors: {{
      primary: '{primary}',
      secondary: '#06B6D4',
      accent: '#10B981',
      danger: '#EF4444',
      bg:      {{ dark: '#0A0A10', light: '#FAFBFC' }},
      surface: {{ dark: '#151519', light: '#FFFFFF' }},
      text:    {{ dark: '#EDEDF0', light: '#1A1D23' }},
      muted:   {{ dark: 'rgba(255,255,255,0.55)', light: 'rgba(0,0,0,0.55)' }},
      dim:     {{ dark: 'rgba(255,255,255,0.35)', light: 'rgba(0,0,0,0.35)' }},
      border:  {{ dark: 'rgba(255,255,255,0.10)', light: 'rgba(0,0,0,0.10)' }},
    }},
    fonts: {{
      display: "'JetBrains Mono', monospace",
      body: "system-ui, -apple-system, sans-serif",
      mono: "'JetBrains Mono', monospace",
    }},
    radius: '6px',
    contentWidth: '680px',
    pageWidth: '1200px',
    darkFirst: true,
  }},

  i18n: {{
    defaultLang: 'en' as const,
    languages: ['en'] as const,
    rtl: ['fa', 'ar'] as const,
    fallback: 'en' as const,
  }},

  features: {{
    reactions: true,
    newsletter: true,
    readingProgress: true,
    toc: true,
    shareButtons: true,
    commandPalette: true,
    knowledgeGraph: false,
    rss: true,
    search: true,
    darkModeToggle: true,
    chat: false,
  }},

  analytics: {{ googleAnalytics: '', clarity: '', hotjar: '', tagManager: '', plausible: '' }},

  seo: {{
    organization: {{
      name: '{_esc(name)}',
      url: 'https://{_esc(domain)}',
      logo: '/logo.svg',
      knowsAbout: [],
    }},
    defaultAuthor: {{ name: '{_esc(name)}', url: 'https://{_esc(domain)}' }},
  }},

  workerUrl: '',

  publish: {{
    inbox: true,
    api: true,
    mcp: true,
  }},
}} as const

export type InkwellConfig = typeof config
"""
        (build_dir / "inkwell.config.ts").write_text(config_ts)

    async def _run_build(self, build_dir: Path) -> tuple[bool, str]:
        """Run ``npx astro build`` in the temp directory."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "npx",
                "astro",
                "build",
                cwd=str(build_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, "NODE_ENV": "production"},
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
            output = stdout.decode("utf-8", errors="replace")
            return proc.returncode == 0, output
        except asyncio.TimeoutError:
            return False, "Build timed out after 120s"
        except Exception as exc:
            return False, str(exc)

    async def _upload_to_kv(self, tenant_slug: str, dist_dir: Path) -> int:
        """Upload built files to Cloudflare KV under tenant prefix.

        Each file is stored as ``{tenant_slug}:page:{relative_path}``.
        Returns the number of files successfully uploaded.
        """
        if not CF_API_TOKEN or not CF_ACCOUNT_ID:
            log.warning(
                "CF credentials not set — skipping KV upload, files at %s", dist_dir
            )
            return 0

        kv_namespace_id = os.environ.get("CF_KV_CONTENT_ID", "")
        if not kv_namespace_id:
            log.warning("CF_KV_CONTENT_ID not set — skipping KV upload")
            return 0

        base_url = (
            f"https://api.cloudflare.com/client/v4/accounts/"
            f"{CF_ACCOUNT_ID}/storage/kv/namespaces/{kv_namespace_id}/values"
        )
        headers = {"Authorization": f"Bearer {CF_API_TOKEN}"}

        uploaded = 0
        async with httpx.AsyncClient(timeout=30) as client:
            for file_path in dist_dir.rglob("*"):
                if not file_path.is_file():
                    continue

                relative = file_path.relative_to(dist_dir)
                kv_key = f"{tenant_slug}:page:{relative}"
                content = file_path.read_bytes()

                try:
                    resp = await client.put(
                        f"{base_url}/{kv_key}",
                        headers=headers,
                        content=content,
                    )
                    if resp.status_code == 200:
                        uploaded += 1
                    else:
                        log.warning(
                            "KV upload failed for %s: HTTP %s",
                            kv_key,
                            resp.status_code,
                        )
                except Exception as exc:
                    log.warning("KV upload error for %s: %s", kv_key, exc)

        return uploaded


# ------------------------------------------------------------------
# Utility
# ------------------------------------------------------------------


def _esc(value: str) -> str:
    """Escape single quotes and newlines for TypeScript string literals."""
    return value.replace("'", "\\'").replace("\n", " ")


# ------------------------------------------------------------------
# Convenience entry point
# ------------------------------------------------------------------


async def build_tenant(slug: str, trigger: str = "manual") -> dict:
    """Build a tenant's site. Returns result dict."""
    orchestrator = BuildOrchestrator()
    return await orchestrator.build_tenant(slug, trigger)
