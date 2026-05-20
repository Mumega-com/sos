# Dashboard Service Migration Plan

Date: 2026-05-20
Scope: S088-A dashboard migration preparation only. Do not apply live until
Hadi reviews.

## Current Live State

`dashboard.service` is a user systemd unit running from the internal checkout:

```ini
[Service]
EnvironmentFile=/home/mumega/.env.secrets
Type=simple
WorkingDirectory=/home/mumega/SOS
ExecStart=/usr/bin/python3 -m sos.services.dashboard
Restart=always
RestartSec=5
```

The service is healthy on port `8090`:

- `GET /health` returns `200`.
- `GET /api/status` returns `401` without a dashboard cookie.
- `GET /sos/api/health` returns `200`.

## Public-Kernel Compatibility Pins

The live dashboard historically uses:

- cookie name: `mum_dash`
- token registry: `/mnt/HC_Volume_104325311/SOS/sos/bus/tokens.json`

The public kernel defaults differ unless pinned. S088 added these pins to
`/home/mumega/.env.secrets`:

```env
SOS_DASHBOARD_COOKIE_NAME=mum_dash
SOS_BUS_TOKENS_PATH=/mnt/HC_Volume_104325311/SOS/sos/bus/tokens.json
```

With those env values loaded, the public kernel resolves the same cookie name
and token registry.

## Verification Run

Temporary public-kernel process, not the live unit:

```bash
cd /home/mumega/sos-public-kernel
set -a
source /home/mumega/.env.secrets
set +a
python3 -m uvicorn sos.services.dashboard.app:app --host 127.0.0.1 --port 18090
```

Observed parity:

- public `:18090/health` matched live `:8090/health` with `200`.
- public `:18090/api/status` matched live `:8090/api/status` with unauthenticated
  `401`.
- public `:18090/sos` matched live method behavior.

Observed difference:

- live `:8090/sos/api/health` reports internal runtime version `0.3.0`.
- public `:18090/sos/api/health` reports public kernel version `0.10.3`.

This is expected after the public release tag and is not a functional mismatch.

## Draft Unit

Do not install this until Hadi approves.

```ini
[Unit]
Description=Mumega Tenant Dashboard - customer web UI
After=network.target

[Service]
EnvironmentFile=/home/mumega/.env.secrets
Type=simple
WorkingDirectory=/home/mumega/sos-public-kernel
Environment=PYTHONPATH=/home/mumega/sos-public-kernel
ExecStart=/usr/bin/python3 -m sos.services.dashboard
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

Rollback unit is the current internal unit with
`WorkingDirectory=/home/mumega/SOS` and no `PYTHONPATH` override.

## CEO Windshield Endpoints

The internal checkout has unmerged S066 endpoints in
`sos/services/dashboard/routes/sos_operator.py`:

- `GET /api/organism/health`
- `GET /api/economics/stats`

They are explicitly public and unauthenticated. They read live Redis heartbeat,
objective, and experience-index data and expose squad names, mandates/status,
coherence hints, $MIND burn estimates, and recent lessons. Comments say they
are called by `torivers.com/#windshield`.

Recommendation: do not merge these endpoints into public SOS as-is. They are
host/product surfaces, not public microkernel surfaces. Hadi should decide one
of:

- keep them internal-only in the Mumega host overlay;
- move them behind admin/system auth before any public-kernel merge;
- redesign them as a generic, opt-in host plugin endpoint with explicit docs and
  tests.

## Live Migration Result

Applied: 2026-05-20, request `s088-dashboard-migrate-live-001`.

Hadi decision: CEO Windshield endpoints stay internal-only and are not merged
to public SOS. After migration, the public dashboard returns `404` for:

- `GET /api/organism/health`
- `GET /api/economics/stats`

The live user unit now runs from the public kernel checkout:

```ini
[Service]
EnvironmentFile=/home/mumega/.env.secrets
Type=simple
WorkingDirectory=/home/mumega/sos-public-kernel
Environment=PYTHONPATH=/home/mumega/sos-public-kernel
ExecStart=/usr/bin/python3 -m sos.services.dashboard
Restart=always
RestartSec=5
```

Backup of the pre-migration internal unit:

```text
/home/mumega/.config/systemd/user/dashboard.service.pre-s088-dashboard-migrate-live-001.bak
```

Post-migration proof on `:8090`:

- `GET /health` returns `200`.
- `GET /api/status` returns unauthenticated `401`.
- `GET /sos/api/health` returns `200`.
- `GET /login` returns `200`.
- `POST /login` with an env-backed system token returns `303 /dashboard` and
  sets the dashboard cookie.
- `GET /dashboard` with that cookie returns `200`.

Rollback command:

```bash
cp /home/mumega/.config/systemd/user/dashboard.service.pre-s088-dashboard-migrate-live-001.bak \
  /home/mumega/.config/systemd/user/dashboard.service
systemctl --user daemon-reload
systemctl --user restart dashboard.service
systemctl --user status dashboard.service --no-pager -l
curl -fsS http://127.0.0.1:8090/health
```

## Pre-Migration Checklist (Historical)

- Hadi reviews the unit and CEO Windshield decision.
- Public dashboard process is run once more on a side port with the live env.
- Dashboard login is tested with a real existing dashboard token.
- Rollback command is ready:

```bash
systemctl --user daemon-reload
systemctl --user restart dashboard.service
systemctl --user status dashboard.service --no-pager -l
```
