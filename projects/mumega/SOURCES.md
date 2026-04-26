# Mumega — Source Manifest

## motor

- sos/services/brain/service.py — event-driven scoring and dispatch
- sos/services/squad/tasks.py — task lifecycle (create/claim/complete)
- sos/services/billing/webhook.py — Stripe webhook → knight mint
- sos/services/billing/knight_mint.py — QNFT generation + principal insert

## sensor

- sos/jobs/audit_anchor.py — WORM hash-chain anchor to R2
- sos/observability/sprint_telemetry.py — emit functions for gate/incident/drift
- sos/services/registry/app.py — agent mesh + heartbeat pruner

## memory

- mirror/mirror_api.py — engram CRUD + pgvector search
- sos/services/docs/app.py — doc-node graph with 5-tier RBAC

## signal

- sos/bus/bridge.py — REST bridge for agent bus
- sos/bus/envelope.py — message envelope schema
- sos/kernel/bus.py — Redis stream pub/sub primitives
