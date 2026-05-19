# sos-medic Experience Log

Append-only notes from resolved incidents. One line per pattern. Newest at top.

Format: `- YYYY-MM-DD | <pattern class> → <root cause class>. See incidents/<file>.`

## Patterns

- 2026-04-16 | dashboard/mirror reject valid customer token → SEC-001 regression: `entry["token"] == token` comparison against post-migration empty-raw tokens. Check all token-verifying code for the same anti-pattern. See `incidents/2026-04-16-dashboard-mirror-auth.md`.
- 2026-04-16 | `app.mumega.com/dashboard` returns marketing 404 → nginx `301 → mumega.com` instead of `proxy_pass :8090`. Always verify site-enabled config does proxy, not redirect. See `incidents/2026-04-16-app-mumega-routing.md`.

## Heuristics (the medic's accumulated instincts)

- After any security commit (`git log --grep=security`), spot-check every service that reads `tokens.json` — hash format migrations leak through consumers.
- Calcifer sees "port open" as healthy. It will not catch auth/semantic breaks. A port-up + endpoint-returns-401 state is silent.
- `nginx -t` followed by `systemctl reload nginx` is the safe path. Never `restart` unless config changes require it.
- Service in `activating (auto-restart)` state usually means port conflict. Kill the old PID with `fuser -k <port>/tcp` before retrying.
- Mirror endpoints are `/search`, `/store`, `/recent/{agent}`, `/stats` — NOT `/engrams`. Old consumers hitting `/engrams` always 404.
