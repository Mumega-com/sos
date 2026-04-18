"""Analytics ingestion — pulls GA4, GSC, Clarity data into Mirror.

Runs weekly per tenant. Stores results in Mirror for the decision agent.

Usage:
    python -m sos.services.analytics --tenant viamar
    python -m sos.services.analytics --all
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx

from sos.clients.integrations import AsyncIntegrationsClient

logger = logging.getLogger("sos.analytics.ingest")

FALLBACK_DIR = Path.home() / ".sos" / "analytics"


class AnalyticsIngester:
    """Pulls analytics from GA4, GSC, and Clarity, stores in Mirror."""

    def __init__(
        self,
        tenant_name: str,
        mirror_url: str,
        mirror_token: str,
        ga4_property_id: Optional[str] = None,
        gsc_domain: Optional[str] = None,
        clarity_project_id: Optional[str] = None,
        integrations_client: Optional[AsyncIntegrationsClient] = None,
    ) -> None:
        self.tenant = tenant_name
        self.mirror_url = mirror_url.rstrip("/")
        self.mirror_token = mirror_token
        self.ga4_property_id = ga4_property_id
        self.gsc_domain = gsc_domain
        self.clarity_project_id = clarity_project_id
        self._client = httpx.Client(timeout=30)
        self._integrations = integrations_client or AsyncIntegrationsClient(
            base_url=os.environ.get("SOS_INTEGRATIONS_URL"),
            token=os.environ.get("SOS_INTEGRATIONS_TOKEN")
            or os.environ.get("SOS_SYSTEM_TOKEN"),
        )

    # ------------------------------------------------------------------
    # GA4
    # ------------------------------------------------------------------

    async def ingest_ga4(self, days: int = 30) -> str:
        """Pull top pages, bounce rate, session duration from GA4.

        Endpoint: POST https://analyticsdata.googleapis.com/v1beta/properties/{id}:runReport
        Auth: Bearer access_token fetched via the Integrations HTTP service
        Falls back to empty string (no mock) if credentials not configured.
        """
        if not self.ga4_property_id:
            logger.info("GA4 property not configured for %s, skipping", self.tenant)
            return ""

        creds = await self._integrations.get_credentials(
            self.tenant, "google_analytics"
        )
        if not creds:
            logger.warning("No GA4 credentials for %s — skipping", self.tenant)
            return ""

        access_token = creds["access_token"]
        start_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

        url = (
            f"https://analyticsdata.googleapis.com/v1beta/properties/"
            f"{self.ga4_property_id}:runReport"
        )
        body = {
            "dimensions": [{"name": "pagePath"}],
            "metrics": [
                {"name": "sessions"},
                {"name": "bounceRate"},
                {"name": "averageSessionDuration"},
            ],
            "dateRanges": [{"startDate": start_date, "endDate": "today"}],
            "limit": 20,
            "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
        }

        try:
            resp = self._client.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error("GA4 API error for %s: %s %s", self.tenant, exc.response.status_code, exc.response.text[:200])
            return ""
        except httpx.RequestError as exc:
            logger.error("GA4 request failed for %s: %s", self.tenant, exc)
            return ""

        rows = data.get("rows", [])
        if not rows:
            logger.info("GA4 returned no rows for %s", self.tenant)
            return ""

        pages = []
        total_sessions = 0
        total_bounce = 0.0

        for row in rows:
            dims = row.get("dimensionValues", [])
            metrics = row.get("metricValues", [])
            page = dims[0]["value"] if dims else "unknown"
            sessions = int(metrics[0]["value"]) if len(metrics) > 0 else 0
            bounce = float(metrics[1]["value"]) if len(metrics) > 1 else 0.0
            pages.append({"page": page, "sessions": sessions, "bounce_rate": round(bounce * 100, 1)})
            total_sessions += sessions
            total_bounce += bounce

        avg_bounce = (total_bounce / len(rows)) * 100 if rows else 0.0
        top_pages = [p["page"] for p in pages[:5]]

        report = (
            f"GA4 Report for {self.tenant}: "
            f"Top pages: {', '.join(top_pages)}. "
            f"Total sessions (top {len(rows)}): {total_sessions}. "
            f"Avg bounce rate: {avg_bounce:.1f}%."
        )

        self.store_in_mirror(
            content=report,
            context=f"ga4-{self.tenant}-{datetime.utcnow().strftime('%Y-%m-%d')}",
        )
        logger.info("GA4 ingested for %s: %d pages, %d sessions", self.tenant, len(rows), total_sessions)
        return report

    # ------------------------------------------------------------------
    # Google Search Console
    # ------------------------------------------------------------------

    async def ingest_gsc(self, days: int = 30) -> str:
        """Pull top queries, clicks, impressions, CTR, position from GSC.

        Endpoint: POST https://www.googleapis.com/webmasters/v3/sites/{domain}/searchAnalytics/query
        Auth: Bearer access_token fetched via the Integrations HTTP service
        Falls back to empty string (no mock) if credentials not configured.
        """
        if not self.gsc_domain:
            logger.info("GSC domain not configured for %s, skipping", self.tenant)
            return ""

        creds = await self._integrations.get_credentials(
            self.tenant, "google_search_console"
        )
        if not creds:
            logger.warning("No GSC credentials for %s — skipping", self.tenant)
            return ""

        access_token = creds["access_token"]
        end_date = datetime.utcnow().strftime("%Y-%m-%d")
        start_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

        # GSC requires the site URL to be URL-encoded in the path
        import urllib.parse
        encoded_domain = urllib.parse.quote(self.gsc_domain, safe="")
        url = f"https://www.googleapis.com/webmasters/v3/sites/{encoded_domain}/searchAnalytics/query"

        body = {
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": ["query"],
            "rowLimit": 20,
            "startRow": 0,
        }

        try:
            resp = self._client.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error("GSC API error for %s: %s %s", self.tenant, exc.response.status_code, exc.response.text[:200])
            return ""
        except httpx.RequestError as exc:
            logger.error("GSC request failed for %s: %s", self.tenant, exc)
            return ""

        rows = data.get("rows", [])
        if not rows:
            logger.info("GSC returned no rows for %s", self.tenant)
            return ""

        queries = []
        total_clicks = 0
        total_position = 0.0

        for row in rows:
            keys = row.get("keys", [])
            query = keys[0] if keys else "unknown"
            clicks = int(row.get("clicks", 0))
            impressions = int(row.get("impressions", 0))
            ctr = round(float(row.get("ctr", 0)) * 100, 2)
            position = round(float(row.get("position", 0)), 1)
            queries.append({"query": query, "clicks": clicks, "impressions": impressions, "ctr": ctr, "position": position})
            total_clicks += clicks
            total_position += position

        avg_position = total_position / len(rows) if rows else 0.0
        top_queries = [q["query"] for q in queries[:5]]

        report = (
            f"GSC Report for {self.tenant}: "
            f"Top queries: {', '.join(top_queries)}. "
            f"Total clicks (top {len(rows)}): {total_clicks}. "
            f"Avg position: {avg_position:.1f}."
        )

        self.store_in_mirror(
            content=report,
            context=f"gsc-{self.tenant}-{datetime.utcnow().strftime('%Y-%m-%d')}",
        )
        logger.info("GSC ingested for %s: %d queries, %d clicks", self.tenant, len(rows), total_clicks)
        return report

    # ------------------------------------------------------------------
    # Microsoft Clarity
    # ------------------------------------------------------------------

    async def ingest_clarity(self, days: int = 30) -> str:
        """Pull rage clicks, dead clicks, and scroll depth from Microsoft Clarity.

        Endpoint: GET https://www.clarity.ms/api/v1/projects/{id}/metrics
        Auth: Bearer api_key fetched via the Integrations HTTP service
        Docs: https://learn.microsoft.com/en-us/clarity/setup-and-installation/clarity-api
        Falls back to empty string (no mock) if credentials not configured.
        """
        if not self.clarity_project_id:
            logger.info("Clarity not configured for %s, skipping", self.tenant)
            return ""

        creds = await self._integrations.get_credentials(self.tenant, "clarity")
        if not creds:
            logger.warning("No Clarity credentials for %s — skipping", self.tenant)
            return ""

        api_key = creds.get("api_key") or creds.get("access_token", "")
        if not api_key:
            logger.warning("Clarity credentials missing api_key for %s", self.tenant)
            return ""

        end_date = datetime.utcnow().strftime("%Y-%m-%d")
        start_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

        base_url = f"https://www.clarity.ms/api/v1/projects/{self.clarity_project_id}"
        headers = {"Authorization": f"Bearer {api_key}"}
        params = {"startDate": start_date, "endDate": end_date}

        try:
            resp = self._client.get(f"{base_url}/metrics", headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error("Clarity API error for %s: %s %s", self.tenant, exc.response.status_code, exc.response.text[:200])
            return ""
        except httpx.RequestError as exc:
            logger.error("Clarity request failed for %s: %s", self.tenant, exc)
            return ""

        # Clarity metrics response shape:
        # { "rageclicks": [...], "deadclicks": [...], "scrollDepth": {...}, "pages": [...] }
        rage_clicks = data.get("rageclicks", [])
        dead_clicks = data.get("deadclicks", [])
        scroll = data.get("scrollDepth", {})
        pages = data.get("pages", [])

        rage_elements = [r.get("url") or r.get("selector", "unknown") for r in rage_clicks[:3]]
        drop_off_pages = [p.get("url", "unknown") for p in pages if float(p.get("scrollDepth", 1)) < 0.5][:3]

        scroll_pcts = list(scroll.values())
        avg_scroll = sum(scroll_pcts) / len(scroll_pcts) if scroll_pcts else 0.0

        parts = []
        if rage_elements:
            parts.append(f"Rage clicks on: {', '.join(rage_elements)}")
        if dead_clicks:
            parts.append(f"Dead clicks: {len(dead_clicks)} elements")
        if scroll:
            parts.append(f"Avg scroll depth: {avg_scroll:.0f}%")
        if drop_off_pages:
            parts.append(f"Drop-off pages: {', '.join(drop_off_pages)}")

        if not parts:
            logger.info("Clarity returned empty metrics for %s", self.tenant)
            return ""

        report = f"Clarity Report for {self.tenant}: " + ". ".join(parts) + "."

        self.store_in_mirror(
            content=report,
            context=f"clarity-{self.tenant}-{datetime.utcnow().strftime('%Y-%m-%d')}",
        )
        logger.info("Clarity ingested for %s: %d rage clicks, %d dead clicks", self.tenant, len(rage_clicks), len(dead_clicks))
        return report

    # ------------------------------------------------------------------
    # Combined
    # ------------------------------------------------------------------

    async def ingest_all(self, days: int = 30) -> str:
        """Run all three sources and store a combined weekly summary."""
        parts: list[str] = []

        ga4 = await self.ingest_ga4(days=days)
        if ga4:
            parts.append(ga4)

        gsc = await self.ingest_gsc(days=days)
        if gsc:
            parts.append(gsc)

        clarity = await self.ingest_clarity(days=days)
        if clarity:
            parts.append(clarity)

        if not parts:
            logger.warning("No analytics sources configured for %s", self.tenant)
            return ""

        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        combined = f"Weekly Analytics for {self.tenant} ({date_str}): " + " | ".join(parts)

        self.store_in_mirror(
            content=combined,
            context=f"analytics-weekly-{self.tenant}-{date_str}",
        )
        logger.info("Combined analytics stored for %s", self.tenant)
        return combined

    # ------------------------------------------------------------------
    # Mirror storage
    # ------------------------------------------------------------------

    def store_in_mirror(self, content: str, context: str) -> bool:
        """POST engram to Mirror. Falls back to local JSON if Mirror is down."""
        payload = {
            "agent": "analytics",
            "context_id": context,
            "text": content,
            "project": self.tenant,
            "core_concepts": ["analytics", "seo", "ux"],
            "metadata": {
                "source": "analytics-ingest",
                "tenant": self.tenant,
                "ingested_at": datetime.utcnow().isoformat(),
            },
        }

        try:
            resp = self._client.post(
                f"{self.mirror_url}/store",
                json=payload,
                headers={"Authorization": f"Bearer {self.mirror_token}"},
            )
            resp.raise_for_status()
            logger.info("Stored in Mirror: %s", context)
            return True
        except (httpx.HTTPError, httpx.ConnectError) as exc:
            logger.warning("Mirror unavailable (%s), falling back to local file", exc)
            return self._store_local_fallback(content, context)

    def _store_local_fallback(self, content: str, context: str) -> bool:
        """Write analytics report to local JSON when Mirror is unreachable."""
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        fallback_path = FALLBACK_DIR / self.tenant / f"{date_str}.json"
        fallback_path.parent.mkdir(parents=True, exist_ok=True)

        # Append to existing file if it exists (multiple reports per day)
        existing: list[dict[str, str]] = []
        if fallback_path.exists():
            try:
                existing = json.loads(fallback_path.read_text())
            except (json.JSONDecodeError, ValueError):
                existing = []

        existing.append({
            "context": context,
            "content": content,
            "timestamp": datetime.utcnow().isoformat(),
        })

        fallback_path.write_text(json.dumps(existing, indent=2))
        logger.info("Saved locally: %s", fallback_path)
        return True

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()
