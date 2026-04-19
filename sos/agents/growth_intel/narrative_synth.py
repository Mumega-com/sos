"""narrative-synth agent — cluster provider snapshots into a BrandVector."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Sequence

from sos.contracts.brand_vector import BrandVector
from sos.contracts.ports.integrations import ProviderSnapshot

_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "to", "of", "in", "for", "on",
    "with", "is", "are", "was", "were", "be", "by", "at", "from",
    "as", "it", "this", "that", "our", "your",
})


class NarrativeSynth:
    """Turn a list of ProviderSnapshots into one BrandVector.

    Keyword-overlap clustering for v0.10.1; real embeddings are flagged
    as post-v1.0 cleanup in the Phase 7 plan.
    """

    def synthesize(
        self,
        tenant: str,
        snapshots: Sequence[ProviderSnapshot],
    ) -> BrandVector:
        if not snapshots:
            return BrandVector(
                tenant=tenant,
                computed_at=datetime.now(timezone.utc),
                confidence=0.0,
            )

        keywords = Counter[str]()
        competitors = Counter[str]()
        for snap in snapshots:
            keywords.update(self._extract_keywords(snap))
            competitors.update(self._extract_competitors(snap))

        opportunity_vector = [w for w, _ in keywords.most_common(5)]
        threat_vector = [c for c, _ in competitors.most_common(5)]

        return BrandVector(
            tenant=tenant,
            computed_at=datetime.now(timezone.utc),
            tone=self._infer_tone(opportunity_vector),
            audience=[],  # filled in by human / LLM downstream, post-v1.0
            opportunity_vector=opportunity_vector,
            threat_vector=threat_vector,
            source_snapshot_ids=[self._fingerprint(s) for s in snapshots],
            confidence=min(1.0, 0.2 + 0.15 * len(snapshots)),
        )

    @staticmethod
    def _fingerprint(snap: ProviderSnapshot) -> str:
        digest = hashlib.sha256(
            f"{snap.kind}:{snap.source_id}:{snap.captured_at.isoformat()}".encode()
        ).hexdigest()
        return f"{snap.kind}:{digest[:12]}"

    @staticmethod
    def _extract_keywords(snap: ProviderSnapshot) -> list[str]:
        words: list[str] = []
        payload = snap.payload
        # GSC: top_queries [{query, impressions}] OR rows [{keys: [...]}]
        for q in payload.get("top_queries", []):
            text = q.get("query", "")
            words.extend(_tokenize(text))
        for row in payload.get("rows", []):
            if isinstance(row, dict):
                for k in row.get("keys", []) or []:
                    words.extend(_tokenize(str(k)))
                if "keyword" in row:
                    words.extend(_tokenize(str(row["keyword"])))
        # Apify competitor rows
        for item in payload.get("items", []):
            if isinstance(item, dict) and "keyword" in item:
                words.extend(_tokenize(str(item["keyword"])))
        return words

    @staticmethod
    def _extract_competitors(snap: ProviderSnapshot) -> list[str]:
        comps: list[str] = []
        for row in snap.payload.get("rows", []):
            if isinstance(row, dict) and "competitor" in row:
                comps.append(str(row["competitor"]))
        for item in snap.payload.get("items", []):
            if isinstance(item, dict) and "competitor" in item:
                comps.append(str(item["competitor"]))
        return comps

    @staticmethod
    def _infer_tone(opportunities: list[str]) -> list[str]:
        tokens = {w for phrase in opportunities for w in phrase.split()}
        tone: list[str] = []
        if tokens & {"automation", "organism", "system", "infrastructure"}:
            tone.append("technical")
        if tokens & {"brand", "story", "narrative", "voice"}:
            tone.append("narrative")
        if tokens & {"ai", "agents", "autonomous", "copilot"}:
            tone.append("futurist")
        return tone or ["neutral"]


def _tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9\-]+", text.lower())
    return [w for w in words if w not in _STOPWORDS and len(w) > 2]
