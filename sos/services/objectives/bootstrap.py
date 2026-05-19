"""Bootstrap the canonical /reviews/ subtree in the objectives tree.

This module restores the structural primitive that was represented as columns
(review_enabled / review_agent / review_cmd / reviewer_notes) and dropped in
commit 742d307e (v0.6.2 "chore(db): v0.6.2 — drop 6 legacy squad columns").
Reviews are nodes, not rows.

Idempotent: calling ``bootstrap_reviews_subtree`` twice is safe — the second
call is a no-op if the root node already exists.
"""
from __future__ import annotations

import logging

from sos.contracts.objective import Objective
from sos.services.objectives import read_objective, write_objective

logger = logging.getLogger("sos.objectives.bootstrap")

# ---------------------------------------------------------------------------
# Canonical stable IDs — NOT ULIDs, NOT random.  These are load-bearing
# identifiers that docs, future objectives, and migration scripts reference.
# ---------------------------------------------------------------------------

_ROOT_ID = "reviews-primitive"
_LEAF_PROTOCOL_ID = "review-protocol-drafted"
_LEAF_MIGRATION_ID = "review-gate-migrated-from-columns"


def bootstrap_reviews_subtree(*, project: str | None = None) -> None:
    """Idempotent: on first call, create the canonical /reviews/ root + leaves.

    If the root already exists, do nothing.

    Parameters
    ----------
    project:
        Project scope to write into.  Defaults to None (the ``"default"``
        bucket in the storage layer).

    Notes
    -----
    Uses ``read_objective`` + ``write_objective`` from the storage layer.
    Storage errors are logged as warnings and swallowed — bootstrap MUST NOT
    prevent the service from starting.
    """
    try:
        existing = read_objective(_ROOT_ID, project=project)
        if existing is not None:
            logger.info(
                "bootstrap_reviews_subtree: root '%s' already exists — skipping",
                _ROOT_ID,
            )
            return
    except Exception as exc:
        logger.warning(
            "bootstrap_reviews_subtree: could not check root existence: %s — aborting",
            exc,
        )
        return

    now = Objective.now_iso()

    # ------------------------------------------------------------------
    # 1. Root node
    # ------------------------------------------------------------------
    root = Objective(
        id=_ROOT_ID,
        parent_id=None,
        title="Reviews — structural primitive",
        description=(
            "The /reviews/ subtree is the v0.8.0 replacement for the "
            "review_enabled / review_agent / review_cmd / reviewer_notes columns "
            "dropped in commit 742d307e (v0.6.2). Reviews are nodes, not rows."
        ),
        bounty_mind=0,
        state="open",
        tags=["root", "canonical", "reviews"],
        created_by="system",
        created_at=now,
        updated_at=now,
        tenant_id="default",
        project=project,
    )

    # ------------------------------------------------------------------
    # 2. Leaf: review protocol spec
    # ------------------------------------------------------------------
    leaf_protocol = Objective(
        id=_LEAF_PROTOCOL_ID,
        parent_id=_ROOT_ID,
        title="Draft the review protocol (how a review happens on a node)",
        description=(
            "Define the acks-to-paid flow, required peer count, "
            "parent-holder override, and escalation path."
        ),
        bounty_mind=100,
        state="open",
        tags=["review", "spec"],
        capabilities_required=["writing", "protocol-design"],
        created_by="system",
        created_at=now,
        updated_at=now,
        tenant_id="default",
        project=project,
    )

    # ------------------------------------------------------------------
    # 3. Leaf: migration tracking node
    # ------------------------------------------------------------------
    leaf_migration = Objective(
        id=_LEAF_MIGRATION_ID,
        parent_id=_ROOT_ID,
        title="Migrate from v0.6.2-dropped review_* columns to node-based reviews",
        description=(
            "The review_enabled / review_agent / review_cmd (on pipeline_specs) and "
            "reviewer_notes (on pipeline_runs) columns were dropped in commit 742d307e. "
            "This node tracks the migration to the node-based replacement."
        ),
        bounty_mind=200,
        state="open",
        tags=["review", "migration", "v0.6.2"],
        capabilities_required=["python", "alembic"],
        created_by="system",
        created_at=now,
        updated_at=now,
        tenant_id="default",
        project=project,
    )

    # ------------------------------------------------------------------
    # Write all three — fail-soft per node
    # ------------------------------------------------------------------
    for node in (root, leaf_protocol, leaf_migration):
        try:
            write_objective(node)
            logger.info(
                "bootstrap_reviews_subtree: created objective '%s'", node.id
            )
        except Exception as exc:
            logger.warning(
                "bootstrap_reviews_subtree: failed to write '%s': %s", node.id, exc
            )
