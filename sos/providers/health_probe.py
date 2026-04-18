"""Health probe scaffold for SOS provider matrix.

Probes each ProviderCard to determine reachability and latency.
Not wired to cron yet — run manually:

    python -m sos.providers.health_probe

"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from sos.providers.matrix import ProviderCard, load_matrix


async def probe(card: ProviderCard) -> tuple[bool, int]:
    """Probe a single provider.

    Returns (is_healthy, latency_ms).

    Strategy:
    - If health_probe_url is set: HTTP HEAD with 3s timeout.
    - Otherwise: delegate to the adapter's health_check() — or return
      (True, 0) as optimistic default when no adapter is wired.
    """
    start = time.monotonic()

    if card.health_probe_url:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.head(card.health_probe_url)
            latency_ms = int((time.monotonic() - start) * 1000)
            return resp.status_code < 500, latency_ms
        except Exception:
            latency_ms = int((time.monotonic() - start) * 1000)
            return False, latency_ms

    # No probe URL — attempt adapter health_check if resolvable
    try:
        adapter = _resolve_adapter(card)
        if adapter is not None:
            healthy = await adapter.health_check()
            latency_ms = int((time.monotonic() - start) * 1000)
            return healthy, latency_ms
    except Exception:
        pass

    # Optimistic default: assume healthy, zero latency (no probe available)
    return True, 0


def _resolve_adapter(card: ProviderCard):  # type: ignore[return]
    """Attempt to import and instantiate the adapter for a card.

    Returns None if the adapter isn't available in this environment.
    """
    try:
        if card.backend == "claude-adapter":
            from sos.adapters.claude_adapter import ClaudeAdapter  # type: ignore[import]

            return ClaudeAdapter()
        if card.backend == "gemini-adapter":
            from sos.adapters.gemini_adapter import GeminiAdapter  # type: ignore[import]

            return GeminiAdapter()
        if card.backend == "openai-adapter":
            from sos.adapters.openai_adapter import OpenAIAdapter  # type: ignore[import]

            return OpenAIAdapter()
    except ImportError:
        pass
    return None


async def probe_all(matrix: list[ProviderCard]) -> dict[str, tuple[bool, int]]:
    """Probe all providers concurrently.

    Returns mapping of provider_id → (is_healthy, latency_ms).
    """
    results = await asyncio.gather(*[probe(card) for card in matrix], return_exceptions=True)
    out: dict[str, tuple[bool, int]] = {}
    for card, result in zip(matrix, results):
        if isinstance(result, Exception):
            out[card.id] = (False, 0)
        else:
            out[card.id] = result  # type: ignore[assignment]
    return out


async def _main() -> None:
    matrix = load_matrix()
    results = await probe_all(matrix)

    col_id = max(len(c.id) for c in matrix) + 2
    col_name = max(len(c.name) for c in matrix) + 2
    header = f"{'ID':<{col_id}} {'NAME':<{col_name}} {'HEALTHY':<9} {'LATENCY_MS'}"
    print(header)
    print("-" * len(header))
    for card in matrix:
        healthy, latency = results[card.id]
        status = "yes" if healthy else "NO"
        print(f"{card.id:<{col_id}} {card.name:<{col_name}} {status:<9} {latency}")


if __name__ == "__main__":
    asyncio.run(_main())
