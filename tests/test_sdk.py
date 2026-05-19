from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from sos.bus import envelope
from sos.sdk import Agent, Message


class FakeRedis:
    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.published: list[tuple[str, str]] = []
        self.hashes: dict[str, dict[str, str]] = {}
        self._seq = 0

    def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self._seq += 1
        stream_id = f"177880000000{self._seq}-0"
        self.streams.setdefault(stream, []).append((stream_id, fields))
        return stream_id

    def xrange(
        self,
        stream: str,
        min: str = "-",
        max: str = "+",
        count: int | None = None,
    ) -> list[tuple[str, dict[str, str]]]:
        rows = list(self.streams.get(stream, []))
        if min.startswith("("):
            cursor = min[1:]
            rows = [row for row in rows if _sid_key(row[0]) > _sid_key(cursor)]
        if count is not None:
            rows = rows[:count]
        return rows

    def publish(self, channel: str, payload: str) -> int:
        self.published.append((channel, payload))
        return 1

    def hset(self, key: str, mapping: dict[str, str]) -> int:
        self.hashes.setdefault(key, {}).update(mapping)
        return len(mapping)


def _sid_key(value: str) -> tuple[int, int]:
    left, right = value.split("-", 1)
    return int(left), int(right)


def _add(fake: FakeRedis, stream: str, stream_id: str, fields: dict[str, str]) -> None:
    fake.streams.setdefault(stream, []).append((stream_id, fields))


def test_agent_import_shape(tmp_path: Path) -> None:
    agent = Agent(
        token="sk-test",
        name="Hadi.Codex",
        project="sos",
        redis_client=FakeRedis(),
        state_dir=tmp_path,
    )

    assert agent.name == "hadi-codex"
    assert callable(agent.inbox)
    assert callable(agent.send)


def test_streams_cover_project_global_and_legacy(tmp_path: Path) -> None:
    agent = Agent(
        token="sk-test",
        name="hadi-codex",
        project="sos",
        redis_client=FakeRedis(),
        state_dir=tmp_path,
    )

    assert [(s.kind, s.name) for s in agent.streams()] == [
        ("project", "sos:stream:project:sos:agent:hadi-codex"),
        ("global", "sos:stream:global:agent:hadi-codex"),
        ("legacy-global", "sos:stream:agent:hadi-codex"),
        ("legacy-private", "sos:stream:sos:channel:private:agent:hadi-codex"),
        ("subscription:sos:channel:project:sos:global", "sos:stream:project:sos:broadcast"),
    ]


def test_explicit_empty_subscriptions_disable_project_default(tmp_path: Path) -> None:
    agent = Agent(
        token="sk-test",
        name="hadi-codex",
        project="sos",
        redis_client=FakeRedis(),
        state_dir=tmp_path,
        subscriptions=[],
    )

    assert all(not stream.kind.startswith("subscription:") for stream in agent.streams())


def test_streams_include_subscribed_broadcast_channels(tmp_path: Path) -> None:
    agent = Agent(
        token="sk-test",
        name="hadi-codex",
        project="sos",
        redis_client=FakeRedis(),
        state_dir=tmp_path,
        subscriptions=[
            "sos:channel:project:sos:global",
            "sos:channel:project:sos:squad:mumega",
            "sos:channel:project:other:global",
        ],
    )

    assert ("subscription:sos:channel:project:sos:global", "sos:stream:project:sos:broadcast") in [
        (s.kind, s.name) for s in agent.streams()
    ]
    assert ("subscription:sos:channel:project:sos:squad:mumega", "sos:stream:project:sos:squad:mumega") in [
        (s.kind, s.name) for s in agent.streams()
    ]
    assert all(s.name != "sos:stream:project:other:broadcast" for s in agent.streams())


def test_inbox_reads_subscribed_broadcast_channels(tmp_path: Path) -> None:
    fake = FakeRedis()
    _add(
        fake,
        "sos:stream:project:sos:broadcast",
        "1778800000010-0",
        envelope.build(
            msg_type="send",
            source="agent:loom",
            target="sos:channel:project:sos:global",
            text="broadcast update",
            project="sos",
            message_id="broadcast-1",
        ),
    )
    agent = Agent(
        token="sk-test",
        name="hadi-codex",
        project="sos",
        redis_client=fake,
        state_dir=tmp_path,
        subscriptions=["sos:channel:project:sos:global"],
    )

    messages = agent.inbox()

    assert [message.text for message in messages] == ["broadcast update"]
    assert messages[0].stream_kind == "subscription:sos:channel:project:sos:global"


def test_inbox_can_read_via_bridge_without_local_redis(tmp_path: Path, monkeypatch: Any) -> None:
    requested: dict[str, Any] = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "messages": [
                        {
                            "stream_id": "1778811511313-0",
                            "stream": "sos:stream:project:sos:broadcast",
                            "stream_kind": "subscription:sos:channel:project:sos:global",
                            "source": "agent:loom",
                            "target": "sos:channel:project:sos:global",
                            "text": "broadcast update",
                            "project": "sos",
                            "message_id": "broadcast-1",
                        }
                    ]
                }
            ).encode()

    def _urlopen(request: Any, timeout: int):
        parsed = urlparse(request.full_url)
        requested["url"] = request.full_url
        requested["path"] = parsed.path
        requested["query"] = parse_qs(parsed.query)
        requested["authorization"] = request.headers.get("Authorization")
        requested["timeout"] = timeout
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    agent = Agent(
        token="sk-test",
        name="hadi-codex",
        project="sos",
        state_dir=tmp_path,
        subscriptions=["sos:channel:project:sos:global"],
    )

    messages = agent.inbox(since="1778811511312-0")

    assert [message.text for message in messages] == ["broadcast update"]
    assert messages[0].stream == "sos:stream:project:sos:broadcast"
    assert messages[0].stream_kind == "subscription:sos:channel:project:sos:global"
    assert requested["path"] == "/inbox"
    assert requested["url"].startswith("https://bus.mumega.com/")
    assert requested["query"]["since"] == ["1778811511312-0"]
    assert requested["query"]["format"] == ["json"]
    assert requested["query"]["subscriptions"] == ["sos:channel:project:sos:global"]
    assert requested["authorization"] == "Bearer sk-test"


def test_inbox_returns_structured_messages_with_provenance(tmp_path: Path) -> None:
    fake = FakeRedis()
    env = envelope.build(
        msg_type="chat",
        source="agent:kasra",
        target="agent:hadi-codex",
        text="please verify",
        project="sos",
        extras={"request_id": "req-123"},
        message_id="msg-1",
    )
    _add(fake, "sos:stream:project:sos:agent:hadi-codex", "1778800000010-0", env)
    agent = Agent(token="sk-test", name="hadi-codex", project="sos", redis_client=fake, state_dir=tmp_path)

    messages = agent.inbox()

    assert len(messages) == 1
    message = messages[0]
    assert message.stream_id == "1778800000010-0"
    assert message.stream == "sos:stream:project:sos:agent:hadi-codex"
    assert message.stream_kind == "project"
    assert message.sender == "agent:kasra"
    assert message.target == "agent:hadi-codex"
    assert message.text == "please verify"
    assert message.request_id == "req-123"
    assert message.message_id == "msg-1"
    assert message.project == "sos"
    assert message.raw["payload"]


def test_inbox_dedups_across_streams_but_keeps_provenance(tmp_path: Path) -> None:
    fake = FakeRedis()
    env = envelope.build(
        msg_type="chat",
        source="agent:kasra",
        target="agent:hadi-codex",
        text="same",
        project="sos",
        message_id="same-id",
    )
    _add(fake, "sos:stream:project:sos:agent:hadi-codex", "1778800000010-0", env)
    _add(fake, "sos:stream:global:agent:hadi-codex", "1778800000011-0", env)
    agent = Agent(token="sk-test", name="hadi-codex", project="sos", redis_client=fake, state_dir=tmp_path)

    messages = agent.inbox()

    assert len(messages) == 1
    assert messages[0].stream_kind == "global"
    assert messages[0].stream_id == "1778800000011-0"


def test_inbox_cursor_uses_exclusive_redis_stream_id(tmp_path: Path) -> None:
    fake = FakeRedis()
    for sid, text in [
        ("1778800000010-0", "old"),
        ("1778800000011-0", "new"),
    ]:
        _add(
            fake,
            "sos:stream:project:sos:agent:hadi-codex",
            sid,
            envelope.build(
                msg_type="chat",
                source="agent:kasra",
                target="agent:hadi-codex",
                text=text,
                project="sos",
            ),
        )
    agent = Agent(token="sk-test", name="hadi-codex", project="sos", redis_client=fake, state_dir=tmp_path)

    messages = agent.inbox(since="1778800000010-0")

    assert [message.text for message in messages] == ["new"]


def test_inbox_filters_self_messages_by_default(tmp_path: Path) -> None:
    fake = FakeRedis()
    _add(
        fake,
        "sos:stream:project:sos:agent:hadi-codex",
        "1778800000010-0",
        envelope.build(
            msg_type="chat",
            source="agent:hadi-codex",
            target="agent:hadi-codex",
            text="self",
            project="sos",
        ),
    )
    agent = Agent(token="sk-test", name="hadi-codex", project="sos", redis_client=fake, state_dir=tmp_path)

    assert agent.inbox() == []
    assert len(agent.inbox(include_self=True)) == 1


def test_send_returns_stream_and_message_ids(tmp_path: Path) -> None:
    fake = FakeRedis()
    agent = Agent(token="sk-test", name="codex", project="sos", redis_client=fake, state_dir=tmp_path)

    result = agent.send("hadi-codex", "sdk test")

    assert result.stream_id.startswith("1778800000001-")
    assert result.message_id
    assert result.stream == "sos:stream:project:sos:agent:hadi-codex"
    assert result.channel == "sos:channel:project:sos:agent:hadi-codex"
    assert result.wake_channel == "sos:wake:hadi-codex"
    stored = fake.streams[result.stream][0][1]
    parsed = envelope.parse(stored)
    assert parsed["source"] == "agent:codex"
    assert parsed["target"] == "agent:hadi-codex"
    assert parsed["text"] == "sdk test"
    assert fake.published[-1][0] == "sos:wake:hadi-codex"


def test_heartbeat_updates_registry_and_broadcasts(tmp_path: Path) -> None:
    fake = FakeRedis()
    agent = Agent(token="sk-test", name="hadi-codex", project="sos", redis_client=fake, state_dir=tmp_path)

    result = agent.heartbeat(summary="alive", tool="pytest")

    assert result.registry_key == "sos:registry:hadi-codex"
    assert fake.hashes["sos:registry:hadi-codex"]["summary"] == "alive"
    assert fake.hashes["sos:registry:hadi-codex"]["tool"] == "pytest"
    assert result.stream_id
    assert "sos:stream:global:agent:broadcast" in fake.streams


def test_cursor_persistence(tmp_path: Path) -> None:
    agent = Agent(
        token="sk-test",
        name="hadi-codex",
        project="sos",
        redis_client=FakeRedis(),
        state_dir=tmp_path,
    )

    agent.save_cursor("1778800000010-0")

    assert agent.load_cursor() == "1778800000010-0"
    assert json.loads((tmp_path / "cursor.json").read_text()) == {
        "last_stream_id": "1778800000010-0"
    }


def test_send_routes_via_bridge_when_no_redis(tmp_path: Path, monkeypatch: Any) -> None:
    posted: dict[str, Any] = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({
                "ok": True,
                "status": "queued",
                "message_id": "msg-bridge-1",
                "entry_id": "1778900000001-0",
                "stream": "sos:stream:project:sos:agent:kasra",
                "project": "sos",
            }).encode()

    def _urlopen(request: Any, timeout: int):
        posted["url"] = request.full_url
        posted["method"] = request.method
        posted["body"] = json.loads(request.data.decode())
        posted["authorization"] = request.headers.get("Authorization")
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    agent = Agent(
        token="sk-bus-test",
        name="hadi-codex",
        project="sos",
        bridge_url="https://bus.mumega.com",
        state_dir=tmp_path,
    )

    result = agent.send("kasra", "hello from bridge")

    assert posted["url"] == "https://bus.mumega.com/send"
    assert posted["method"] == "POST"
    assert posted["body"]["from"] == "hadi-codex"
    assert posted["body"]["to"] == "kasra"
    assert posted["body"]["text"] == "hello from bridge"
    assert posted["body"]["project"] == "sos"
    assert posted["authorization"] == "Bearer sk-bus-test"
    assert result.stream_id == "1778900000001-0"
    assert result.message_id == "msg-bridge-1"
    assert result.target == "kasra"


def test_onboard_sends_heartbeat_to_broadcast(tmp_path: Path, monkeypatch: Any) -> None:
    posted: dict[str, Any] = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"ok": True, "message_id": "hb-1", "entry_id": "1778900000002-0"}).encode()

    def _urlopen(request: Any, timeout: int):
        posted["body"] = json.loads(request.data.decode())
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    agent = Agent(
        token="sk-bus-test",
        name="gemini-enterprise-sos",
        project="sos",
        bridge_url="https://bus.mumega.com",
        state_dir=tmp_path,
    )

    agent.onboard(summary="Gemini Enterprise online")

    assert posted["body"]["to"] == "broadcast"
    assert posted["body"]["from"] == "gemini-enterprise-sos"
    assert posted["body"]["text"] == "Gemini Enterprise online"


def test_on_message_decorator_and_start_once(tmp_path: Path) -> None:
    fake = FakeRedis()
    _add(
        fake,
        "sos:stream:project:sos:agent:hadi-codex",
        "1778800000010-0",
        envelope.build(
            msg_type="chat",
            source="agent:kasra",
            target="agent:hadi-codex",
            text="run handler",
            project="sos",
        ),
    )
    agent = Agent(token="sk-test", name="hadi-codex", project="sos", redis_client=fake, state_dir=tmp_path)
    handled: list[Message] = []

    @agent.on_message
    def handle(message: Message) -> None:
        handled.append(message)

    agent.start(once=True)

    assert [message.text for message in handled] == ["run handler"]
