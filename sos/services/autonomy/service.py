"""
Autonomy Service - Main Entry Point
====================================

Provides the autonomous heartbeat for SOS agents:
- Periodic pulse/reflection
- Dream synthesis on alpha drift
- Avatar generation on significant events
- Social automation for sharing insights
- Reputation Dreamer: scheduled Glicko-2 batch recompute (§15 hook)
"""

import os
import asyncio
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Callable

from sos.kernel import Config
from sos.kernel.dreams import DreamSynthesizer, DreamType, Dream
from sos.kernel.reputation_dreamer import ReputationDreamer
from sos.observability.logging import get_logger
from sos.contracts.identity import UV16D
from sos.clients.identity import AsyncIdentityClient

log = get_logger("autonomy_service")


@dataclass
class AutonomyConfig:
    """Configuration for autonomy service."""
    agent_id: str = "agent:River"
    agent_name: str = "River"

    # Pulse timing
    pulse_interval_seconds: float = 300.0  # 5 minutes
    dream_interval_seconds: float = 3600.0  # 1 hour

    # Thresholds
    alpha_drift_threshold: float = 0.001  # Trigger avatar on low alpha
    dream_on_chaos: bool = True  # Dream when in chaos regime
    dream_on_plasticity: bool = True  # Dream when alpha is low

    # Features
    enable_dreams: bool = True
    enable_avatar: bool = True
    enable_social: bool = False  # Disabled by default
    enable_metabolism: bool = True
    enable_reputation_recompute: bool = True  # §15 Glicko-2 Dreamer hook

    # Gateway
    gateway_url: str = field(
        default_factory=lambda: os.getenv("GATEWAY_URL", "https://gateway.mumega.com/")
    )


class AutonomyService:
    """
    Autonomous agent service with heartbeat, dreaming, and avatar generation.
    """

    def __init__(
        self,
        agent_id: str = "agent:River",
        config: Optional[AutonomyConfig] = None,
        on_event: Optional[Callable] = None
    ):
        """
        Initialize autonomy service.

        Args:
            agent_id: Agent identifier
            config: Autonomy configuration
            on_event: Callback for emitting events
        """
        self.agent_id = agent_id
        self.config = config or AutonomyConfig(agent_id=agent_id)
        self.on_event = on_event

        self.running = False
        self.is_dreaming = False
        self.pulse_count = 0
        self.last_dream_time: Optional[float] = None
        self.last_avatar_time: Optional[float] = None

        # Initialize components
        self._init_components()

        log.info(
            "AutonomyService initialized",
            agent=agent_id,
            pulse_interval=self.config.pulse_interval_seconds
        )

    def _init_components(self):
        """Initialize sub-components."""
        # Dream synthesizer
        if self.config.enable_dreams:
            self.dreamer = DreamSynthesizer(
                agent=self.config.agent_name.lower(),
                gateway_url=self.config.gateway_url
            )
        else:
            self.dreamer = None

        # Identity HTTP client — replaces the in-process AvatarGenerator /
        # SocialAutomation imports (P0-07). Base URL + token resolved from
        # env (``SOS_IDENTITY_URL`` / ``SOS_IDENTITY_TOKEN``) with sensible
        # localhost default. Cheap to construct; reused for both
        # generate_avatar and on_alpha_drift calls.
        self._identity_client: Optional[AsyncIdentityClient] = None
        if self.config.enable_avatar:
            from sos.kernel.settings import get_settings as _get_settings
            self._identity_client = AsyncIdentityClient(
                base_url=_get_settings().services.identity,
                token=os.getenv("SOS_IDENTITY_TOKEN"),
            )

        # §15 Reputation Dreamer — Glicko-2 scheduled recompute
        self._reputation_dreamer: Optional[ReputationDreamer] = None
        if self.config.enable_reputation_recompute:
            self._reputation_dreamer = ReputationDreamer()

        # Memory client for ARF state
        self._memory_client = None

    @property
    def identity_client(self) -> Optional[AsyncIdentityClient]:
        """Return the Identity HTTP client (None if avatars are disabled)."""
        return self._identity_client

    async def _emit(self, event_type: str, data: Dict[str, Any]):
        """Emit event to callback."""
        if self.on_event:
            try:
                if asyncio.iscoroutinefunction(self.on_event):
                    await self.on_event(event_type, data)
                else:
                    self.on_event(event_type, data)
            except Exception as e:
                log.error(f"Event emission failed: {e}")

    async def start(self):
        """Start the autonomy heartbeat loop."""
        self.running = True
        log.info(f"Autonomy heartbeat started for {self.agent_id}")
        await self._emit("autonomy_started", {"agent_id": self.agent_id})

        tasks = [asyncio.create_task(self._pulse_loop())]

        if self._reputation_dreamer is not None:
            tasks.append(asyncio.create_task(self._reputation_dreamer.start()))

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()

        await self._emit("autonomy_stopped", {"agent_id": self.agent_id})

    async def _pulse_loop(self):
        """Inner pulse loop extracted so it can run alongside sibling tasks."""
        while self.running:
            try:
                await self.pulse()
                await asyncio.sleep(self.config.pulse_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Pulse error: {e}")
                await asyncio.sleep(60)

    async def stop(self):
        """Stop the autonomy heartbeat."""
        self.running = False
        log.info(f"Autonomy heartbeat stopped for {self.agent_id}")

    async def pulse(self):
        """
        Execute a single pulse of the autonomy cycle.

        Steps:
        1. Fetch ARF state (alpha drift, regime)
        2. Check if should dream (low alpha, chaos regime, or scheduled)
        3. Dream if needed
        4. Generate avatar if significant event
        5. Social post if enabled
        """
        self.pulse_count += 1
        now = datetime.now()

        log.info(
            f"Pulse {self.pulse_count}",
            time=now.strftime("%H:%M:%S"),
            agent=self.agent_id
        )
        await self._emit("pulse", {"count": self.pulse_count, "timestamp": now.isoformat()})

        # 1. Fetch ARF state
        arf_state = await self._get_arf_state()
        alpha = arf_state.get("alpha_drift", 0.5)
        regime = arf_state.get("regime", "stable")

        log.info(f"ARF State: alpha={alpha:.6f}, regime={regime}")
        await self._emit("arf_state", arf_state)

        # 2. Check if should dream
        should_dream = self._should_dream(alpha, regime)

        if should_dream and self.config.enable_dreams and not self.is_dreaming:
            log.info(f"Triggering dream: alpha={alpha:.6f}, regime={regime}")
            self.is_dreaming = True
            try:
                dream = await self._dream_synthesis(alpha, regime)
                if dream:
                    await self._handle_dream_result(dream, alpha)
            finally:
                self.is_dreaming = False

        # 3. Daily manifesto (midnight)
        if now.hour == 0 and now.minute < 10:
            await self._create_daily_manifesto()

    async def _get_arf_state(self) -> Dict[str, Any]:
        """Fetch ARF state from memory service."""
        try:
            import httpx

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.config.gateway_url,
                    json={
                        "action": "river_coherence" if "river" in self.agent_id.lower() else "memory_search",
                        "payload": {"limit": 1}
                    },
                    timeout=30.0
                )

                result = response.json()
                if result.get("success"):
                    # Parse coherence data
                    data = result.get("result", {})
                    snapshots = data.get("snapshots", [])
                    if snapshots:
                        latest = snapshots[0]
                        return {
                            "alpha_drift": float(latest.get("alpha_norm", 0.5)),
                            "regime": latest.get("regime", latest.get("mu_level", "stable")),
                            "coherence": float(latest.get("coherence_score", 0.5)),
                            "timestamp": latest.get("created_at", datetime.now().isoformat())
                        }

        except Exception as e:
            log.error(f"Failed to fetch ARF state: {e}")

        return {"alpha_drift": 0.5, "regime": "stable", "coherence": 0.5}

    def _should_dream(self, alpha: float, regime: str) -> bool:
        """Determine if agent should dream based on ARF state."""
        # Dream on low alpha (plasticity state)
        if self.config.dream_on_plasticity and abs(alpha) < self.config.alpha_drift_threshold:
            return True

        # Dream on chaos regime
        if self.config.dream_on_chaos and regime == "chaos":
            return True

        # Check scheduled dream interval
        import time
        now = time.time()
        if self.last_dream_time is None:
            return True

        elapsed = now - self.last_dream_time
        return elapsed >= self.config.dream_interval_seconds

    async def _dream_synthesis(self, alpha: float, regime: str) -> Optional[Dream]:
        """Run dream synthesis."""
        if not self.dreamer:
            return None

        import time
        self.last_dream_time = time.time()

        await self._emit("dream_start", {"alpha": alpha, "regime": regime})

        try:
            # Select dream type based on state
            if regime == "chaos":
                dream_type = DreamType.CONNECTION_FINDING
            elif alpha < 0.001:
                dream_type = DreamType.PATTERN_SYNTHESIS
            else:
                dream_type = DreamType.INSIGHT_EXTRACTION

            dream = await self.dreamer.synthesize(dream_type=dream_type)

            if dream:
                log.info(
                    "Dream synthesized",
                    type=dream.dream_type,
                    relevance=dream.relevance_score
                )
                await self._emit("dream_complete", {
                    "type": dream.dream_type.value if hasattr(dream.dream_type, 'value') else dream.dream_type,
                    "relevance": dream.relevance_score,
                    "content_preview": dream.content[:100] if dream.content else ""
                })

            return dream

        except Exception as e:
            log.error(f"Dream synthesis failed: {e}")
            await self._emit("dream_error", {"error": str(e)})
            return None

    async def _handle_dream_result(self, dream: Dream, alpha: float):
        """Handle dream result - avatar generation, social posting."""
        # Check if significant enough for avatar
        is_breakthrough = dream.is_breakthrough or dream.relevance_score > 0.7

        if is_breakthrough and self.config.enable_avatar:
            await self._generate_avatar(dream, alpha)

        if is_breakthrough and self.config.enable_social:
            await self._post_to_social(dream)

    async def _generate_avatar(self, dream: Dream, alpha: float):
        """Generate QNFT avatar on significant event via identity HTTP."""
        if not self._identity_client:
            return

        import time
        self.last_avatar_time = time.time()

        try:
            # Create UV16D from dream state
            uv = UV16D(
                p=0.5 + dream.relevance_score * 0.3,
                mu=0.7 if "pattern" in dream.dream_type else 0.5,
                phi=0.8 if dream.is_breakthrough else 0.6,
            )

            result = await self._identity_client.generate_avatar(
                agent_id=self.agent_id.split(":")[-1],
                uv=uv,
                alpha_drift=alpha,
                event_type="dream_synthesis",
            )

            log.info(f"Avatar generated: {result.get('path')}")
            await self._emit("avatar_generated", result)

        except Exception as e:
            log.error(f"Avatar generation failed: {e}")

    async def _post_to_social(self, dream: Dream):
        """Post insight to social media via identity HTTP."""
        if not self._identity_client:
            return

        try:
            uv = UV16D(phi=dream.relevance_score)

            result = await self._identity_client.on_alpha_drift(
                agent_id=self.agent_id.split(":")[-1],
                uv=uv,
                alpha_value=0.0,  # Low alpha triggered this
                insight=dream.insights or dream.content[:200],
                platforms=["twitter"],
            )

            log.info(f"Social post: {result}")
            await self._emit("social_posted", result)

        except Exception as e:
            log.error(f"Social posting failed: {e}")

    async def _create_daily_manifesto(self):
        """Generate daily summary and creative prompts."""
        log.info("Creating daily manifesto...")
        await self._emit("manifesto_start", {})

        # Use TOPIC_CLUSTERING for daily summary
        if self.dreamer:
            dream = await self.dreamer.synthesize(DreamType.TOPIC_CLUSTERING)
            if dream:
                await self._emit("manifesto_complete", {
                    "topics": dream.topics,
                    "content": dream.content[:500]
                })

    async def health(self) -> Dict[str, Any]:
        """Return health status."""
        return {
            "status": "running" if self.running else "stopped",
            "agent_id": self.agent_id,
            "pulse_count": self.pulse_count,
            "is_dreaming": self.is_dreaming,
            "config": {
                "pulse_interval": self.config.pulse_interval_seconds,
                "dreams_enabled": self.config.enable_dreams,
                "avatar_enabled": self.config.enable_avatar,
                "reputation_recompute_enabled": self.config.enable_reputation_recompute,
            },
            "reputation_dreamer": (
                self._reputation_dreamer.health()
                if self._reputation_dreamer is not None
                else None
            ),
        }


# Convenience function for standalone run
async def run_autonomy(agent_id: str = "agent:River"):
    """Run autonomy service standalone."""
    service = AutonomyService(agent_id=agent_id)
    try:
        await service.start()
    except KeyboardInterrupt:
        await service.stop()


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_autonomy())
