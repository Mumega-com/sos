from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sos.watch.core import (
    BusWatchConfig,
    BusWatcher,
    Transport,
    WatchMessage,
    WatchState,
    default_config,
)
from sos.watch.cli import main


class FakeWatcher(BusWatcher):
    def __init__(self, config: BusWatchConfig, messages: list[WatchMessage], exits: list[bool]) -> None:
        super().__init__(config, WatchState())
        self._messages = messages
        self._exits = exits
        self.delivered: list[str] = []

    def fetch_messages(self) -> list[WatchMessage]:
        return self._messages

    def deliver(self, message: WatchMessage) -> bool:
        self.delivered.append(message.stream_id)
        return self._exits.pop(0)


def _config(tmp_path: Path) -> BusWatchConfig:
    return BusWatchConfig(
        agent="hadi-codex",
        token="sk-test",
        project="sos",
        state_path=tmp_path / "state.json",
        allowlist=("/bin/echo",),
        transports=(Transport(name="stdout", command=["/bin/echo", "{text}"]),),
    )


def test_config_requires_allowlisted_transport(tmp_path: Path) -> None:
    config = BusWatchConfig(
        agent="hadi-codex",
        token="sk-test",
        state_path=tmp_path / "state.json",
        allowlist=("/bin/echo",),
        transports=(Transport(name="bad", command=["/bin/sh", "-c", "echo nope"]),),
    )

    assert any("not allowlisted" in error for error in config.validate())


def test_poll_does_not_mark_seen_until_delivery_success(tmp_path: Path) -> None:
    message = WatchMessage(stream_id="1-0", source="agent:loom", text="hello", raw={})
    watcher = FakeWatcher(_config(tmp_path), [message], [False, True])

    assert watcher.poll_once() == []
    assert "1-0" not in watcher.state.delivered

    assert [m.stream_id for m in watcher.poll_once()] == ["1-0"]
    assert "1-0" in watcher.state.delivered


def test_poll_deduplicates_delivered_state(tmp_path: Path) -> None:
    message = WatchMessage(stream_id="1-0", source="agent:loom", text="hello", raw={})
    watcher = FakeWatcher(_config(tmp_path), [message], [True])

    assert len(watcher.poll_once()) == 1
    assert watcher.poll_once() == []
    assert watcher.delivered == ["1-0"]


def test_config_loads_from_json(tmp_path: Path) -> None:
    path = tmp_path / "bus-watch.json"
    path.write_text(
        json.dumps(
            {
                "agent": "hadi-codex",
                "token": "sk-test",
                "project": "sos",
                "allowlist": ["/bin/echo"],
                "transports": [{"name": "stdout", "command": ["/bin/echo", "{text}"]}],
            }
        )
    )

    config = BusWatchConfig.load(path)

    assert config.agent == "hadi-codex"
    assert config.transports[0].command == ["/bin/echo", "{text}"]
    assert config.validate() == []


def test_default_config_is_valid_after_install(tmp_path: Path) -> None:
    raw: dict[str, Any] = default_config("hadi-codex", "sk-test", "sos")
    raw["state_path"] = str(tmp_path / "state.json")
    path = tmp_path / "bus-watch.json"
    path.write_text(json.dumps(raw))

    config = BusWatchConfig.load(path)

    assert config.validate() == []


def test_install_reads_token_from_env(tmp_path: Path, monkeypatch: Any) -> None:
    path = tmp_path / "bus-watch.json"
    monkeypatch.setenv("SOS_BUS_TOKEN", "sk-env-token")

    result = main(["install", "--config", str(path), "--agent", "hadi-codex", "--project", "sos"])

    assert result == 0
    raw = json.loads(path.read_text())
    assert raw["token_env"] == "SOS_BUS_TOKEN"
    assert "token" not in raw
    assert BusWatchConfig.load(path).token == "sk-env-token"


def test_install_reads_token_from_file(tmp_path: Path) -> None:
    path = tmp_path / "bus-watch.json"
    token_path = tmp_path / "token"
    token_path.write_text("sk-file-token\n")

    result = main(
        [
            "install",
            "--config",
            str(path),
            "--agent",
            "hadi-codex",
            "--token-file",
            str(token_path),
        ]
    )

    assert result == 0
    raw = json.loads(path.read_text())
    assert raw["token_file"] == str(token_path)
    assert "token" not in raw
    assert BusWatchConfig.load(path).token == "sk-file-token"


def test_install_requires_single_explicit_token_source(tmp_path: Path) -> None:
    path = tmp_path / "bus-watch.json"
    token_path = tmp_path / "token"
    token_path.write_text("sk-file-token\n")

    result = main(
        [
            "install",
            "--config",
            str(path),
            "--agent",
            "hadi-codex",
            "--token",
            "sk-arg-token",
            "--token-file",
            str(token_path),
        ]
    )

    assert result == 1
    assert not path.exists()


def test_status_does_not_print_token_value(tmp_path: Path, capsys: Any) -> None:
    path = tmp_path / "bus-watch.json"
    token_path = tmp_path / "token"
    token_path.write_text("sk-file-token\n")
    raw: dict[str, Any] = default_config("hadi-codex", project="sos", token_file=str(token_path))
    raw["state_path"] = str(tmp_path / "state.json")
    path.write_text(json.dumps(raw))

    result = main(["status", "--config", str(path)])

    assert result == 0
    captured = capsys.readouterr()
    assert "sk-file-token" not in captured.out
    assert '"token_source": "token_file"' in captured.out
