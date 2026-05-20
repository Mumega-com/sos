# bus_canary - bus round-trip canary

## What it does

Every 60 s, probes the SOS bus by simulating a canary-to-canary round trip via
the bus REST bridge on `:6380`:

1. The origin canary POSTs `[probe:{id}] {ts}` to `/send` for the peer canary.
2. The peer canary GETs `/inbox` and waits for the probe to surface.
3. The peer canary POSTs a reply to `/send` for the origin canary.
4. The origin canary GETs `/inbox` and waits for the reply.

Round-trip > 5 s or any step failing pages Discord (`alerts` by default) and
sends a best-effort bus alert.

State-change paging only: while in `fail` state, repeated failures log to
journalctl but do not re-page; first successful cycle pages a `RECOVERY`.

## Why two identities

The probe uses two dedicated bus identities so probe traffic stays out of real
agent inboxes and per-side authentication mirrors real-agent posture.

The default identity names keep compatibility with the original SOS deployment:

- `ORIGIN_CANARY_AGENT=loom-canary`
- `PEER_CANARY_AGENT=kasra-canary`
- `BUS_CANARY_ALERT_AGENT=loom`

Override them in the unit environment for another host.

## Files

- `probe.py` - sync probe loop (stdlib urllib, no external deps).
- `~/.config/systemd/user/bus-canary.service` - long-running unit.
- `~/.env.secrets` - `LOOM_CANARY_TOKEN`, `KASRA_CANARY_TOKEN`.

## Operate

```bash
systemctl --user start  bus-canary
systemctl --user status bus-canary
journalctl --user -u bus-canary -f
```

## Verifying the canary

Mechanical check: kill bus-bridge (`systemctl --user stop sos-bus-bridge`) for
60 s, watch `journalctl --user -u bus-canary -f`. Within one probe cycle a
`PAGE` line appears and the configured Discord channel receives a notice.
Restart bridge; the next healthy cycle prints `RECOVERY`.

## Layered scope

The probe exercises the bus REST surface that ultimately backs `mcp__sos__send`
/ `mcp__sos__inbox` (both XADD/XREVRANGE the same Redis stream). The MCP server
on `:6070` sits in front of REST and could fail independently. Extending the
canary to also exercise the MCP layer is a follow-up if that gap surfaces.
