"""Identity contract — shared shapes for identity/avatar surface.

Shared between ``sos.services.identity`` (the owner) and sibling services
that need the 16D Universal Vector without importing identity internals.

Currently exposes:

- :class:`UV16D` — the 16-dimensional Universal Vector as a pure dataclass.

Moved here as part of v0.4.5 Wave 4 (P0-07 autonomy→identity decoupling).
``sos.services.identity.avatar`` re-exports :class:`UV16D` from this module
so internal identity code keeps working unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class UV16D:
    """16D Universal Vector.

    Inner octave (personal / worldly):
        p, e, mu, v, n, delta, r, phi

    Outer octave (transpersonal, ``*t`` suffix):
        pt, et, mut, vt, nt, deltat, rt, phit

    Values are expected in ``[0.0, 1.0]`` — but the dataclass does not
    enforce the range.  Coherence is a pure computed property.
    """

    # Inner Octave
    p: float = 0.5       # Phase/Identity
    e: float = 0.5       # Existence/Worlds
    mu: float = 0.5      # Cognition/Masks
    v: float = 0.5       # Energy/Vitality
    n: float = 0.5       # Narrative/Story
    delta: float = 0.5   # Trajectory/Motion
    r: float = 0.5       # Relationality/Bonds
    phi: float = 0.5     # Field Awareness

    # Outer Octave (transpersonal)
    pt: float = 0.5
    et: float = 0.5
    mut: float = 0.5
    vt: float = 0.5
    nt: float = 0.5
    deltat: float = 0.5
    rt: float = 0.5
    phit: float = 0.5

    @property
    def coherence(self) -> float:
        """Mean of the inner octave — a quick coherence scalar."""
        inner = (
            self.p + self.e + self.mu + self.v
            + self.n + self.delta + self.r + self.phi
        ) / 8
        return inner

    @property
    def inner_octave(self) -> List[float]:
        return [self.p, self.e, self.mu, self.v, self.n, self.delta, self.r, self.phi]

    @property
    def outer_octave(self) -> List[float]:
        return [self.pt, self.et, self.mut, self.vt, self.nt, self.deltat, self.rt, self.phit]

    def to_dict(self) -> Dict[str, float]:
        return {
            "p": self.p, "e": self.e, "mu": self.mu, "v": self.v,
            "n": self.n, "delta": self.delta, "r": self.r, "phi": self.phi,
            "pt": self.pt, "et": self.et, "mut": self.mut, "vt": self.vt,
            "nt": self.nt, "deltat": self.deltat, "rt": self.rt, "phit": self.phit,
            "coherence": self.coherence,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, float]) -> "UV16D":
        return cls(
            p=d.get("p", 0.5), e=d.get("e", 0.5), mu=d.get("mu", 0.5), v=d.get("v", 0.5),
            n=d.get("n", 0.5), delta=d.get("delta", 0.5), r=d.get("r", 0.5), phi=d.get("phi", 0.5),
            pt=d.get("pt", 0.5), et=d.get("et", 0.5), mut=d.get("mut", 0.5), vt=d.get("vt", 0.5),
            nt=d.get("nt", 0.5), deltat=d.get("deltat", 0.5), rt=d.get("rt", 0.5), phit=d.get("phit", 0.5),
        )


__all__ = ["UV16D"]
