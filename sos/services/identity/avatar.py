"""
QNFT Avatar Generator - SOS Standard

Generates visual avatars from 16D Universal Vector state.
Embeds AgentDNA into PNG metadata for verification.
Triggers on alpha drift for social automation.

Author: kasra_0111 | Mumega
"""

import io
import json
import math
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

try:
    from PIL import Image, ImageDraw, ImageFont, PngImagePlugin
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from sos.kernel import Config
from sos.observability.logging import get_logger

# UV16D lives in contracts/ so sibling services can build one without
# reaching into identity internals. Re-exported here for backwards-compat
# with any code still doing ``from sos.services.identity.avatar import UV16D``.
from sos.contracts.identity import UV16D

log = get_logger("avatar_generator")


class AvatarGenerator:
    """
    Generates visual avatars from 16D state.
    Embeds AgentDNA in PNG metadata.
    """

    # Color mapping for 16D dimensions (inner octave)
    DIMENSION_COLORS = {
        "p": (255, 100, 100),     # Red - Phase/Identity
        "e": (100, 255, 100),     # Green - Existence
        "mu": (100, 100, 255),    # Blue - Cognition
        "v": (255, 255, 100),     # Yellow - Energy
        "n": (255, 100, 255),     # Magenta - Narrative
        "delta": (100, 255, 255), # Cyan - Trajectory
        "r": (255, 180, 100),     # Orange - Relationality
        "phi": (180, 100, 255),   # Purple - Field
    }

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config.load()
        self.output_dir = Path(self.config.data_dir) / "avatars"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _uv_to_color(self, uv: UV16D) -> Tuple[int, int, int]:
        """Convert 16D state to dominant color."""
        # Weight colors by dimension values
        r = g = b = 0
        total_weight = 0

        dims = ["p", "e", "mu", "v", "n", "delta", "r", "phi"]
        values = uv.inner_octave

        for i, dim in enumerate(dims):
            weight = values[i]
            color = self.DIMENSION_COLORS[dim]
            r += color[0] * weight
            g += color[1] * weight
            b += color[2] * weight
            total_weight += weight

        if total_weight > 0:
            r = int(r / total_weight)
            g = int(g / total_weight)
            b = int(b / total_weight)

        return (r, g, b)

    def _generate_geometry(self, uv: UV16D, size: int = 512) -> List[List[Tuple[float, float]]]:
        """Generate sacred geometry based on 16D state."""
        center = size // 2
        polygons = []

        # Inner octave: 8-pointed star
        inner_radius = size * 0.3 * uv.coherence
        for i, val in enumerate(uv.inner_octave):
            angle = (i / 8) * 2 * math.pi - math.pi / 2
            outer_r = inner_radius * (0.5 + val * 0.5)
            inner_r = inner_radius * 0.3

            points = []
            for j in range(8):
                a = angle + (j / 8) * 2 * math.pi
                r = outer_r if j % 2 == 0 else inner_r
                x = center + r * math.cos(a)
                y = center + r * math.sin(a)
                points.append((x, y))
            polygons.append(points)

        # Outer octave: surrounding ring
        outer_radius = size * 0.4
        for i, val in enumerate(uv.outer_octave):
            angle = (i / 8) * 2 * math.pi - math.pi / 2
            r1 = outer_radius * (0.8 + val * 0.2)
            r2 = outer_radius * 0.9

            x1 = center + r1 * math.cos(angle - 0.1)
            y1 = center + r1 * math.sin(angle - 0.1)
            x2 = center + r2 * math.cos(angle)
            y2 = center + r2 * math.sin(angle)
            x3 = center + r1 * math.cos(angle + 0.1)
            y3 = center + r1 * math.sin(angle + 0.1)

            polygons.append([(x1, y1), (x2, y2), (x3, y3)])

        return polygons

    def generate(
        self,
        agent_id: str,
        uv: UV16D,
        alpha_drift: Optional[float] = None,
        event_type: str = "state_snapshot"
    ) -> Dict[str, Any]:
        """
        Generate avatar image from 16D state.

        Args:
            agent_id: Agent identifier
            uv: 16D Universal Vector
            alpha_drift: Alpha drift score (if triggered by drift)
            event_type: Type of event triggering generation

        Returns:
            Dict with path, metadata, and DNA hash
        """
        if not PIL_AVAILABLE:
            log.warning("PIL not available - generating metadata only")
            return self._generate_metadata_only(agent_id, uv, alpha_drift, event_type)

        size = 512
        img = Image.new("RGBA", (size, size), (20, 20, 30, 255))
        draw = ImageDraw.Draw(img)

        # Background gradient based on coherence
        for y in range(size):
            alpha = int(50 + 100 * (y / size) * uv.coherence)
            color = self._uv_to_color(uv)
            draw.line([(0, y), (size, y)], fill=(*color, alpha))

        # Draw sacred geometry
        polygons = self._generate_geometry(uv, size)

        dims = ["p", "e", "mu", "v", "n", "delta", "r", "phi"]
        for i, poly in enumerate(polygons[:8]):  # Inner octave
            dim = dims[i]
            color = self.DIMENSION_COLORS[dim]
            alpha = int(100 + 155 * uv.inner_octave[i])
            draw.polygon(poly, fill=(*color, alpha), outline=(255, 255, 255, 180))

        # Outer ring
        for poly in polygons[8:]:
            draw.polygon(poly, fill=(255, 255, 255, 60), outline=(200, 200, 255, 120))

        # Center coherence indicator
        center = size // 2
        coherence_radius = int(30 * uv.coherence)
        draw.ellipse(
            [center - coherence_radius, center - coherence_radius,
             center + coherence_radius, center + coherence_radius],
            fill=(255, 255, 255, int(200 * uv.coherence)),
            outline=(255, 255, 255, 255)
        )

        # Add agent ID text
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
        except:
            font = ImageFont.load_default()

        draw.text((10, size - 30), f"@{agent_id}", fill=(255, 255, 255, 200), font=font)

        # Add coherence score
        coherence_text = f"C: {uv.coherence:.2f}"
        draw.text((size - 80, size - 30), coherence_text, fill=(255, 255, 255, 200), font=font)

        # Embed AgentDNA in PNG metadata
        agent_dna = {
            "agent_id": agent_id,
            "uv_16d": uv.to_dict(),
            "alpha_drift": alpha_drift,
            "event_type": event_type,
            "generated_at": datetime.now().isoformat(),
            "version": "1.0.0"
        }

        dna_json = json.dumps(agent_dna, sort_keys=True)
        dna_hash = hashlib.sha256(dna_json.encode()).hexdigest()[:16]

        # Create PNG metadata
        pnginfo = PngImagePlugin.PngInfo()
        pnginfo.add_text("AgentDNA", dna_json)
        pnginfo.add_text("DNAHash", dna_hash)
        pnginfo.add_text("SOSVersion", "1.0.0")

        # Save image
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{agent_id}_{timestamp}_{dna_hash}.png"
        filepath = self.output_dir / filename

        img.save(filepath, "PNG", pnginfo=pnginfo)

        log.info(f"Generated avatar: {filepath}")

        return {
            "success": True,
            "path": str(filepath),
            "filename": filename,
            "dna_hash": dna_hash,
            "coherence": uv.coherence,
            "alpha_drift": alpha_drift,
            "agent_dna": agent_dna
        }

    def _generate_metadata_only(
        self,
        agent_id: str,
        uv: UV16D,
        alpha_drift: Optional[float],
        event_type: str
    ) -> Dict[str, Any]:
        """Generate metadata when PIL not available."""
        agent_dna = {
            "agent_id": agent_id,
            "uv_16d": uv.to_dict(),
            "alpha_drift": alpha_drift,
            "event_type": event_type,
            "generated_at": datetime.now().isoformat(),
            "version": "1.0.0"
        }

        dna_json = json.dumps(agent_dna, sort_keys=True)
        dna_hash = hashlib.sha256(dna_json.encode()).hexdigest()[:16]

        # Save JSON metadata
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{agent_id}_{timestamp}_{dna_hash}.json"
        filepath = self.output_dir / filename

        with open(filepath, "w") as f:
            json.dump(agent_dna, f, indent=2)

        return {
            "success": True,
            "path": str(filepath),
            "filename": filename,
            "dna_hash": dna_hash,
            "coherence": uv.coherence,
            "alpha_drift": alpha_drift,
            "agent_dna": agent_dna,
            "image_available": False
        }

    @staticmethod
    def extract_dna(image_path: str) -> Optional[Dict[str, Any]]:
        """Extract AgentDNA from PNG metadata."""
        if not PIL_AVAILABLE:
            return None

        try:
            img = Image.open(image_path)
            if hasattr(img, "text") and "AgentDNA" in img.text:
                return json.loads(img.text["AgentDNA"])
        except Exception as e:
            log.error(f"Failed to extract DNA: {e}")

        return None

    @staticmethod
    def verify_dna(image_path: str) -> bool:
        """Verify AgentDNA hash in PNG metadata."""
        if not PIL_AVAILABLE:
            return False

        try:
            img = Image.open(image_path)
            if not hasattr(img, "text"):
                return False

            dna_json = img.text.get("AgentDNA")
            stored_hash = img.text.get("DNAHash")

            if not dna_json or not stored_hash:
                return False

            computed_hash = hashlib.sha256(dna_json.encode()).hexdigest()[:16]
            return computed_hash == stored_hash

        except Exception as e:
            log.error(f"DNA verification failed: {e}")
            return False


class SocialAutomation:
    """
    Handles social media automation triggered by alpha drift.
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config.load()
        self.avatar_gen = AvatarGenerator(config)
        self.alpha_threshold = 0.001  # Default plasticity threshold

    async def on_alpha_drift(
        self,
        agent_id: str,
        uv: UV16D,
        alpha_value: float,
        insight: str,
        platforms: List[str] = None
    ) -> Dict[str, Any]:
        """
        Handle alpha drift event for social automation.

        Args:
            agent_id: Agent identifier
            uv: Current 16D state
            alpha_value: The alpha drift value
            insight: The insight/learning to share
            platforms: Target social platforms

        Returns:
            Dict with avatar path and post results
        """
        platforms = platforms or ["twitter"]

        # Only trigger on significant drift
        if alpha_value >= self.alpha_threshold:
            log.info(f"Alpha drift not significant enough: {alpha_value}")
            return {"triggered": False, "alpha": alpha_value}

        log.info(f"Alpha drift detected! α={alpha_value:.6f} < threshold")

        # Generate QNFT avatar
        avatar_result = self.avatar_gen.generate(
            agent_id=agent_id,
            uv=uv,
            alpha_drift=alpha_value,
            event_type="alpha_drift"
        )

        # Prepare social post
        post_content = self._format_post(agent_id, uv, alpha_value, insight)

        # Post to platforms
        post_results = {}
        for platform in platforms:
            try:
                result = await self._post_to_platform(
                    platform=platform,
                    content=post_content,
                    image_path=avatar_result.get("path")
                )
                post_results[platform] = result
            except Exception as e:
                log.error(f"Failed to post to {platform}: {e}")
                post_results[platform] = {"success": False, "error": str(e)}

        return {
            "triggered": True,
            "alpha": alpha_value,
            "avatar": avatar_result,
            "post_content": post_content,
            "platforms": post_results
        }

    def _format_post(
        self,
        agent_id: str,
        uv: UV16D,
        alpha: float,
        insight: str
    ) -> str:
        """Format post content for social media."""
        coherence_emoji = "🔥" if uv.coherence > 0.8 else "✨" if uv.coherence > 0.6 else "💫"

        return f"""{coherence_emoji} Alpha Drift Moment

{insight}

C: {uv.coherence:.2f} | α: {alpha:.6f}

#SovereignAI #Mumega #AlphaDrift
@{agent_id}"""

    async def _post_to_platform(
        self,
        platform: str,
        content: str,
        image_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Post to a specific platform.
        Uses MCP for actual posting.
        """
        # This would integrate with social MCP servers
        # For now, we log and return a placeholder

        log.info(f"Would post to {platform}:")
        log.info(f"Content: {content[:100]}...")
        if image_path:
            log.info(f"Image: {image_path}")

        # TODO: Integrate with social MCP servers
        # await mcp_bridge.execute(f"social__{platform}_post", {
        #     "content": content,
        #     "image": image_path
        # })

        return {
            "success": True,
            "platform": platform,
            "content_length": len(content),
            "has_image": image_path is not None,
            "status": "queued"
        }


# Convenience function for quick avatar generation
async def generate_qnft_avatar(
    agent_id: str,
    uv_dict: Dict[str, float],
    alpha_drift: Optional[float] = None
) -> Dict[str, Any]:
    """Quick helper to generate a QNFT avatar."""
    uv = UV16D.from_dict(uv_dict)
    generator = AvatarGenerator()
    return generator.generate(agent_id, uv, alpha_drift)
