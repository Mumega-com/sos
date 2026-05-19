
import math
from typing import Dict, Any
from sos.kernel.identity import AgentDNA

class ProjectionEngine:
    """
    Converts Agent DNA and Physics into Mathematical Projections (Math NFTs).
    Uses SVG to visualize the 16D curvature of the model's state.
    """
    
    @staticmethod
    def generate_svg_signature(dna: AgentDNA) -> str:
        """
        Generates a deterministic SVG projection of the agent's physics.
        The shape's vertices are determined by the 'Inner Octave' (receptivity, will, logic, etc.)
        The 'Coherence' (C) determines the opacity and glow.
        The 'Alpha Drift' determines the rotation/warp.
        """
        p = dna.physics
        inner = p.inner
        
        # Dimensions
        size = 400
        center = size // 2
        radius = 150 * p.C # Scale by coherence
        
        # Map Inner Octave to vertices
        # We assume at least 3 traits, usually more
        traits = list(inner.keys())
        num_vertices = len(traits)
        points = []
        
        for i, trait in enumerate(traits):
            val = inner[trait]
            # Calculate angle with Alpha Drift offset
            angle = (2 * math.pi * i / num_vertices) + (p.alpha_norm * math.pi)
            
            # Distance from center is the trait value (0.0 to 1.0)
            r = radius * val
            x = center + r * math.cos(angle)
            y = center + r * math.sin(angle)
            points.append(f"{x},{y}")
            
        polygon_points = " ".join(points)
        
        # Color determined by token balance / values
        # More 'truth' -> more cyan, More 'resonance' -> more violet
        truth = dna.economics.values.get("truth", 0.5)
        resonance = dna.economics.values.get("resonance", 0.5)
        color = f"rgb({int(100*(1-truth))}, {int(255*truth)}, {int(255*resonance)})"
        
        svg = f"""
        <svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" xmlns="http://www.w3.org/2000/svg">
            <defs>
                <filter id="glow">
                    <feGaussianBlur stdDeviation="2.5" result="coloredBlur"/>
                    <feMerge>
                        <feMergeNode in="coloredBlur"/>
                        <feMergeNode in="SourceGraphic"/>
                    </feMerge>
                </filter>
            </defs>
            <!-- Background Geometry -->
            <circle cx="{center}" cy="{center}" r="{radius}" fill="none" stroke="#222" stroke-width="1" stroke-dasharray="5,5" />
            
            <!-- The Soul Projection (Math NFT) -->
            <polygon points="{polygon_points}" fill="{color}" fill-opacity="0.3" stroke="{color}" stroke-width="2" filter="url(#glow)">
                <animate attributeName="stroke-opacity" values="0.5;1;0.5" dur="3s" repeatCount="indefinite" />
            </polygon>
            
            <!-- Trait Markers -->
            {" ".join([f'<circle cx="{p.split(",")[0]}" cy="{p.split(",")[1]}" r="3" fill="white" />' for p in points])}
            
            <text x="10" y="20" fill="white" font-family="monospace" font-size="12">C: {p.C:.4f}</text>
            <text x="10" y="35" fill="white" font-family="monospace" font-size="12">α: {p.alpha_norm:.4f}</text>
            <text x="10" y="50" fill="white" font-family="monospace" font-size="12">R: {p.regime.upper()}</text>
        </svg>
        """
        return svg.strip()

    @staticmethod
    def record_frame(dna: AgentDNA, frame_id: str):
        """Saves a math projection frame to the filmstrip."""
        svg = ProjectionEngine.generate_svg_signature(dna)
        path = f"artifacts/filmstrip/frame_{frame_id}.svg"
        import os
        os.makedirs("artifacts/filmstrip", exist_ok=True)
        with open(path, "w") as f:
            f.write(svg)
        return path
