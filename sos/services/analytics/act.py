"""Action agent — executes decisions via SitePilotAI and GHL.

The flywheel: READ (ingest) -> DECIDE -> ACT (this) -> LEARN (Mirror)

Takes a list of decisions (from decide.py) and executes:
- "rewrite" -> calls SitePilotAI MCP to update page content
- "new_content" -> calls SitePilotAI MCP to create new blog post
- "fix_ux" -> calls SitePilotAI MCP to update widget/section
- "double_down" -> calls SitePilotAI MCP to create related posts
- "send_followup" -> calls GHL API to trigger email/SMS

Usage:
    python -m sos.services.analytics.act --tenant viamar --decisions /path/to/decisions.json
    python -m sos.services.analytics.act --tenant viamar --from-mirror
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("sos.analytics.act")

FALLBACK_DIR = Path.home() / ".sos" / "analytics"


class ActionAgent:
    """Executes decisions from the decision agent via SitePilotAI and GHL."""

    def __init__(
        self,
        tenant: str,
        mcp_url: Optional[str] = None,
        mcp_token: Optional[str] = None,
        ghl_api_key: Optional[str] = None,
        mirror_url: Optional[str] = None,
        mirror_token: Optional[str] = None,
    ) -> None:
        self.tenant = tenant
        self.mcp_url = (mcp_url or "").rstrip("/")
        self.mcp_token = mcp_token or ""
        self.ghl_api_key = ghl_api_key or ""
        self.mirror_url = (mirror_url or "http://localhost:8844").rstrip("/")
        self.mirror_token = mirror_token or ""
        self._client = httpx.AsyncClient(timeout=30)

    # ------------------------------------------------------------------
    # Decision routing
    # ------------------------------------------------------------------

    async def execute_decision(self, decision: dict[str, str]) -> dict[str, object]:
        """Route a single decision to the appropriate executor.

        Expected decision format:
        {
            "type": "rewrite"|"new_content"|"fix_ux"|"double_down"|"send_followup",
            "target": "/services" or "dental implants" or "hero-section",
            "reason": "High bounce rate on /services (55%)",
            "priority": "high"|"medium"|"low"
        }
        """
        action_type = decision.get("type", "unknown")
        target = decision.get("target", "unknown")
        reason = decision.get("reason", "")

        try:
            if action_type == "rewrite":
                details = await self._act_rewrite(target, reason)
            elif action_type == "new_content":
                details = await self._act_new_content(target, reason)
            elif action_type == "fix_ux":
                details = await self._act_fix_ux(target, reason)
            elif action_type == "double_down":
                details = await self._act_double_down(target, reason)
            elif action_type == "send_followup":
                details = await self._act_send_followup(target, reason)
            else:
                logger.warning("Unknown decision type: %s", action_type)
                return {
                    "decision": decision,
                    "status": "skipped",
                    "details": f"Unknown action type: {action_type}",
                }

            return {
                "decision": decision,
                "status": "executed",
                "details": details,
            }
        except Exception as exc:
            logger.error("Failed to execute %s on %s: %s", action_type, target, exc)
            return {
                "decision": decision,
                "status": "failed",
                "details": str(exc),
            }

    # ------------------------------------------------------------------
    # Action stubs — each has a TODO showing the real implementation
    # ------------------------------------------------------------------

    async def _dispatch_to_agent(self, agent: str, title: str, description: str, priority: str = "medium") -> str:
        """Dispatch a task to an agent via the coordination protocol.

        Creates task in Squad Service and sends DELEGATE via bus.
        The agent picks it up via task poller or bus wake.
        """
        from sos.kernel.coordination import Coordinator
        coord = Coordinator()
        task_id = coord.delegate(
            to=agent,
            title=title,
            from_agent="analytics-act",
            description=description,
            priority=priority,
            squad_id=self.tenant,
        )
        logger.info("Dispatched to %s: %s (task_id=%s)", agent, title[:50], task_id)
        return f"Delegated to {agent}: {title} (task_id={task_id})"

    async def _act_rewrite(self, target: str, reason: str) -> str:
        """Rewrite existing page content — dispatched to mumcp for WordPress."""
        return await self._dispatch_to_agent(
            agent="mumcp",
            title=f"Rewrite page {target} — {reason[:60]}",
            description=(
                f"Page {target} needs a rewrite.\n"
                f"Reason: {reason}\n\n"
                f"Improve the content to reduce bounce rate. Focus on:\n"
                f"- More compelling intro/hero section\n"
                f"- Clearer value proposition\n"
                f"- Stronger CTAs\n"
                f"Set as draft for review before publishing."
            ),
            priority="high",
        )

    async def _act_new_content(self, target: str, reason: str) -> str:
        """Create new blog content — dispatched to sol (content agent)."""
        return await self._dispatch_to_agent(
            agent="sol",
            title=f"Write blog post: {target}",
            description=(
                f"Create an SEO-optimized blog post for: {target}\n"
                f"Reason: {reason}\n\n"
                f"Requirements:\n"
                f"- 1500+ words, well-structured with H2/H3 headers\n"
                f"- Target keyword in title, first paragraph, and headers\n"
                f"- Include internal links to related content\n"
                f"- Set as draft for review"
            ),
            priority="medium",
        )

    async def _act_fix_ux(self, target: str, reason: str) -> str:
        """Fix UX issue — dispatched to mumcp for Elementor edits."""
        return await self._dispatch_to_agent(
            agent="mumcp",
            title=f"Fix UX: {target}",
            description=(
                f"UX issue detected on element: {target}\n"
                f"Reason: {reason}\n\n"
                f"Investigate the element and fix the issue:\n"
                f"- If rage clicks: element may be broken or misleading\n"
                f"- If dead clicks: element looks clickable but isn't — add link or make non-interactive\n"
                f"- If drop-off: section may be too long or confusing — simplify"
            ),
            priority="high" if "rage" in reason.lower() else "medium",
        )

    async def _act_double_down(self, target: str, reason: str) -> str:
        """Create related content for winning topics — dispatched to sol."""
        return await self._dispatch_to_agent(
            agent="sol",
            title=f"Expand content: {target}",
            description=(
                f"This topic is performing well: {target}\n"
                f"Reason: {reason}\n\n"
                f"Create 2 related posts:\n"
                f"1. A deeper dive into the same topic\n"
                f"2. A related angle that links back to the original\n"
                f"Both as drafts for review."
            ),
            priority="medium",
        )

    async def _act_send_followup(self, target: str, reason: str) -> str:
        """Send follow-up via GHL — dispatched to mizan (business agent)."""
        return await self._dispatch_to_agent(
            agent="mizan",
            title=f"Send follow-up: {target}",
            description=(
                f"Follow-up action needed for: {target}\n"
                f"Reason: {reason}\n\n"
                f"Use GHL to create/update the contact and trigger the follow-up workflow.\n"
                f"IMPORTANT: Do NOT send outbound without Hadi's approval."
            ),
            priority="medium",
        )

    # ------------------------------------------------------------------
    # Batch execution
    # ------------------------------------------------------------------

    async def execute_all(self, decisions: list[dict[str, str]]) -> list[dict[str, object]]:
        """Execute all decisions sequentially, store summary in Mirror."""
        results: list[dict[str, object]] = []

        for decision in decisions:
            result = await self.execute_decision(decision)
            results.append(result)

        # Build execution summary
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        executed = sum(1 for r in results if r["status"] == "executed")
        skipped = sum(1 for r in results if r["status"] == "skipped")
        failed = sum(1 for r in results if r["status"] == "failed")

        action_types = [r["decision"]["type"] for r in results if r["status"] == "executed"]
        summary = ", ".join(action_types) if action_types else "none"

        report = (
            f"Action Report for {self.tenant} ({date_str}): "
            f"Executed {executed} actions. "
            f"Skipped {skipped}. Failed {failed}. "
            f"Results: {summary}."
        )

        await self._store_in_mirror(
            content=report,
            context=f"act-report-{self.tenant}-{date_str}",
        )
        logger.info("Action report stored: %s", report)

        # Run the LEARN step
        await self.learn(results)

        return results

    # ------------------------------------------------------------------
    # LEARN — close the flywheel loop
    # ------------------------------------------------------------------

    async def learn(self, results: list[dict[str, object]]) -> None:
        """Store what was done in Mirror so next week's analytics can measure impact.

        This closes the flywheel loop:
        - We record each action taken
        - Next week's ingest will pull fresh analytics
        - The decision agent can compare before/after
        - The cycle repeats
        """
        date_str = datetime.utcnow().strftime("%Y-%m-%d")

        for result in results:
            decision = result["decision"]
            action_type = decision.get("type", "unknown")
            target = decision.get("target", "unknown")
            reason = decision.get("reason", "")
            status = result["status"]

            memory = (
                f"Executed: {action_type} on {target}. "
                f"Reason: {reason}. "
                f"Status: {status}. "
                f"Check next week if it worked."
            )

            await self._store_in_mirror(
                content=memory,
                context=f"act-learn-{self.tenant}-{action_type}-{date_str}",
            )

        logger.info(
            "LEARN: Stored %d action memories for %s",
            len(results),
            self.tenant,
        )

    # ------------------------------------------------------------------
    # Mirror storage
    # ------------------------------------------------------------------

    async def _store_in_mirror(self, content: str, context: str) -> bool:
        """POST engram to Mirror. Falls back to local JSON if Mirror is down."""
        payload = {
            "agent": "analytics-act",
            "context_id": context,
            "text": content,
            "project": self.tenant,
            "core_concepts": ["analytics", "action", "flywheel"],
            "metadata": {
                "source": "analytics-act",
                "tenant": self.tenant,
                "acted_at": datetime.utcnow().isoformat(),
            },
        }

        try:
            resp = await self._client.post(
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
        """Write action report to local JSON when Mirror is unreachable."""
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        fallback_path = FALLBACK_DIR / self.tenant / f"act-{date_str}.json"
        fallback_path.parent.mkdir(parents=True, exist_ok=True)

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

    # ------------------------------------------------------------------
    # Mirror recall — load decisions from previous decide step
    # ------------------------------------------------------------------

    async def load_decisions_from_mirror(self) -> list[dict[str, str]]:
        """Recall decisions stored by the decide agent from Mirror.

        TODO: The decide agent should store decisions with context_id
        like "decide-{tenant}-{date}" so we can recall them here.
        """
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        context = f"decide-{self.tenant}-{date_str}"

        try:
            resp = await self._client.post(
                f"{self.mirror_url}/recall",
                json={
                    "query": f"decisions for {self.tenant}",
                    "project": self.tenant,
                    "limit": 1,
                },
                headers={"Authorization": f"Bearer {self.mirror_token}"},
            )
            resp.raise_for_status()
            data = resp.json()

            # Extract decisions from the Mirror response
            engrams = data.get("engrams", [])
            if not engrams:
                logger.warning("No decisions found in Mirror for %s", context)
                return []

            # The decide agent stores decisions as JSON in the text field
            text = engrams[0].get("text", "")
            try:
                decisions = json.loads(text)
                if isinstance(decisions, list):
                    return decisions
            except (json.JSONDecodeError, ValueError):
                logger.warning("Could not parse decisions from Mirror text")

            return []
        except (httpx.HTTPError, httpx.ConnectError) as exc:
            logger.warning("Mirror unavailable (%s), checking local fallback", exc)
            return self._load_local_fallback()

    def _load_local_fallback(self) -> list[dict[str, str]]:
        """Load decisions from local fallback file."""
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        fallback_path = FALLBACK_DIR / self.tenant / f"decide-{date_str}.json"

        if not fallback_path.exists():
            logger.warning("No local decisions file at %s", fallback_path)
            return []

        try:
            data = json.loads(fallback_path.read_text())
            if isinstance(data, list) and data:
                # Get the latest entry's content
                text = data[-1].get("content", "")
                decisions = json.loads(text)
                if isinstance(decisions, list):
                    return decisions
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("Failed to parse local decisions: %s", exc)

        return []

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="SOS Analytics Action Agent")
    parser.add_argument("--tenant", required=True, help="Tenant slug")
    parser.add_argument("--decisions", help="Path to decisions JSON file")
    parser.add_argument(
        "--from-mirror",
        action="store_true",
        help="Load decisions from Mirror (stored by decide step)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    mirror_url = os.environ.get("MIRROR_URL", "http://localhost:8844")
    mirror_token = os.environ.get("MIRROR_TOKEN", "")
    mcp_url = os.environ.get("SITEPILOT_MCP_URL", "")
    mcp_token = os.environ.get("SITEPILOT_MCP_TOKEN", "")
    ghl_api_key = os.environ.get("GHL_API_KEY", "")

    agent = ActionAgent(
        tenant=args.tenant,
        mcp_url=mcp_url,
        mcp_token=mcp_token,
        ghl_api_key=ghl_api_key,
        mirror_url=mirror_url,
        mirror_token=mirror_token,
    )

    async def run() -> None:
        try:
            decisions: list[dict[str, str]] = []

            if args.decisions:
                decisions_path = Path(args.decisions)
                if not decisions_path.exists():
                    logger.error("Decisions file not found: %s", decisions_path)
                    sys.exit(1)
                decisions = json.loads(decisions_path.read_text())
            elif args.from_mirror:
                decisions = await agent.load_decisions_from_mirror()
            else:
                logger.error("Specify --decisions FILE or --from-mirror")
                sys.exit(1)

            if not decisions:
                logger.warning("No decisions to execute for %s", args.tenant)
                sys.exit(0)

            logger.info("Executing %d decisions for %s", len(decisions), args.tenant)
            results = await agent.execute_all(decisions)

            # Print summary
            executed = sum(1 for r in results if r["status"] == "executed")
            skipped = sum(1 for r in results if r["status"] == "skipped")
            failed = sum(1 for r in results if r["status"] == "failed")
            print(
                f"Done: {executed} executed, {skipped} skipped, {failed} failed "
                f"for {args.tenant}"
            )
        finally:
            await agent.close()

    asyncio.run(run())


if __name__ == "__main__":
    main()
