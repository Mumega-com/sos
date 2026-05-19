# mumega-bus-watch

`mumega-bus-watch` is the packageable local receive bridge for off-server
agents. It polls the SOS bus bridge, deduplicates messages locally, and wakes a
small allowlisted transport command.

MVP scope:

- JSON config in `~/.sos/bus-watch.json`
- Durable delivered-message state in `~/.sos/bus-watch-state.json`
- `inbox(format=json)` compatible polling over HTTP
- Token redaction in errors
- Command allowlist
- No mark-seen until every transport exits `0`
- macOS launchd plist generation

Install a starter config:

```bash
export SOS_BUS_TOKEN="$(security find-generic-password -a "$USER" -s sos-bus-token -w)"
mumega-bus-watch install --agent hadi-codex --project sos
```

Safe token sources for `install`:

- `SOS_BUS_TOKEN` by default, or another environment variable via `--token-env NAME`
- `--token-file ~/.sos/token` for a local `0600` token file; the config stores
  the file path, not the bearer token
- `--token-stdin` for secret-manager pipes

`--token TOKEN` is kept for test fixtures and throwaway local canaries, but do
not use it for real bearer tokens. Command-line arguments can leak through shell
history, process listings, and logs.

Validate:

```bash
mumega-bus-watch doctor
mumega-bus-watch status
mumega-bus-watch test-send --to hadi-codex --text "bus-watch test"
```

Run once:

```bash
mumega-bus-watch run --once
```

Run forever:

```bash
mumega-bus-watch run
```

Generate launchd plist:

```bash
mumega-bus-watch install --agent hadi-codex --token-file ~/.sos/token --project sos --launchd
launchctl load ~/Library/LaunchAgents/com.mumega.bus-watch.plist
```

Config example:

```json
{
  "agent": "hadi-codex",
  "token_file": "~/.sos/token",
  "project": "sos",
  "bridge_url": "http://localhost:6380",
  "limit": 10,
  "poll_interval": 3,
  "state_path": "~/.sos/bus-watch-state.json",
  "allowlist": ["/usr/bin/osascript", "/opt/homebrew/bin/tmux", "/usr/bin/tmux"],
  "transports": [
    {
      "name": "tmux",
      "command": ["/opt/homebrew/bin/tmux", "send-keys", "-t", "hadi-codex-cli", "{text}", "Enter"]
    }
  ]
}
```

For Codex Desktop, use an allowlisted wrapper script as the transport target.
Keep the wrapper local and deterministic; do not allow arbitrary shell strings.
