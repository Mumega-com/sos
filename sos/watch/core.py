from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path.home() / ".sos" / "bus-watch.json"
DEFAULT_STATE_PATH = Path.home() / ".sos" / "bus-watch-state.json"


@dataclass(frozen=True)
class Transport:
    name: str
    command: list[str]


@dataclass(frozen=True)
class BusWatchConfig:
    agent: str
    token: str
    token_source: str = "inline"
    token_file: Path | None = None
    token_env: str | None = None
    project: str | None = None
    bridge_url: str = "http://localhost:6380"
    limit: int = 10
    poll_interval: float = 3.0
    state_path: Path = DEFAULT_STATE_PATH
    allowlist: tuple[str, ...] = ()
    transports: tuple[Transport, ...] = ()

    @classmethod
    def load(cls, path: str | Path = DEFAULT_CONFIG_PATH) -> "BusWatchConfig":
        raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        transports = tuple(
            Transport(name=str(item["name"]), command=[str(part) for part in item["command"]])
            for item in raw.get("transports", [])
        )
        token, token_source, token_file, token_env = _resolve_config_token(raw)
        return cls(
            agent=str(raw["agent"]),
            token=token,
            token_source=token_source,
            token_file=token_file,
            token_env=token_env,
            project=raw.get("project") or None,
            bridge_url=str(raw.get("bridge_url") or "http://localhost:6380").rstrip("/"),
            limit=int(raw.get("limit", 10)),
            poll_interval=float(raw.get("poll_interval", 3.0)),
            state_path=Path(raw.get("state_path") or DEFAULT_STATE_PATH).expanduser(),
            allowlist=tuple(str(item) for item in raw.get("allowlist", [])),
            transports=transports,
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.agent:
            errors.append("agent is required")
        if not self.token:
            errors.append("token is required via token, token_file, token_env, or SOS_BUS_TOKEN")
        if self.token_file and not self.token_file.exists():
            errors.append(f"token_file does not exist: {self.token_file}")
        if not self.transports:
            errors.append("at least one transport is required")
        for transport in self.transports:
            if not transport.command:
                errors.append(f"transport {transport.name} has empty command")
            elif not _is_allowed(transport.command[0], self.allowlist):
                errors.append(
                    f"transport {transport.name} command is not allowlisted: {transport.command[0]}"
                )
        return errors


@dataclass
class WatchState:
    delivered: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: Path) -> "WatchState":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return cls()
        delivered = raw.get("delivered", [])
        return cls(delivered={str(item) for item in delivered if item})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"delivered": sorted(self.delivered)[-2000:]}, indent=2) + "\n",
            encoding="utf-8",
        )


@dataclass(frozen=True)
class WatchMessage:
    stream_id: str
    source: str
    text: str
    raw: dict[str, Any]


class BusWatcher:
    def __init__(self, config: BusWatchConfig, state: WatchState | None = None) -> None:
        self.config = config
        self.state = state if state is not None else WatchState.load(config.state_path)

    def poll_once(self) -> list[WatchMessage]:
        messages = self.fetch_messages()
        delivered: list[WatchMessage] = []
        for message in messages:
            if message.stream_id in self.state.delivered:
                continue
            if self.deliver(message):
                self.state.delivered.add(message.stream_id)
                self.state.save(self.config.state_path)
                delivered.append(message)
        return delivered

    def run_forever(self) -> None:
        backoff = self.config.poll_interval
        while True:
            try:
                self.poll_once()
                backoff = self.config.poll_interval
                time.sleep(self.config.poll_interval)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"bus-watch: poll failed: {_redact(str(exc), self.config.token)}")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    def fetch_messages(self) -> list[WatchMessage]:
        query = {
            "agent": self.config.agent,
            "limit": str(self.config.limit),
            "format": "json",
        }
        if self.config.project:
            query["project"] = self.config.project
        url = f"{self.config.bridge_url}/inbox?{urllib.parse.urlencode(query)}"
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {self.config.token}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
        return [_message_from_raw(item) for item in payload.get("messages", [])]

    def send_test(self, target: str, text: str) -> dict[str, Any]:
        url = f"{self.config.bridge_url}/send"
        body: dict[str, Any] = {"from": self.config.agent, "to": target, "text": text}
        if self.config.project:
            body["project"] = self.config.project
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {body_text}") from exc

    def deliver(self, message: WatchMessage) -> bool:
        ok = True
        for transport in self.config.transports:
            if not self._run_transport(transport, message):
                ok = False
        return ok

    def _run_transport(self, transport: Transport, message: WatchMessage) -> bool:
        if not transport.command or not _is_allowed(transport.command[0], self.config.allowlist):
            print(f"bus-watch: blocked non-allowlisted transport {transport.name}")
            return False
        env = os.environ.copy()
        env.update(
            {
                "SOS_MESSAGE_STREAM_ID": message.stream_id,
                "SOS_MESSAGE_SOURCE": message.source,
                "SOS_MESSAGE_TEXT": message.text,
                "SOS_AGENT": self.config.agent,
            }
        )
        command = [_render_arg(part, message, self.config.agent) for part in transport.command]
        try:
            result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=30)
        except Exception as exc:
            print(f"bus-watch: transport {transport.name} failed: {exc}")
            return False
        if result.returncode != 0:
            stderr = _redact(result.stderr.strip(), self.config.token)
            print(f"bus-watch: transport {transport.name} exit {result.returncode}: {stderr}")
            return False
        return True


def default_config(
    agent: str,
    token: str | None = None,
    project: str | None = "sos",
    *,
    token_file: str | None = None,
    token_env: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "agent": agent,
        "project": project,
        "bridge_url": "http://localhost:6380",
        "limit": 10,
        "poll_interval": 3,
        "state_path": str(DEFAULT_STATE_PATH),
        "allowlist": ["/bin/echo", "/usr/bin/osascript", "/opt/homebrew/bin/tmux", "/usr/bin/tmux"],
        "transports": [
            {
                "name": "stdout",
                "command": [
                    "/bin/echo",
                    "[bus:{agent}] {source}: {text}",
                ],
            }
        ],
    }
    if token_file:
        data["token_file"] = token_file
    elif token_env:
        data["token_env"] = token_env
    else:
        data["token"] = token or ""
    return data


def write_default_config(
    path: Path,
    agent: str,
    token: str | None = None,
    project: str | None = "sos",
    *,
    token_file: str | None = None,
    token_env: str | None = None,
) -> None:
    path = path.expanduser()
    if path.exists():
        raise FileExistsError(path)
    data = default_config(
        agent=agent,
        token=token,
        project=project,
        token_file=token_file,
        token_env=token_env,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def write_launchd_plist(config_path: Path, label: str = "com.mumega.bus-watch") -> Path:
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    python = shlex.quote(os.environ.get("PYTHON", "python3"))
    config = shlex.quote(str(config_path.expanduser()))
    program = f"{python} -m sos.watch run --config {config}"
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>-lc</string>
    <string>{program}</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{Path.home()}/Library/Logs/{label}.out.log</string>
  <key>StandardErrorPath</key><string>{Path.home()}/Library/Logs/{label}.err.log</string>
</dict>
</plist>
"""
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist, encoding="utf-8")
    return plist_path


def _message_from_raw(raw: dict[str, Any]) -> WatchMessage:
    stream_id = str(raw.get("stream_id") or raw.get("id") or "")
    source = str(raw.get("source") or raw.get("sender") or "")
    text = str(raw.get("text") or "")
    return WatchMessage(stream_id=stream_id, source=source, text=text, raw=raw)


def _render_arg(template: str, message: WatchMessage, agent: str) -> str:
    return template.format(
        agent=agent,
        stream_id=message.stream_id,
        source=message.source,
        text=message.text,
    )


def _is_allowed(command: str, allowlist: tuple[str, ...]) -> bool:
    if not allowlist:
        return False
    command_path = Path(command)
    for allowed in allowlist:
        allowed_path = Path(allowed)
        if command_path == allowed_path:
            return True
    return False


def _resolve_config_token(raw: dict[str, Any]) -> tuple[str, str, Path | None, str | None]:
    if raw.get("token_file"):
        token_file = Path(str(raw["token_file"])).expanduser()
        try:
            return token_file.read_text(encoding="utf-8").strip(), "token_file", token_file, None
        except FileNotFoundError:
            return "", "token_file", token_file, None
    if raw.get("token_env"):
        token_env = str(raw["token_env"])
        return os.environ.get(token_env, "").strip(), "token_env", None, token_env
    if raw.get("token"):
        return str(raw["token"]).strip(), "inline", None, None
    return os.environ.get("SOS_BUS_TOKEN", "").strip(), "token_env", None, "SOS_BUS_TOKEN"


def _redact(text: str, token: str) -> str:
    if not token:
        return text
    return text.replace(token, "[redacted-token]")
