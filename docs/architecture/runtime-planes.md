# Runtime Planes

SOS is easiest to understand as six runtime planes. Public SOS owns the shared
kernel pieces; host overlays can add product-specific services without changing
the kernel.

## MCP Tool Plane

The MCP server is the primary agent interface. It exposes tools such as send,
inbox, peers, broadcast, status, task create/list/update, and optional memory
tools when Mirror is configured.

## Bus Postal Plane

The bus is Redis-backed message delivery. It carries direct messages,
broadcasts, inbox reads, stream cursors, and wake-compatible events.

## Squad Labor Plane

The Squad service provides task primitives: create, list, claim/update, and
completion flows. The public promise is coordination, not a bundled business
workflow.

## Gateway And Ingress Plane

Gateway and webhook routes are integration surfaces. They require explicit
public-edge review before exposure. S080 owns the public route map, health
policy, CORS posture, and webhook fail-closed rules.

## Mirror Memory Plane

Mirror is optional and lives in a separate public repo. SOS should degrade
gracefully without it. Memory tools only become fully useful when Mirror is
configured.

## Engine Inference Plane

The engine routes model work and supports multi-model operation. It is useful
for richer deployments but not required for the bus, inbox, or task substrate.

## Host Overlay Boundary

Host overlays such as `mumega_sos_addons` may add tenant-specific services,
provider integrations, billing flows, and deployment glue. Those overlays must
not be required for a clean public SOS clone to import, test, or run its core
bus/task surfaces.

The host profile contract is documented in `docs/architecture/plugin-boundary.md`.
OpenClaw, Hermes, and similar runtimes should integrate through public SDK,
MCP, bus-watch, Squad, and optional Mirror interfaces rather than importing
private host modules.
