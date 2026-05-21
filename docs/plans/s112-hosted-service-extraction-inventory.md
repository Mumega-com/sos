# S112 Hosted Service Extraction Inventory

Date: 2026-05-21
Status: first pass

## Purpose

This inventory separates public SOS kernel surfaces from hosted application
surfaces that belong in operator overlays. It is intentionally conservative:
only the service-map asset is changed in S112. Larger service families remain
documented until their contracts and live launch paths are proven separately.

## Categories

- A: move to operator overlay now, or make the public path an overlay hook.
- B: keep public, but make generic or configurable.
- C: leave for a later sprint after dependency and test impact are clearer.

## Inventory

| Path | Category | Reason | S112 action |
|---|---:|---|---|
| `sos/services/dashboard/` | C | Mixed operator diagnostics, hosted UI, route templates, and legacy compatibility paths. Needs sub-route extraction rather than a directory move. | Inventory only, with one asset extracted below. |
| `sos/services/dashboard/service_map.svg` | A | The previous SVG encoded hosted topology, product-layer labels, and commercial boundary claims. | Replaced with a generic public-kernel map. Added `SOS_SERVICE_MAP_SVG_PATH` so operators can mount their own topology without changing kernel code. |
| `sos/services/dashboard/templates/sos_operator.py` | B | Operator dashboard is useful, but hardcoded service-map loading made hosted topology part of the public package. | Kept public route; made service-map path configurable and cache per configured path. |
| `sos/services/dashboard/routes/marketplace.py` | C | Skill-market routes are application/product surface, but route extraction can affect dashboard imports and templates. | Leave for a later dashboard extraction sprint. |
| `sos/services/dashboard/routes/customer.py` | C | Cookie dashboard and project summary are hosted app UX; still coupled to auth/session tests. | Leave for later. |
| `sos/services/dashboard/routes/sos_mesh.py` | C | Useful operator view, but still reads registry cards directly. Existing import-linter ignore tracks the debt. | Leave for later. |
| `sos/services/dashboard/routes/bus.py` | B | DLQ read-only diagnostics are generic operator functionality, though the route imports bus DLQ helpers directly. | No change. Keep under service-boundary debt until a kernel/client helper exists. |
| `sos/services/dashboard/routes/brain.py` | B | Brain snapshot viewer is generic diagnostics if the snapshot contract remains public. | No change. |
| `sos/services/dashboard/routes/traces.py` | B | Audit trace viewer is generic operator diagnostics. | No change. |
| `sos/services/economy/` | C | Contains both kernel-adjacent ledger primitives and hosted commerce/payment flows. Needs a contract-level split, not a bulk move. | Leave for later. |
| `sos/services/analytics/` | C | Analytics ingestion/decision agents are operator add-ons, but tests already cover decoupling. Needs a separate add-on migration. | Leave for later. |
| `sos/services/atelier/` | C | Static visualization app appears hosted/demo oriented. Needs reachability check before removal. | Leave for later. |
| `sos/services/glass/` | C | Glass powers dashboard tiles and public contracts/tests. Needs a separate decision: keep as generic tile service or move as hosted app layer. | Leave for later. |
| `sos/docs/ux/dashboard-design.md` | A | References `mumega.com/sos/dashboard` and hosted web routes, not public kernel docs. | Documented as a future docs move; no file move in S112. |
| `sos/docs/ux/mumega-web-reference.md` | A | Mumega web reference belongs with the Mumega host overlay. | Documented as a future docs move; no file move in S112. |

## Result

S112 extracts the hosted service-map asset without changing production route
semantics. Public SOS now ships a generic map by default. A hosted deployment
can set `SOS_SERVICE_MAP_SVG_PATH=/path/to/service_map.svg` to restore its own
topology.

## Follow-Up Candidates

1. Move dashboard marketplace routes/templates to the host overlay behind a
   route mount.
2. Split `sos/services/economy` into public ledger primitives and hosted
   commerce/payment overlays.
3. Decide whether Glass is a public generic tile service or a hosted dashboard
   layer.
4. Move `sos/docs/ux/*mumega*` docs into the Mumega repo.
