"""dossier-writer agent — render a BrandVector into a markdown Dossier."""

from __future__ import annotations

from datetime import datetime, timezone

from sos.contracts.brand_vector import BrandVector, Dossier


class DossierWriter:
    """Render BrandVector → markdown Dossier."""

    def render(self, vector: BrandVector) -> Dossier:
        rendered_at = datetime.now(timezone.utc)
        summary = self._summary(vector)
        markdown = self._markdown(vector, summary, rendered_at)
        return Dossier(
            tenant=vector.tenant,
            rendered_at=rendered_at,
            summary=summary,
            opportunities=list(vector.opportunity_vector),
            threats=list(vector.threat_vector),
            markdown=markdown,
            vector_ref={
                "tenant": vector.tenant,
                "computed_at": vector.computed_at.isoformat(),
            },
        )

    @staticmethod
    def _summary(v: BrandVector) -> str:
        if not v.opportunity_vector and not v.threat_vector:
            return (
                f"No growth signals yet for {v.tenant}. Connect GA, GSC, "
                f"BrightData, or Apify to start generating a brand vector."
            )
        opp_txt = ", ".join(v.opportunity_vector[:3]) or "(none)"
        threat_txt = ", ".join(v.threat_vector[:3]) or "(none)"
        tone_txt = "/".join(v.tone) if v.tone else "neutral"
        return (
            f"{v.tenant.title()} — tone {tone_txt}. "
            f"Top opportunities: {opp_txt}. "
            f"Watch: {threat_txt}. "
            f"Confidence {v.confidence:.2f}."
        )

    @staticmethod
    def _markdown(v: BrandVector, summary: str, rendered_at: datetime) -> str:
        lines = [
            f"# Brand Vector — {v.tenant}",
            "",
            f"_Rendered {rendered_at.isoformat()}  _  |  _{len(v.source_snapshot_ids)} source snapshots_",
            "",
            "## Summary",
            summary,
            "",
            "## Opportunities",
        ]
        lines.extend(f"- {o}" for o in v.opportunity_vector) if v.opportunity_vector else lines.append(
            "_None yet._"
        )
        lines.extend(["", "## Threats"])
        lines.extend(f"- {t}" for t in v.threat_vector) if v.threat_vector else lines.append("_None yet._")
        lines.extend(["", "## Tone", ", ".join(v.tone) if v.tone else "_unknown_"])
        return "\n".join(lines)
