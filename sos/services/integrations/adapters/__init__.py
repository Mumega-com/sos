"""Scraping-provider adapters for the growth-intel squad (Phase 7).

Each adapter is split into `trigger()` (POSTs to provider's run endpoint,
returns a run_id) and `fetch_result(run_id)` (returns the snapshot when
the run is ready, otherwise raises `NotReady`).

The SOS-side runtime polls with `asyncio.sleep` for dev/CI. In production,
Inkwell's `GenericWorkflow` (v8.3, commit 54db2fe) orchestrates the
trigger → sleep → fetch chain as durable Workflow steps.
"""

from __future__ import annotations


class NotReady(Exception):
    """Raised by `fetch_result` when a run is still running."""


from sos.services.integrations.adapters.apify import ApifyAdapter  # noqa: E402
from sos.services.integrations.adapters.brightdata import BrightDataAdapter  # noqa: E402
from sos.services.integrations.adapters.fake import FakeSnapshotAdapter  # noqa: E402

__all__ = [
    "ApifyAdapter",
    "BrightDataAdapter",
    "FakeSnapshotAdapter",
    "NotReady",
]
