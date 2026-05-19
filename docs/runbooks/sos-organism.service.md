# sos-organism.service — systemd runbook

The **organism** is a long-running Python loop that drives the per-project
daily heartbeat: three scheduled pulses (morning / noon / evening) plus a
postmortem objective auto-posted whenever a root objective is paid. It's
the v0.8.1 entry point for "the tree runs itself".

See `sos.services.operations.organism` (the loop) and
`sos.services.operations.pulse` (the individual pulse functions) for code.

## systemd unit

Install at `/etc/systemd/system/sos-organism.service`:

```
[Unit]
Description=SOS Organism — daily rhythm pulses
After=network.target redis.service
Requires=redis.service

[Service]
Type=simple
User=sos
WorkingDirectory=/opt/sos
EnvironmentFile=/opt/sos/.env
ExecStart=/opt/sos/.venv/bin/python -m sos.services.operations.organism --projects trop
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Reload + start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sos-organism.service
sudo journalctl -u sos-organism -f
```

## Flags

| Flag | Default | Meaning |
|---|---|---|
| `--projects` | *(required)* | Comma-separated list of active tenant slugs, e.g. `trop` or `trop,viamar`. One pulse window per project per day. |
| `--sleep-seconds` | `60` | Interval between ticks. The postmortem scan looks back `2 * sleep_seconds` into the audit stream. |

## Environment variables

All standard SOS runtime vars, plus these are the ones this service reads:

| Variable | Purpose |
|---|---|
| `SOS_OBJECTIVES_URL` | Base URL of the Objectives service, default `http://localhost:6068`. |
| `SOS_SYSTEM_TOKEN` | Bearer token the organism uses when creating objectives. Falls back to `SOS_OBJECTIVES_TOKEN`. |
| `REDIS_URL` | Redis connection URL (used for per-window dedupe cache + postmortem dedupe set). Legacy `SOS_REDIS_URL` + `REDIS_HOST/PORT/PASSWORD` also honoured. |

Place these in `/opt/sos/.env` — the `EnvironmentFile` line in the unit
picks them up.

## Pulse windows (server local time)

| Window | Hours | Pulse function |
|---|---|---|
| morning | 06:00–08:59 | `post_morning_pulse` (a.k.a. `post_daily_rhythm`) |
| noon | 11:00–13:59 | `post_noon_pulse` |
| evening | 18:00–20:59 | `post_evening_pulse` |

Outside those hours the organism stays idle on the pulse side and only
services the paid-root postmortem scan.

## Redis keys owned by the organism

| Key | Shape | TTL | Purpose |
|---|---|---|---|
| `sos:organism:last_ran:{project}:{window}` | string (ISO date) | 25h | Dedupe — ensures each `(project, window)` pair fires at most once per day. |
| `sos:organism:postmortem_posted` | set of objective ids | 7d rolling | Dedupe — ensures each paid root gets exactly one postmortem objective. |

Both keys are fail-soft — if the read fails we re-fire rather than silently
skip; if the write fails we log and continue.

## Fail-soft behaviour

Every tick is wrapped so any of these failure modes just log and continue:

- Objectives service 500/timeout → pulse returns empty, cache not set, next
  tick retries.
- Redis unreachable → organism keeps going, windows may double-fire when
  Redis returns (safe because each pulse is idempotent at the root level
  via the dated `<project>-<window>-YYYYMMDD` slug).
- Malformed audit stream entries → skipped silently.

The process only exits on SIGTERM / SIGINT. systemd `Restart=always` handles
the rest.

## Debugging

```bash
# One-shot pulse (manual kick):
python -m sos.services.operations.pulse --project trop --window morning

# Check the dedupe cache:
redis-cli --scan --pattern 'sos:organism:last_ran:*'
redis-cli smembers sos:organism:postmortem_posted

# Clear the cache to re-fire (use sparingly):
redis-cli del sos:organism:last_ran:trop:morning
```

## See also

- `docs/runbooks/deploy-sos-dispatcher.md` — peer service runbook.
- `docs/plans/2026-04-19-sos-trop-ready-v0.8.1.md` §S6 — sprint spec this
  unit implements.
