# Public Route Residual-Risk Checklist

Use this checklist before adding or exposing any new public SOS route.

## Classification

- [ ] Route is listed in `docs/security/public-edge-map.md`.
- [ ] Exposure is classified as public minimal, public authenticated, private,
      optional public webhook, or static.
- [ ] The upstream service owner is named.
- [ ] The route has a clear reason to be public.

## Authentication And Authorization

- [ ] Mutation routes require bearer auth, signed webhook auth, or an explicit
      public-write design review.
- [ ] Token scope is enforced server-side; query params cannot widen scope.
- [ ] System/admin-only routes reject tenant-scoped tokens.
- [ ] Prototype mint/register routes are private unless reviewed.

## Data Disclosure

- [ ] Unauthenticated responses contain no tenant, customer, token, queue,
      filesystem, or raw exception detail.
- [ ] Health routes follow `docs/security/threat-model.md`.
- [ ] Error messages are stable and do not leak secrets.

## Webhooks

- [ ] Missing signing secret fails closed.
- [ ] Invalid signature returns `401` or `403`.
- [ ] Replay/idempotency key is stored before side effects when provider event
      ids exist.
- [ ] Payload size limits and schema validation are in place.

## Browser And Edge

- [ ] CORS origin is explicit for credentialed routes.
- [ ] TLS is required before tokens cross the network.
- [ ] Edge logs redact `Authorization`, path tokens, webhook signatures, and
      secret headers.
- [ ] Rate limits and request body limits exist at the public edge.

## Runtime Dependencies

- [ ] Redis is private, authenticated, and TLS/private-network protected in
      production.
- [ ] Optional dependencies fail disabled or degraded, not open.
- [ ] `sos doctor` or a release gate can detect the unsafe configuration.
