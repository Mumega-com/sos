# Incident 2026-04-16 — SEC-001 auth regression in dashboard + mirror

**Reporter:** hadi (via sos-dev session)
**Severity:** blocker
**Time to resolve:** ~30 min
**Pipe:** dashboard + mirror
**Medic version:** 1.0.0 (seeded retroactively)

## Symptom

- No customer could log in to `app.mumega.com/dashboard` — all tokens returned 401.
- Mirror `/recent/{agent}` rejected every SOS bus token with "Invalid token".
- Dashboard memory card always showed 0 entries.

## Reproduction

```
# Dashboard
$ curl -X POST https://app.mumega.com/login -d "token=<valid-customer-token>" -i
HTTP/2 401

# Mirror
$ curl -H "Authorization: Bearer <valid-customer-token>" http://localhost:8844/recent/trop
{"detail":"Invalid token"}
```

## Root cause

- `sos/services/dashboard/app.py:44` — `_verify_token` compared raw input against `entry["token"]`. After SEC-001 (commit `f975e94e`), `tokens.json` stores hashes only; `token` field is `""` for all 62 entries.
- `/home/mumega/mirror/mirror_api.py:114` — same anti-pattern in `resolve_token`: `entry.get("token") == token` against empty strings.
- Dashboard memory card called `/engrams` + `/engrams/count` which were never real Mirror endpoints.

## Fix

- **Dashboard:** `_verify_token` now hashes input (sha256) and compares to `token_hash`; falls back to bcrypt check on `hash`. Kept raw-field fallback for unmigrated entries.
- **Dashboard `_fetch_memory`:** switched to real Mirror endpoints `/recent/{project}` and `/stats`; threads the tenant's bus token as Bearer to the Mirror call.
- **Mirror `resolve_token`:** added sha256 `token_hash` match + bcrypt `hash` match on the SOS bus token lookup.
- **Dashboard routes:** `dashboard()` and `api_status()` now pull `token` from cookie and pass to `_fetch_memory`.

## Verification

```
$ curl -X POST https://app.mumega.com/login -d "token=sk-trop-f3ad1fb355a2d2ffa408bdcb4182d50e" -i
HTTP/2 303
location: /dashboard

$ curl -b cookie.txt https://app.mumega.com/api/status
{"tenant":"The Realm of Patterns — customer","project":"trop","agents_online":0,"task_count":0,"memory_count":0}

$ curl -H "Authorization: Bearer sk-trop-f3ad1fb355a2d2ffa408bdcb4182d50e" http://localhost:8844/recent/trop
{"agent":"trop","project":"trop","count":0,"engrams":[]}
```

## Pattern class

**SEC-001 auth regression** — post-security-migration code reads legacy raw-token field that's now always empty. Recurs anywhere code hasn't been updated to use the hash-aware verifier from `sos.mcp.sos_mcp_sse` or `sos.services.squad.auth`.

## Followups

- Audit every consumer of `tokens.json` for the same anti-pattern (likely: bus bridge, any scripts).
- Calcifer should gain a logical-health probe: a known-good customer token must return 200 from `/api/status`. Open as task.
