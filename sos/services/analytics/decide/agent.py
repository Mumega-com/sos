"""Decision agent — reads analytics from Mirror, generates tasks.

The flywheel: READ (ingest) -> DECIDE (this) -> ACT (SitePilotAI) -> LEARN (Mirror)

Reads the latest analytics summary from Mirror for a tenant.
Compares with previous weeks. Identifies:
- Pages losing traffic -> rewrite/update
- Keywords dropping position -> new content needed
- High bounce rate pages -> UX fix needed
- Rage clicks (Clarity) -> fix broken elements
- Top performing content -> double down

Outputs: task list as JSON, optionally creates tasks in Squad Service.

Usage:
    python -m sos.services.analytics.decide --tenant viamar
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger("sos.analytics.decide")

# Thresholds for decision triggers
BOUNCE_RATE_HIGH = 65.0
POSITION_DROP_THRESHOLD = 2.0
RAGE_CLICK_THRESHOLD = 10
SESSION_GROWTH_THRESHOLD = 30.0  # percent


class DecisionAgent:
    """Reads Mirror analytics, compares weeks, generates actionable tasks."""

    def __init__(
        self,
        tenant: str,
        mirror_url: str,
        mirror_token: str,
        squad_url: Optional[str] = None,
    ) -> None:
        self.tenant = tenant
        self.mirror_url = mirror_url.rstrip("/")
        self.mirror_token = mirror_token
        self.squad_url = squad_url.rstrip("/") if squad_url else None
        self._client = httpx.AsyncClient(timeout=30)

    async def recall_analytics(self, weeks: int = 2) -> list[dict[str, str]]:
        """Query Mirror for the last N weeks of analytics summaries.

        Returns list of dicts with 'text' and 'context_id' keys.
        """
        query = f"Weekly Analytics for {self.tenant}"
        try:
            resp = await self._client.post(
                f"{self.mirror_url}/search",
                json={"query": query, "limit": weeks, "project": self.tenant},
                headers={"Authorization": f"Bearer {self.mirror_token}"},
            )
            resp.raise_for_status()
            results = resp.json()
            # Mirror search returns list of engram objects
            engrams: list[dict[str, str]] = []
            if isinstance(results, list):
                engrams = results
            elif isinstance(results, dict) and "results" in results:
                engrams = results["results"]
            else:
                logger.warning("Unexpected Mirror response shape: %s", type(results))
                return self._mock_analytics()

            # Check that engrams actually have text content
            has_text = any(e.get("text") or e.get("content") for e in engrams)
            if not has_text:
                logger.info("Mirror returned engrams without text for %s, using mock data", self.tenant)
                return self._mock_analytics()

            return engrams
        except (httpx.HTTPError, httpx.ConnectError) as exc:
            logger.warning("Mirror unavailable (%s), using mock data", exc)
            return self._mock_analytics()

    def _mock_analytics(self) -> list[dict[str, str]]:
        """Mock analytics for pipeline development when Mirror is unavailable."""
        return [
            {
                "text": (
                    f"Weekly Analytics for {self.tenant} (2026-04-07): "
                    "GA4 Report: Top pages: /, /services, /about, /contact, /blog/seo-tips. "
                    "Total sessions (top 10): 5418. Bounce rate: 44.3%. "
                    "Page /pricing bounce rate: 78.2%. Page /careers bounce rate: 61.3%. "
                    "| GSC Report: Top queries: dental near me, best dentist toronto. "
                    "Total clicks: 1298. Avg position: 9.8. "
                    "Query 'freight shipping toronto' position: 9.1. "
                    "| Clarity Report: Rage clicks on: #contact-form-submit (12), "
                    ".pricing-card .select-plan (28). "
                    "Scroll depth: 56%. Drop-off at: pricing-comparison."
                ),
                "context_id": f"analytics-weekly-{self.tenant}-2026-04-07",
            },
            {
                "text": (
                    f"Weekly Analytics for {self.tenant} (2026-03-31): "
                    "GA4 Report: Top pages: /, /services, /about, /blog/seo-tips, /contact. "
                    "Total sessions (top 10): 4980. Bounce rate: 42.1%. "
                    "Page /pricing bounce rate: 65.0%. Page /careers bounce rate: 58.7%. "
                    "| GSC Report: Top queries: dental near me, best dentist toronto. "
                    "Total clicks: 1190. Avg position: 9.2. "
                    "Query 'freight shipping toronto' position: 5.3. "
                    "| Clarity Report: Rage clicks on: nav .dropdown-toggle (47), "
                    "#cta-button (31). "
                    "Scroll depth: 58%. Drop-off at: faq-section."
                ),
                "context_id": f"analytics-weekly-{self.tenant}-2026-03-31",
            },
        ]

    async def load_adaptations(self) -> dict[str, object]:
        """Query Mirror for adaptation rules generated by the feedback loop.

        Returns priority adjustments and skip conditions.
        If none found, returns defaults (all 1.0 weights, no skip conditions).
        """
        defaults: dict[str, object] = {
            "priority_adjustments": {},
            "skip_conditions": [],
            "boost_types": [],
            "reduce_types": [],
        }

        try:
            resp = await self._client.post(
                f"{self.mirror_url}/search",
                json={
                    "query": f"Adaptation Rules for {self.tenant}",
                    "limit": 1,
                    "project": self.tenant,
                },
                headers={"Authorization": f"Bearer {self.mirror_token}"},
            )
            resp.raise_for_status()
            results = resp.json()

            engrams: list[dict[str, str]] = []
            if isinstance(results, list):
                engrams = results
            elif isinstance(results, dict) and "results" in results:
                engrams = results["results"]

            if not engrams:
                logger.info("No adaptation rules found for %s, using defaults", self.tenant)
                return defaults

            text = engrams[0].get("text", "")

            # Parse adaptation rules from text
            # Format: "Adjustments: {json}. Skip conditions: [...]."
            adj_match = re.search(r"Adjustments:\s*(\{[^}]+\})", text)
            if adj_match:
                try:
                    defaults["priority_adjustments"] = json.loads(adj_match.group(1))
                except (json.JSONDecodeError, ValueError):
                    pass

            boost_match = re.search(r"Boost:\s*\[([^\]]*)\]", text)
            if boost_match:
                defaults["boost_types"] = [
                    t.strip().strip("'\"")
                    for t in boost_match.group(1).split(",")
                    if t.strip()
                ]

            reduce_match = re.search(r"Reduce:\s*\[([^\]]*)\]", text)
            if reduce_match:
                defaults["reduce_types"] = [
                    t.strip().strip("'\"")
                    for t in reduce_match.group(1).split(",")
                    if t.strip()
                ]

            skip_match = re.search(r"Skip conditions:\s*\[([^\]]*)\]", text)
            if skip_match:
                raw = skip_match.group(1).strip()
                if raw:
                    defaults["skip_conditions"] = [
                        s.strip().strip("'\"") for s in raw.split(",") if s.strip()
                    ]

            logger.info(
                "Loaded adaptations for %s: boost=%s, reduce=%s",
                self.tenant,
                defaults["boost_types"],
                defaults["reduce_types"],
            )
            return defaults

        except (httpx.HTTPError, httpx.ConnectError) as exc:
            logger.warning("Could not load adaptations (%s), using defaults", exc)
            return defaults

    async def analyze(self) -> list[dict[str, str]]:
        """Compare last 2 weeks of analytics. Return list of decisions.

        Each decision: {type, target, reason, priority}
        Types: rewrite, new_content, fix_ux, double_down

        Applies adaptation rules from the feedback loop to adjust priorities
        and skip actions that previously didn't work.
        """
        summaries = await self.recall_analytics(weeks=2)

        if len(summaries) < 2:
            logger.warning("Not enough data for comparison (got %d weeks)", len(summaries))
            if summaries:
                return self._analyze_single(summaries[0])
            return []

        current = summaries[0].get("text", "")
        previous = summaries[1].get("text", "")

        decisions: list[dict[str, str]] = []

        # --- High bounce rate pages ---
        decisions.extend(self._check_bounce_rates(current, previous))

        # --- Keyword position drops ---
        decisions.extend(self._check_keyword_drops(current, previous))

        # --- Rage clicks ---
        decisions.extend(self._check_rage_clicks(current))

        # --- Session growth (double down) ---
        decisions.extend(self._check_session_growth(current, previous))

        # --- Drop-off sections ---
        decisions.extend(self._check_dropoffs(current))

        # --- Apply feedback adaptations ---
        adaptations = await self.load_adaptations()
        decisions = self._apply_adaptations(decisions, adaptations)

        logger.info("Generated %d decisions for %s", len(decisions), self.tenant)
        return decisions

    def _apply_adaptations(
        self,
        decisions: list[dict[str, str]],
        adaptations: dict[str, object],
    ) -> list[dict[str, str]]:
        """Apply feedback loop adaptations to decision list.

        - Adjusts priorities based on action type weights
        - Filters out decisions matching skip conditions
        """
        priority_adjustments: dict[str, float] = adaptations.get("priority_adjustments", {})
        skip_conditions: list[str] = adaptations.get("skip_conditions", [])

        if not priority_adjustments and not skip_conditions:
            return decisions

        priority_order = ["low", "medium", "high"]
        adjusted: list[dict[str, str]] = []

        for decision in decisions:
            decision_type = decision.get("type", "")

            # Check skip conditions
            skip = False
            for condition in skip_conditions:
                condition_lower = condition.lower()
                if decision_type.lower() in condition_lower:
                    logger.info(
                        "Skipping %s on %s — feedback skip condition: %s",
                        decision_type,
                        decision.get("target", ""),
                        condition,
                    )
                    skip = True
                    break
            if skip:
                continue

            # Apply priority weight
            weight = priority_adjustments.get(decision_type, 1.0)
            if weight != 1.0:
                current_priority = decision.get("priority", "medium")
                current_idx = priority_order.index(current_priority) if current_priority in priority_order else 1
                # Weight > 1 boosts priority up, weight < 1 pushes down
                new_idx = round(current_idx * weight)
                new_idx = max(0, min(len(priority_order) - 1, new_idx))
                new_priority = priority_order[new_idx]
                if new_priority != current_priority:
                    logger.info(
                        "Adjusted %s priority: %s -> %s (weight %.1f)",
                        decision_type,
                        current_priority,
                        new_priority,
                        weight,
                    )
                    decision = {**decision, "priority": new_priority}

            adjusted.append(decision)

        if len(adjusted) < len(decisions):
            logger.info(
                "Feedback adaptations: %d -> %d decisions (filtered %d)",
                len(decisions),
                len(adjusted),
                len(decisions) - len(adjusted),
            )

        return adjusted

    def _analyze_single(self, summary: dict[str, str]) -> list[dict[str, str]]:
        """Generate decisions from a single week (no comparison available)."""
        text = summary.get("text", "")
        decisions: list[dict[str, str]] = []
        decisions.extend(self._check_bounce_rates(text, ""))
        decisions.extend(self._check_rage_clicks(text))
        decisions.extend(self._check_dropoffs(text))
        return decisions

    def _check_bounce_rates(
        self, current: str, previous: str
    ) -> list[dict[str, str]]:
        """Detect pages with high or increasing bounce rates."""
        decisions: list[dict[str, str]] = []

        # Parse "Page /path bounce rate: XX.X%"
        current_bounces = dict(re.findall(
            r"Page\s+(\/\S+)\s+bounce rate:\s+(\d+(?:\.\d+)?)%", current
        ))
        previous_bounces = dict(re.findall(
            r"Page\s+(\/\S+)\s+bounce rate:\s+(\d+(?:\.\d+)?)%", previous
        ))

        for page, rate_str in current_bounces.items():
            rate = float(rate_str)
            prev_rate = float(previous_bounces.get(page, "0"))

            if rate >= BOUNCE_RATE_HIGH:
                change = ""
                if prev_rate > 0:
                    change = f" (was {prev_rate:.0f}%)"
                decisions.append({
                    "type": "rewrite",
                    "target": page,
                    "reason": f"Bounce rate {rate:.0f}%{change}. Page needs content or UX improvement.",
                    "priority": "high" if rate >= 75 else "medium",
                })

        return decisions

    def _check_keyword_drops(
        self, current: str, previous: str
    ) -> list[dict[str, str]]:
        """Detect keywords that dropped in search position."""
        decisions: list[dict[str, str]] = []

        # Parse "Query 'keyword' position: XX.X"
        current_positions = dict(re.findall(
            r"Query\s+'([^']+)'\s+position:\s+(\d+(?:\.\d+)?)", current
        ))
        previous_positions = dict(re.findall(
            r"Query\s+'([^']+)'\s+position:\s+(\d+(?:\.\d+)?)", previous
        ))

        for keyword, pos_str in current_positions.items():
            pos = float(pos_str)
            prev_pos_str = previous_positions.get(keyword)
            if prev_pos_str:
                prev_pos = float(prev_pos_str)
                drop = pos - prev_pos  # higher position number = worse ranking
                if drop >= POSITION_DROP_THRESHOLD:
                    decisions.append({
                        "type": "new_content",
                        "target": f"keyword: {keyword}",
                        "reason": (
                            f"Position dropped from #{prev_pos:.0f} to #{pos:.0f}. "
                            "Need supporting blog post."
                        ),
                        "priority": "high" if drop >= 4 else "medium",
                    })

        return decisions

    def _check_rage_clicks(self, current: str) -> list[dict[str, str]]:
        """Detect elements with rage clicks above threshold."""
        decisions: list[dict[str, str]] = []

        # Parse "element (count)" pattern from Clarity section
        # Use greedy match up to sentence boundary (period followed by space or end)
        rage_matches = re.findall(
            r"Rage clicks on:\s*(.+?)(?:\.\s|$)", current
        )
        if rage_matches:
            elements = re.findall(
                r"([^\d,]+?)\s*\((\d+)\)", rage_matches[0]
            )
            for element, count_str in elements:
                count = int(count_str)
                if count >= RAGE_CLICK_THRESHOLD:
                    element = element.strip().rstrip(",").strip()
                    decisions.append({
                        "type": "fix_ux",
                        "target": element,
                        "reason": f"Clarity: {count} rage clicks on {element}.",
                        "priority": "high" if count >= 25 else "medium",
                    })

        return decisions

    def _check_session_growth(
        self, current: str, previous: str
    ) -> list[dict[str, str]]:
        """Detect content with significant session growth -> double down."""
        decisions: list[dict[str, str]] = []

        # Parse total sessions
        current_match = re.search(r"Total sessions.*?:\s*(\d+)", current)
        previous_match = re.search(r"Total sessions.*?:\s*(\d+)", previous)

        if current_match and previous_match:
            current_sessions = int(current_match.group(1))
            previous_sessions = int(previous_match.group(1))

            if previous_sessions > 0:
                growth_pct = (
                    (current_sessions - previous_sessions) / previous_sessions
                ) * 100

                if growth_pct >= SESSION_GROWTH_THRESHOLD:
                    # Find top performing content from current week
                    top_match = re.search(
                        r"Top pages:\s*([^.]+)", current
                    )
                    if top_match:
                        top_page = top_match.group(1).split(",")[0].strip()
                        decisions.append({
                            "type": "double_down",
                            "target": top_page,
                            "reason": (
                                f"Sessions up {growth_pct:.0f}% week-over-week. "
                                "Write more content on this topic."
                            ),
                            "priority": "medium",
                        })

        return decisions

    def _check_dropoffs(self, current: str) -> list[dict[str, str]]:
        """Detect drop-off sections from Clarity data."""
        decisions: list[dict[str, str]] = []

        dropoff_match = re.search(r"Drop-off at:\s*([^.]+)", current)
        if dropoff_match:
            sections = [
                s.strip() for s in dropoff_match.group(1).split(",")
            ]
            for section in sections:
                if section:
                    decisions.append({
                        "type": "fix_ux",
                        "target": f"section: {section}",
                        "reason": f"Users drop off at {section}. Simplify or restructure.",
                        "priority": "medium",
                    })

        return decisions

    async def create_tasks(self, decisions: list[dict[str, str]]) -> list[str]:
        """Create tasks in Squad Service for each decision.

        Returns list of task IDs. If no squad_url, prints tasks to stdout.
        """
        if not decisions:
            logger.info("No decisions to create tasks for")
            return []

        task_ids: list[str] = []

        priority_map = {
            "high": "high",
            "medium": "medium",
            "low": "low",
        }

        squad_id_map = {
            "rewrite": "content",
            "new_content": "content",
            "fix_ux": "dev",
            "double_down": "content",
        }

        # Wire 3: Bounty values per decision type (in $MIND)
        bounty_values = {
            "rewrite": {"high": 200.0, "medium": 100.0, "low": 50.0},
            "new_content": {"high": 150.0, "medium": 100.0, "low": 50.0},
            "fix_ux": {"high": 150.0, "medium": 75.0, "low": 40.0},
            "double_down": {"high": 200.0, "medium": 100.0, "low": 50.0},
        }

        for decision in decisions:
            task_id = f"decide-{uuid.uuid4().hex[:8]}"
            decision_type = decision["type"]
            target = decision["target"]
            reason = decision["reason"]
            priority = priority_map.get(decision.get("priority", "medium"), "medium")
            squad_id = squad_id_map.get(decision_type, "dev")

            title_prefix = {
                "rewrite": "Rewrite",
                "new_content": "Create content for",
                "fix_ux": "Fix UX",
                "double_down": "Expand content on",
            }
            title = f"[{self.tenant}] {title_prefix.get(decision_type, 'Action')}: {target}"

            if self.squad_url:
                # Wire 3: Calculate bounty value
                reward = bounty_values.get(decision_type, {}).get(priority, 50.0)

                payload = {
                    "id": task_id,
                    "squad_id": squad_id,
                    "title": title,
                    "description": reason,
                    "priority": priority,
                    "project": self.tenant,
                    "labels": [decision_type, "analytics-decide", self.tenant],
                    "inputs": {
                        "decision_type": decision_type,
                        "target": target,
                        "source": "analytics-decide",
                    },
                    "bounty": {
                        "reward": reward,
                        "currency": "MIND",
                        "timeout_hours": 48.0,
                        "source": "analytics-decide",
                    },
                }

                try:
                    resp = await self._client.post(
                        f"{self.squad_url}/tasks",
                        json=payload,
                    )
                    resp.raise_for_status()
                    task_ids.append(task_id)
                    logger.info("Created task %s: %s", task_id, title)
                except (httpx.HTTPError, httpx.ConnectError) as exc:
                    logger.error("Failed to create task %s: %s", task_id, exc)
            else:
                logger.info("Task (dry run): [%s] %s — %s", priority, title, reason)
                task_ids.append(task_id)

        return task_ids

    async def run(self) -> list[dict[str, str]]:
        """Full decision cycle: analyze, create tasks, store report in Mirror."""
        decisions = await self.analyze()

        task_ids = await self.create_tasks(decisions)

        # Store decision report in Mirror
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        priority_counts: dict[str, int] = {}
        for d in decisions:
            p = d.get("priority", "medium")
            priority_counts[p] = priority_counts.get(p, 0) + 1

        breakdown = ", ".join(f"{k}: {v}" for k, v in sorted(priority_counts.items()))
        report_text = (
            f"Decision Report for {self.tenant} ({date_str}): "
            f"{len(decisions)} actions generated. "
            f"Priorities: {breakdown}. "
            f"Tasks created: {len(task_ids)}."
        )

        try:
            await self._client.post(
                f"{self.mirror_url}/store",
                json={
                    "agent": "analytics-decide",
                    "context_id": f"decision-report-{self.tenant}-{date_str}",
                    "text": report_text,
                    "project": self.tenant,
                    "core_concepts": ["analytics", "decisions", "tasks"],
                    "metadata": {
                        "source": "analytics-decide",
                        "tenant": self.tenant,
                        "decisions": len(decisions),
                        "tasks_created": len(task_ids),
                        "decided_at": datetime.now(timezone.utc).isoformat(),
                    },
                },
                headers={"Authorization": f"Bearer {self.mirror_token}"},
            )
            logger.info("Decision report stored in Mirror for %s", self.tenant)
        except (httpx.HTTPError, httpx.ConnectError) as exc:
            logger.warning("Failed to store decision report in Mirror: %s", exc)

        # Print summary
        logger.info(report_text)
        for d in decisions:
            logger.info(
                "  [%s] %s: %s — %s",
                d["priority"],
                d["type"],
                d["target"],
                d["reason"],
            )

        return decisions

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
