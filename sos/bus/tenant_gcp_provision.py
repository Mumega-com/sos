"""
S144 Track F — GCP Cloud Run per-tenant provisioning.

Called as a background thread from provision_tenant() after bus token is minted.
Each tenant gets:
  - A dedicated GCP service account (scoped IAM)
  - 3 secrets in Secret Manager (bus token, OpenRouter key, IM token)
  - A Cloud Run service from the shared hermes-base image
  - A Pub/Sub topic + push subscription → Cloud Run /webhook/bus
  - cloud_run_url written back to D1 via inkwell-api PATCH

Env vars required:
  GCP_PROJECT            — defaults to "mumega-com"
  GCP_REGION             — defaults to "us-central1"
  HERMES_IMAGE           — defaults to us-central1-docker.pkg.dev/mumega-com/gaf-agent/hermes-base:latest
  INKWELL_INTERNAL_URL   — e.g. https://inkwell-api.mumega.workers.dev
  INTERNAL_API_SECRET    — shared secret for inkwell-api internal endpoints
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
import urllib.request
import urllib.error
import json
from typing import Any

log = logging.getLogger("tenant-gcp-provision")

GCP_PROJECT    = os.environ.get("GCP_PROJECT", "mumega-com")
GCP_REGION     = os.environ.get("GCP_REGION", "us-central1")
HERMES_IMAGE   = os.environ.get(
    "HERMES_IMAGE",
    f"{os.environ.get('GCP_REGION', 'us-central1')}-docker.pkg.dev/mumega-com/gaf-agent/hermes-base:latest",
)
PUBSUB_PUSH_SA = f"pubsub-invoker@{GCP_PROJECT}.iam.gserviceaccount.com"


# ── gcloud helper ─────────────────────────────────────────────────────────────

def _run(cmd: list[str]) -> str:
    log.info("gcloud: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _run_soft(cmd: list[str], *, ignore: str) -> str:
    """Run cmd; swallow CalledProcessError if stderr contains `ignore` substring."""
    try:
        return _run(cmd)
    except subprocess.CalledProcessError as e:
        if ignore in (e.stderr or ""):
            return ""
        raise


# ── Secret Manager ────────────────────────────────────────────────────────────

def _upsert_secret(name: str, value: str, sa_email: str) -> None:
    """Create (or update) a Secret Manager secret and scope it to one SA."""
    _run_soft(
        ["gcloud", "secrets", "create", name,
         "--replication-policy=automatic", f"--project={GCP_PROJECT}"],
        ignore="already exists",
    )
    # Add a new version
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
        f.write(value)
        tmp = f.name
    try:
        _run(["gcloud", "secrets", "versions", "add", name,
              f"--data-file={tmp}", f"--project={GCP_PROJECT}"])
    finally:
        os.unlink(tmp)
    # Bind access to this tenant's SA only
    _run(["gcloud", "secrets", "add-iam-policy-binding", name,
          f"--member=serviceAccount:{sa_email}",
          "--role=roles/secretmanager.secretAccessor",
          f"--project={GCP_PROJECT}"])


# ── Bus alert on failure ──────────────────────────────────────────────────────

def _alert_bus(tenant_id: str, slug: str, error: Exception) -> None:
    """P0-3: Emit a bus alert when GCP provisioning fails so it is not silent."""
    bus_url = os.environ.get("SOS_BUS_INTERNAL_URL", "http://localhost:6380")
    bridge_token = os.environ.get("SOS_BRIDGE_TOKEN", "")
    if not bridge_token:
        log.warning("SOS_BRIDGE_TOKEN not set — cannot emit GCP failure alert to bus")
        return
    payload = json.dumps({
        "to": "kasra",
        "text": (
            f"[GCP-PROVISION-FAIL] tenant={slug} id={tenant_id} "
            f"error={type(error).__name__}: {error}"
        ),
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{bus_url}/api/messages/send",
        data=payload,
        headers={"Authorization": f"Bearer {bridge_token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception as alert_err:
        log.error("failed to emit bus alert: %s", alert_err)


# ── D1 write-back ─────────────────────────────────────────────────────────────

def _writeback_d1(tenant_id: str, gcp: dict[str, str], slug: str = "") -> None:
    """PATCH cloud_run_url + gcp fields back to inkwell-api → D1."""
    base = os.environ.get("INKWELL_INTERNAL_URL", "").rstrip("/")
    secret = os.environ.get("INTERNAL_API_SECRET", "")
    if not base or not secret:
        log.warning("INKWELL_INTERNAL_URL or INTERNAL_API_SECRET not set — skipping D1 write-back")
        return

    payload = json.dumps({
        "cloud_run_url": gcp["cloud_run_url"],
        "gcp_service_account": gcp["service_account"],
        "gcp_pubsub_topic": gcp["pubsub_topic"],
        "provisioned_gcp": True,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{base}/api/tenants/{tenant_id}/gcp",  # P0-2 FIX: correct route
        data=payload,
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
        },
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info("D1 write-back ok: tenant=%s status=%s", tenant_id, resp.status)
    except urllib.error.HTTPError as e:
        log.error("D1 write-back failed: tenant=%s status=%s", tenant_id, e.code)
        # P1-3 FIX: alert bus on D1 write-back failure too, not just provisioning failure
        _alert_bus(tenant_id, slug, Exception(f"D1 write-back HTTP {e.code}"))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log.error("D1 write-back unreachable: tenant=%s error=%s", tenant_id, e)
        _alert_bus(tenant_id, slug, e)


# ── Core provisioning ─────────────────────────────────────────────────────────

def provision_tenant_gcp(
    tenant_id: str,
    slug: str,
    bus_token: str,
    industry: str = "general",
    tier: str = "subscribe",
    openrouter_key: str = "",
    im_token: str = "",
) -> dict[str, Any]:
    """
    Full GCP provisioning for one tenant. Idempotent.
    Returns { cloud_run_url, service_account, pubsub_topic, pubsub_subscription }
    """
    # Names — keep within GCP limits
    sa_name    = f"sa-t-{slug[:20]}"
    sa_email   = f"{sa_name}@{GCP_PROJECT}.iam.gserviceaccount.com"
    svc_name   = f"hermes-{slug[:40]}"
    topic_name = f"hermes-{slug[:40]}-intake"
    sub_name   = f"hermes-{slug[:40]}-push"

    # ── 1. Service account ────────────────────────────────────────────────────
    _run_soft(
        ["gcloud", "iam", "service-accounts", "create", sa_name,
         f"--display-name=Hermes {slug}", f"--project={GCP_PROJECT}"],
        ignore="already exists",
    )
    # P0-1 / P1-2 FIX: tenant SA gets NO project-level IAM roles.
    # - roles/secretmanager.secretAccessor: bound per-secret in _upsert_secret()
    # - roles/run.invoker: Pub/Sub push SA (not tenant SA) needs this; granted below
    # - roles/aiplatform.user: not needed — model calls go via OpenRouter, not Vertex AI
    # - roles/datastore.user: not needed — Firestore memory not wired yet
    # Result: tenant SA has zero project-level permissions, cannot access any other tenant's resources.
    pass  # no project-level bindings

    # ── 2. Secrets ────────────────────────────────────────────────────────────
    _upsert_secret(f"tenant-{slug}-bus-token",  bus_token,      sa_email)
    _upsert_secret(f"tenant-{slug}-openrouter", openrouter_key, sa_email)
    _upsert_secret(f"tenant-{slug}-im-token",   im_token,       sa_email)

    # ── 3. Cloud Run service ──────────────────────────────────────────────────
    _run([
        "gcloud", "run", "deploy", svc_name,
        f"--image={HERMES_IMAGE}",
        f"--region={GCP_REGION}",
        f"--project={GCP_PROJECT}",
        f"--service-account={sa_email}",
        "--platform=managed",
        "--no-allow-unauthenticated",
        "--min-instances=0",
        "--max-instances=3",
        f"--set-secrets="
        f"BUS_TOKEN=tenant-{slug}-bus-token:latest,"
        f"OPENROUTER_KEY=tenant-{slug}-openrouter:latest,"
        f"IM_TOKEN=tenant-{slug}-im-token:latest",
        f"--set-env-vars="
        f"TENANT_SLUG={slug},"
        f"TENANT_ID={tenant_id},"
        f"INDUSTRY={industry},"
        f"TENANT_TIER={tier}",
    ])

    cloud_run_url = _run([
        "gcloud", "run", "services", "describe", svc_name,
        f"--region={GCP_REGION}", f"--project={GCP_PROJECT}",
        "--format=value(status.url)",
    ])
    log.info("Cloud Run deployed: tenant=%s url=%s", slug, cloud_run_url)

    # Grant Pub/Sub SA permission to invoke this service
    _run(["gcloud", "run", "services", "add-iam-policy-binding", svc_name,
          f"--region={GCP_REGION}", f"--project={GCP_PROJECT}",
          f"--member=serviceAccount:{PUBSUB_PUSH_SA}",
          "--role=roles/run.invoker"])

    # ── 4. Pub/Sub topic + push subscription ─────────────────────────────────
    _run_soft(
        ["gcloud", "pubsub", "topics", "create", topic_name, f"--project={GCP_PROJECT}"],
        ignore="already exists",
    )
    _run_soft(
        ["gcloud", "pubsub", "subscriptions", "create", sub_name,
         f"--topic={topic_name}",
         f"--push-endpoint={cloud_run_url}/webhook/bus",
         f"--push-auth-service-account={PUBSUB_PUSH_SA}",
         "--ack-deadline=60",
         f"--project={GCP_PROJECT}"],
        ignore="already exists",
    )

    return {
        "cloud_run_url": cloud_run_url,
        "service_account": sa_email,
        "pubsub_topic": topic_name,
        "pubsub_subscription": sub_name,
    }


# ── Background thread entry point ─────────────────────────────────────────────

def provision_tenant_gcp_background(
    tenant_id: str,
    slug: str,
    bus_token: str,
    industry: str = "general",
    tier: str = "subscribe",
    openrouter_key: str = "",
    im_token: str = "",
) -> None:
    """
    Run GCP provisioning in a background thread.
    Called from provision_tenant() — does NOT block the HTTP response.
    Writes results back to D1 via inkwell-api PATCH when done.
    """
    def _run_in_thread() -> None:
        try:
            gcp = provision_tenant_gcp(
                tenant_id=tenant_id,
                slug=slug,
                bus_token=bus_token,
                industry=industry,
                tier=tier,
                openrouter_key=openrouter_key,
                im_token=im_token,
            )
            _writeback_d1(tenant_id, gcp, slug)
            log.info("GCP provisioning complete: tenant=%s url=%s", slug, gcp["cloud_run_url"])
        except Exception as e:
            # P0-3 FIX: emit bus alert so the failure is visible, not just logged.
            log.error("GCP provisioning failed: tenant=%s error=%s", slug, e)
            _alert_bus(tenant_id, slug, e)

    t = threading.Thread(target=_run_in_thread, daemon=True, name=f"gcp-provision-{slug}")
    t.start()
    log.info("GCP provisioning started in background: tenant=%s thread=%s", slug, t.name)
