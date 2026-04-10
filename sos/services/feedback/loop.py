"""SOS Feedback Loop — makes the organism learn from its own actions.

The missing piece: after the organism ACTS, did it work?

Cycle:
1. OBSERVE: What happened after our actions? (pull fresh analytics)
2. COMPARE: Before vs after. Did traffic go up? Bounce rate down? Leads increase?
3. SCORE: Each past action gets a score (positive/negative/neutral)
4. LEARN: Store scored actions in Mirror with outcome data
5. ADAPT: Adjust decision-making weights based on what worked

Over time: the organism stops doing things that don't work
and doubles down on things that do. Per-tenant. Compounding.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("sos.feedback.loop")

FALLBACK_DIR = Path.home() / ".sos" / "feedback"

# Metrics where lower is better (inverted polarity)
LOWER_IS_BETTER = {"bounce_rate", "rage_clicks", "position"}


class FeedbackLoop:
    """Observe, compare, score, learn, adapt — the organism's learning cycle."""

    def __init__(
        self,
        tenant: str,
        mirror_url: str,
        mirror_token: str,
    ) -> None:
        self.tenant = tenant
        self.mirror_url = mirror_url.rstrip("/")
        self.mirror_token = mirror_token
        self._client = httpx.AsyncClient(timeout=30)

    # ------------------------------------------------------------------
    # 1. OBSERVE — gather what happened
    # ------------------------------------------------------------------

    async def observe(self) -> dict[str, object]:
        """Query Mirror for latest analytics and action reports.

        Returns:
            {
                "analytics": [latest analytics engrams],
                "actions": [actions taken last cycle],
                "period": "2026-04-01 to 2026-04-08"
            }
        """
        analytics = await self._recall("Weekly Analytics", limit=2)
        actions = await self._recall("Executed:", limit=10)

        # Determine period from analytics dates
        dates = []
        for entry in analytics:
            text = entry.get("text", "")
            date_match = re.search(r"\((\d{4}-\d{2}-\d{2})\)", text)
            if date_match:
                dates.append(date_match.group(1))

        if len(dates) >= 2:
            period = f"{dates[-1]} to {dates[0]}"
        else:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            period = f"unknown to {now}"

        logger.info(
            "OBSERVE: Found %d analytics reports, %d actions for %s (%s)",
            len(analytics),
            len(actions),
            self.tenant,
            period,
        )

        return {
            "analytics": analytics,
            "actions": actions,
            "period": period,
        }

    # ------------------------------------------------------------------
    # 2. COMPARE — before vs after for each action
    # ------------------------------------------------------------------

    async def compare(self, observation: dict[str, object]) -> list[dict[str, object]]:
        """For each action taken, find the metric it targeted and compare before/after.

        Returns list of scored action dicts with delta and outcome.
        """
        analytics_list: list[dict[str, str]] = observation.get("analytics", [])
        actions_list: list[dict[str, str]] = observation.get("actions", [])

        if len(analytics_list) < 2:
            logger.warning("Need at least 2 analytics reports to compare")
            return self._mock_comparisons()

        current_text = analytics_list[0].get("text", "")
        previous_text = analytics_list[1].get("text", "")

        # Parse metrics from both weeks
        current_metrics = self._parse_metrics(current_text)
        previous_metrics = self._parse_metrics(previous_text)

        if not actions_list:
            logger.warning("No actions found to compare, using mock data")
            return self._mock_comparisons()

        comparisons: list[dict[str, object]] = []

        for action_entry in actions_list:
            action_text = action_entry.get("text", "")
            parsed = self._parse_action(action_text)
            if not parsed:
                continue

            action_type = parsed["type"]
            target = parsed["target"]
            reason = parsed["reason"]

            # Find the metric this action was supposed to improve
            metric_key, metric_before, metric_after = self._find_relevant_metric(
                action_type, target, reason, current_metrics, previous_metrics
            )

            if metric_before is None or metric_after is None:
                comparisons.append({
                    "action": f"{action_type} {target}",
                    "reason": reason,
                    "metric_before": None,
                    "metric_after": None,
                    "delta": 0,
                    "outcome": "neutral",
                    "confidence": 0.3,
                    "lesson": f"Could not find metric for {action_type} on {target}. Need better tracking.",
                })
                continue

            delta = metric_after - metric_before
            outcome = self._determine_outcome(metric_key, delta)
            confidence = self._estimate_confidence(action_type, abs(delta))

            lesson = self._generate_lesson(
                action_type, target, outcome, delta, metric_key
            )

            comparisons.append({
                "action": f"{action_type} {target}",
                "reason": reason,
                "metric_before": metric_before,
                "metric_after": metric_after,
                "delta": round(delta, 2),
                "outcome": outcome,
                "confidence": confidence,
                "lesson": lesson,
            })

        if not comparisons:
            return self._mock_comparisons()

        logger.info("COMPARE: Scored %d actions for %s", len(comparisons), self.tenant)
        return comparisons

    # ------------------------------------------------------------------
    # 3. SCORE — aggregate results
    # ------------------------------------------------------------------

    async def score(self, comparisons: list[dict[str, object]]) -> dict[str, object]:
        """Aggregate comparisons into a summary scorecard.

        Returns:
            {
                "period": str,
                "total_actions": int,
                "positive": int,
                "negative": int,
                "neutral": int,
                "effectiveness": float,
                "best_type": str,
                "worst_type": str,
                "lessons": list[str]
            }
        """
        total = len(comparisons)
        positive = sum(1 for c in comparisons if c["outcome"] == "positive")
        negative = sum(1 for c in comparisons if c["outcome"] == "negative")
        neutral = total - positive - negative

        effectiveness = positive / total if total > 0 else 0.0

        # Find best and worst action types
        type_scores: dict[str, list[int]] = {}
        for comp in comparisons:
            action_str = str(comp.get("action", ""))
            action_type = action_str.split(" ")[0] if action_str else "unknown"
            if action_type not in type_scores:
                type_scores[action_type] = []
            score_val = 1 if comp["outcome"] == "positive" else (-1 if comp["outcome"] == "negative" else 0)
            type_scores[action_type].append(score_val)

        best_type = "unknown"
        worst_type = "unknown"
        best_avg = -2.0
        worst_avg = 2.0

        for action_type, scores in type_scores.items():
            avg = sum(scores) / len(scores) if scores else 0
            if avg > best_avg:
                best_avg = avg
                best_type = action_type
            if avg < worst_avg:
                worst_avg = avg
                worst_type = action_type

        lessons = [str(c["lesson"]) for c in comparisons if c.get("lesson")]

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        summary: dict[str, object] = {
            "period": now,
            "total_actions": total,
            "positive": positive,
            "negative": negative,
            "neutral": neutral,
            "effectiveness": round(effectiveness, 2),
            "best_type": best_type,
            "worst_type": worst_type,
            "lessons": lessons,
        }

        logger.info(
            "SCORE: %d/%d positive (%.0f%% effective) for %s",
            positive,
            total,
            effectiveness * 100,
            self.tenant,
        )
        return summary

    # ------------------------------------------------------------------
    # 4. LEARN — store everything in Mirror
    # ------------------------------------------------------------------

    async def learn(
        self,
        scored: list[dict[str, object]],
        summary: dict[str, object],
    ) -> None:
        """Store scored actions and summary in Mirror for compounding knowledge."""
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Store each scored action
        for comp in scored:
            await self._store_in_mirror(
                text=(
                    f"Feedback for {self.tenant}: Action '{comp['action']}' — "
                    f"outcome: {comp['outcome']}, delta: {comp['delta']}, "
                    f"confidence: {comp['confidence']}. "
                    f"Lesson: {comp['lesson']}"
                ),
                context_id=f"feedback-action-{self.tenant}-{date_str}-{hash(str(comp['action'])) % 10000:04d}",
                concepts=["feedback", "action-outcome", comp.get("outcome", "neutral")],
            )

        # Store the summary as a Feedback Report
        summary_text = (
            f"Feedback Report for {self.tenant} ({date_str}): "
            f"{summary['total_actions']} actions evaluated. "
            f"Positive: {summary['positive']}, Negative: {summary['negative']}, "
            f"Neutral: {summary['neutral']}. "
            f"Effectiveness: {summary['effectiveness']:.0%}. "
            f"Best type: {summary['best_type']}. Worst type: {summary['worst_type']}."
        )
        await self._store_in_mirror(
            text=summary_text,
            context_id=f"feedback-report-{self.tenant}-{date_str}",
            concepts=["feedback", "report", "effectiveness"],
        )

        # Store lessons as separate memory entries — these compound over time
        for lesson in summary.get("lessons", []):
            await self._store_in_mirror(
                text=(
                    f"LESSON for {self.tenant}: {lesson}. "
                    f"Source: feedback loop {date_str}."
                ),
                context_id=f"lesson-{self.tenant}-{date_str}-{hash(lesson) % 10000:04d}",
                concepts=["lesson", "feedback", self.tenant],
            )

        logger.info(
            "LEARN: Stored %d scored actions, 1 report, %d lessons for %s",
            len(scored),
            len(summary.get("lessons", [])),
            self.tenant,
        )

    # ------------------------------------------------------------------
    # 5. ADAPT — generate rules for next decision cycle
    # ------------------------------------------------------------------

    async def adapt(self, summary: dict[str, object]) -> dict[str, object]:
        """Generate adaptation rules based on what worked and what didn't.

        These rules are read by the decision agent before generating tasks.
        """
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        effectiveness = float(summary.get("effectiveness", 0))
        best_type = str(summary.get("best_type", "unknown"))
        worst_type = str(summary.get("worst_type", "unknown"))
        lessons = summary.get("lessons", [])

        # Build priority adjustments
        # Types that work get boosted, types that don't get reduced
        priority_adjustments: dict[str, float] = {}

        if best_type != "unknown":
            # Boost effective types by 20%
            priority_adjustments[best_type] = 1.2

        if worst_type != "unknown" and worst_type != best_type:
            # Reduce ineffective types by 30%
            priority_adjustments[worst_type] = 0.7

        # Build skip conditions from negative lessons
        skip_conditions: list[str] = []
        for lesson in lessons:
            lesson_str = str(lesson)
            if "didn't work" in lesson_str.lower() or "need" in lesson_str.lower():
                skip_conditions.append(lesson_str)

        # Build boost/reduce lists
        boost_types: list[str] = [
            t for t, w in priority_adjustments.items() if w > 1.0
        ]
        reduce_types: list[str] = [
            t for t, w in priority_adjustments.items() if w < 1.0
        ]

        adaptations: dict[str, object] = {
            "tenant": self.tenant,
            "generated_at": date_str,
            "effectiveness": effectiveness,
            "boost_types": boost_types,
            "reduce_types": reduce_types,
            "skip_conditions": skip_conditions,
            "priority_adjustments": priority_adjustments,
        }

        # Store in Mirror for the decision agent to read
        await self._store_in_mirror(
            text=(
                f"Adaptation Rules for {self.tenant} ({date_str}): "
                f"Boost: {boost_types}. Reduce: {reduce_types}. "
                f"Adjustments: {json.dumps(priority_adjustments)}. "
                f"Skip conditions: {skip_conditions}."
            ),
            context_id=f"adaptation-rules-{self.tenant}-{date_str}",
            concepts=["adaptation", "rules", "feedback"],
        )

        logger.info(
            "ADAPT: Boost %s, reduce %s for %s (%.0f%% effective)",
            boost_types,
            reduce_types,
            self.tenant,
            effectiveness * 100,
        )
        return adaptations

    # ------------------------------------------------------------------
    # RUN — full feedback cycle
    # ------------------------------------------------------------------

    async def run(self) -> dict[str, object]:
        """Execute the full feedback cycle: observe -> compare -> score -> learn -> adapt.

        This runs BEFORE the next decision cycle so lessons inform new tasks.
        """
        logger.info("Starting feedback loop for %s", self.tenant)

        observation = await self.observe()
        comparisons = await self.compare(observation)
        summary = await self.score(comparisons)
        await self.learn(comparisons, summary)
        adaptations = await self.adapt(summary)

        # Wire 5: Update agent DNA coherence from feedback effectiveness
        await self._update_agent_coherence(summary)

        # Wire 6: Decay conductance network (weekly cycle)
        try:
            from sos.services.health.calcifer import conductance_decay
            conductance_decay()
        except Exception as exc:
            logger.warning("Conductance decay failed (non-blocking): %s", exc)

        report: dict[str, object] = {
            "tenant": self.tenant,
            "period": observation.get("period", "unknown"),
            "summary": summary,
            "adaptations": adaptations,
            "comparisons": comparisons,
        }

        logger.info(
            "Feedback loop complete for %s: %d actions, %.0f%% effective",
            self.tenant,
            summary.get("total_actions", 0),
            float(summary.get("effectiveness", 0)) * 100,
        )
        return report

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def _update_agent_coherence(self, summary: dict[str, object]) -> None:
        """Wire 5: Update agent DNA coherence (C) from feedback effectiveness.

        Reads effectiveness from score summary, applies to each agent that
        worked on this tenant's tasks. Stores updated DNA in Mirror.
        """
        effectiveness = float(summary.get("effectiveness", 0.5))

        try:
            from sos.kernel.identity import PhysicsState, AgentDNA

            # Get agents who worked on this tenant (from comparisons or actions)
            # For now, update a tenant-level coherence score
            # TODO: per-agent coherence once we track which agent did which task
            tenant_dna_key = f"dna-{self.tenant}"

            # Load existing DNA from Mirror or create default
            dna = AgentDNA(id=tenant_dna_key, name=self.tenant)

            # Try to load previous C from Mirror
            try:
                resp = await self._client.post(
                    f"{self.mirror_url}/search",
                    json={"query": f"Agent DNA coherence for {self.tenant}", "limit": 1},
                    headers={"Authorization": f"Bearer {self.mirror_token}"},
                )
                if resp.status_code == 200:
                    results = resp.json()
                    engrams = results if isinstance(results, list) else results.get("results", [])
                    for e in engrams:
                        text = e.get("text", "")
                        if "C=" in text:
                            import re
                            c_match = re.search(r"C=(\d+\.?\d*)", text)
                            if c_match:
                                dna.physics.C = float(c_match.group(1))
            except Exception:
                pass

            # Apply feedback score
            old_c = dna.physics.C
            new_c = dna.physics.apply_feedback_score(effectiveness)

            logger.info(
                "Wire 5: %s coherence C=%.3f → %.3f (effectiveness=%.2f, regime=%s)",
                self.tenant, old_c, new_c, effectiveness, dna.physics.regime,
            )

            # Store updated C in Mirror
            try:
                await self._client.post(
                    f"{self.mirror_url}/store",
                    json={
                        "agent": "feedback-loop",
                        "context_id": f"dna-coherence-{self.tenant}",
                        "text": (
                            f"Agent DNA coherence for {self.tenant}: "
                            f"C={new_c:.4f} (was {old_c:.4f}). "
                            f"Effectiveness={effectiveness:.2f}. "
                            f"Regime={dna.physics.regime}."
                        ),
                        "project": self.tenant,
                        "core_concepts": ["coherence", "dna", "feedback"],
                    },
                    headers={"Authorization": f"Bearer {self.mirror_token}"},
                )
            except Exception as exc:
                logger.warning("Failed to store DNA coherence in Mirror: %s", exc)

        except ImportError as exc:
            logger.warning("Wire 5 import failed (non-blocking): %s", exc)
        except Exception as exc:
            logger.error("Wire 5 coherence update failed: %s", exc)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _recall(self, query_prefix: str, limit: int = 5) -> list[dict[str, str]]:
        """Query Mirror for engrams matching a prefix."""
        query = f"{query_prefix} {self.tenant}"
        try:
            resp = await self._client.post(
                f"{self.mirror_url}/search",
                json={"query": query, "limit": limit, "project": self.tenant},
                headers={"Authorization": f"Bearer {self.mirror_token}"},
            )
            resp.raise_for_status()
            results = resp.json()
            if isinstance(results, list):
                return results
            if isinstance(results, dict) and "results" in results:
                return results["results"]
            return []
        except (httpx.HTTPError, httpx.ConnectError) as exc:
            logger.warning("Mirror unavailable (%s), using mock data", exc)
            return self._mock_recall(query_prefix)

    def _mock_recall(self, query_prefix: str) -> list[dict[str, str]]:
        """Mock recall data for development when Mirror is unavailable."""
        if "Analytics" in query_prefix:
            return [
                {
                    "text": (
                        f"Weekly Analytics for {self.tenant} (2026-04-07): "
                        "GA4 Report: Total sessions (top 10): 5418. Bounce rate: 44.3%. "
                        "Page /pricing bounce rate: 62.0%. "
                        "| GSC Report: Query 'freight shipping toronto' position: 7.1. "
                        "| Clarity Report: Rage clicks on: #contact-form-submit (14)."
                    ),
                },
                {
                    "text": (
                        f"Weekly Analytics for {self.tenant} (2026-03-31): "
                        "GA4 Report: Total sessions (top 10): 4980. Bounce rate: 42.1%. "
                        "Page /pricing bounce rate: 78.2%. "
                        "| GSC Report: Query 'freight shipping toronto' position: 9.1. "
                        "| Clarity Report: Rage clicks on: #contact-form-submit (12)."
                    ),
                },
            ]
        if "Executed" in query_prefix:
            return [
                {
                    "text": (
                        f"Executed: rewrite on /pricing. "
                        "Reason: Bounce rate 78% (was 65%). Page needs content or UX improvement. "
                        "Status: executed."
                    ),
                },
                {
                    "text": (
                        f"Executed: new_content on keyword: freight shipping toronto. "
                        "Reason: Position dropped from #5 to #9. Need supporting blog post. "
                        "Status: executed."
                    ),
                },
                {
                    "text": (
                        f"Executed: fix_ux on #contact-form-submit. "
                        "Reason: Clarity: 12 rage clicks on #contact-form-submit. "
                        "Status: executed."
                    ),
                },
            ]
        return []

    def _parse_metrics(self, text: str) -> dict[str, float]:
        """Extract numeric metrics from analytics text."""
        metrics: dict[str, float] = {}

        # Total sessions
        sessions_match = re.search(r"Total sessions.*?:\s*(\d+)", text)
        if sessions_match:
            metrics["sessions"] = float(sessions_match.group(1))

        # Overall bounce rate
        bounce_match = re.search(r"Bounce rate:\s*(\d+(?:\.\d+)?)%", text)
        if bounce_match:
            metrics["bounce_rate"] = float(bounce_match.group(1))

        # Per-page bounce rates
        page_bounces = re.findall(
            r"Page\s+(\/\S+)\s+bounce rate:\s+(\d+(?:\.\d+)?)%", text
        )
        for page, rate in page_bounces:
            metrics[f"bounce_rate:{page}"] = float(rate)

        # Keyword positions
        positions = re.findall(
            r"Query\s+'([^']+)'\s+position:\s+(\d+(?:\.\d+)?)", text
        )
        for keyword, pos in positions:
            metrics[f"position:{keyword}"] = float(pos)

        # Rage clicks
        rage_matches = re.findall(r"Rage clicks on:\s*(.+?)(?:\.\s|$)", text)
        if rage_matches:
            elements = re.findall(r"([^\d,]+?)\s*\((\d+)\)", rage_matches[0])
            for element, count in elements:
                element = element.strip().rstrip(",").strip()
                metrics[f"rage_clicks:{element}"] = float(count)

        # Total clicks
        clicks_match = re.search(r"Total clicks:\s*(\d+)", text)
        if clicks_match:
            metrics["total_clicks"] = float(clicks_match.group(1))

        return metrics

    def _parse_action(self, text: str) -> Optional[dict[str, str]]:
        """Parse an action memory entry into structured data."""
        # Pattern: "Executed: {type} on {target}. Reason: {reason}. Status: {status}."
        match = re.match(
            r"Executed:\s+(\w+)\s+on\s+(.+?)\.\s+Reason:\s+(.+?)\.\s+Status:\s+(\w+)",
            text,
        )
        if match:
            return {
                "type": match.group(1),
                "target": match.group(2),
                "reason": match.group(3),
                "status": match.group(4),
            }
        return None

    def _find_relevant_metric(
        self,
        action_type: str,
        target: str,
        reason: str,
        current: dict[str, float],
        previous: dict[str, float],
    ) -> tuple[str, Optional[float], Optional[float]]:
        """Find the metric that an action was targeting.

        Returns (metric_key, before_value, after_value).
        """
        # Rewrite actions target bounce rate for the specific page
        if action_type == "rewrite":
            key = f"bounce_rate:{target}"
            if key in current and key in previous:
                return key, previous[key], current[key]
            # Fall back to overall bounce rate
            if "bounce_rate" in current and "bounce_rate" in previous:
                return "bounce_rate", previous["bounce_rate"], current["bounce_rate"]

        # New content targets keyword position
        if action_type == "new_content":
            # Extract keyword from target like "keyword: freight shipping toronto"
            keyword = target.replace("keyword: ", "").strip()
            key = f"position:{keyword}"
            if key in current and key in previous:
                return key, previous[key], current[key]

        # Fix UX targets rage clicks on the element
        if action_type == "fix_ux":
            key = f"rage_clicks:{target}"
            if key in current and key in previous:
                return key, previous[key], current[key]

        # Double down targets sessions
        if action_type == "double_down":
            if "sessions" in current and "sessions" in previous:
                return "sessions", previous["sessions"], current["sessions"]

        return "unknown", None, None

    def _determine_outcome(self, metric_key: str, delta: float) -> str:
        """Determine if a delta represents a positive, negative, or neutral outcome."""
        # Extract the base metric name (before any colon)
        base_metric = metric_key.split(":")[0]

        # For metrics where lower is better, a negative delta is good
        if base_metric in LOWER_IS_BETTER:
            if delta < -1:
                return "positive"
            if delta > 1:
                return "negative"
            return "neutral"

        # For metrics where higher is better (sessions, clicks), positive delta is good
        if delta > 1:
            return "positive"
        if delta < -1:
            return "negative"
        return "neutral"

    def _estimate_confidence(self, action_type: str, abs_delta: float) -> float:
        """Estimate confidence in the cause-effect relationship.

        Direct actions (rewrite, fix_ux) have higher confidence.
        Indirect actions (new_content, double_down) need more time.
        """
        base_confidence: dict[str, float] = {
            "rewrite": 0.8,
            "fix_ux": 0.7,
            "new_content": 0.5,
            "double_down": 0.4,
        }
        confidence = base_confidence.get(action_type, 0.5)

        # Larger deltas increase confidence slightly
        if abs_delta > 10:
            confidence = min(confidence + 0.1, 0.95)

        return round(confidence, 2)

    def _generate_lesson(
        self,
        action_type: str,
        target: str,
        outcome: str,
        delta: float,
        metric_key: str,
    ) -> str:
        """Generate a human-readable lesson from an action's outcome."""
        base_metric = metric_key.split(":")[0]
        direction = "improved" if outcome == "positive" else "worsened" if outcome == "negative" else "unchanged"

        if outcome == "positive":
            return (
                f"{action_type.replace('_', ' ').title()} on {target} worked — "
                f"{base_metric} {direction} by {abs(delta):.1f}. "
                f"This approach works for this tenant."
            )
        if outcome == "negative":
            return (
                f"{action_type.replace('_', ' ').title()} on {target} didn't work — "
                f"{base_metric} {direction} by {abs(delta):.1f}. "
                f"Need deeper investigation before repeating this type of action."
            )
        return (
            f"{action_type.replace('_', ' ').title()} on {target} had no measurable effect on {base_metric}. "
            f"May need more time or a different approach."
        )

    def _mock_comparisons(self) -> list[dict[str, object]]:
        """Mock comparison data for development."""
        return [
            {
                "action": "rewrite /pricing",
                "reason": "Bounce rate 78% (was 65%). Page needs content or UX improvement.",
                "metric_before": 78.2,
                "metric_after": 62.0,
                "delta": -16.2,
                "outcome": "positive",
                "confidence": 0.8,
                "lesson": (
                    "Rewrite on /pricing worked — bounce_rate improved by 16.2. "
                    "This approach works for this tenant."
                ),
            },
            {
                "action": "new_content keyword: freight shipping toronto",
                "reason": "Position dropped from #5 to #9. Need supporting blog post.",
                "metric_before": 9.1,
                "metric_after": 7.1,
                "delta": -2.0,
                "outcome": "positive",
                "confidence": 0.6,
                "lesson": (
                    "New Content on keyword: freight shipping toronto worked — "
                    "position improved by 2.0. This approach works for this tenant."
                ),
            },
            {
                "action": "fix_ux #contact-form-submit",
                "reason": "Clarity: 12 rage clicks on #contact-form-submit.",
                "metric_before": 12.0,
                "metric_after": 14.0,
                "delta": 2.0,
                "outcome": "negative",
                "confidence": 0.7,
                "lesson": (
                    "Fix Ux on #contact-form-submit didn't work — "
                    "rage_clicks worsened by 2.0. "
                    "Need deeper investigation before repeating this type of action."
                ),
            },
        ]

    async def _store_in_mirror(
        self,
        text: str,
        context_id: str,
        concepts: Optional[list[str]] = None,
    ) -> bool:
        """Store an engram in Mirror. Falls back to local JSON if unavailable."""
        payload = {
            "agent": "feedback-loop",
            "context_id": context_id,
            "text": text,
            "project": self.tenant,
            "core_concepts": concepts or ["feedback"],
            "metadata": {
                "source": "feedback-loop",
                "tenant": self.tenant,
                "stored_at": datetime.now(timezone.utc).isoformat(),
            },
        }

        try:
            resp = await self._client.post(
                f"{self.mirror_url}/store",
                json=payload,
                headers={"Authorization": f"Bearer {self.mirror_token}"},
            )
            resp.raise_for_status()
            return True
        except (httpx.HTTPError, httpx.ConnectError) as exc:
            logger.warning("Mirror unavailable (%s), falling back to local", exc)
            return self._store_local_fallback(text, context_id)

    def _store_local_fallback(self, text: str, context_id: str) -> bool:
        """Write feedback data to local JSON when Mirror is unreachable."""
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fallback_path = FALLBACK_DIR / self.tenant / f"feedback-{date_str}.json"
        fallback_path.parent.mkdir(parents=True, exist_ok=True)

        existing: list[dict[str, str]] = []
        if fallback_path.exists():
            try:
                existing = json.loads(fallback_path.read_text())
            except (json.JSONDecodeError, ValueError):
                existing = []

        existing.append({
            "context_id": context_id,
            "text": text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        fallback_path.write_text(json.dumps(existing, indent=2))
        logger.info("Saved locally: %s", fallback_path)
        return True
