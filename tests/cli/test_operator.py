from __future__ import annotations

import json

from sos.cli.operator import (
    OperatorSnapshot,
    RedisSnapshot,
    ServiceProbe,
    TaskSnapshot,
    _redact_url,
    render_json,
    render_text,
    service_snapshot,
    task_snapshot,
)


def test_redact_url_hides_password() -> None:
    assert (
        _redact_url("redis://user:secret@localhost:6379/0")
        == "redis://user:<redacted>@localhost:6379/0"
    )


def test_service_snapshot_uses_health_urls() -> None:
    seen: list[str] = []

    def fake_get(url: str, timeout: float, headers: dict[str, str] | None) -> tuple[int, str]:
        seen.append(url)
        assert timeout == 2.0
        assert headers is None
        return 200, '{"status":"ok"}'

    result = service_snapshot(
        {"SOS_ENGINE_URL": "http://engine.test", "SOS_BUS_URL": "http://bus.test/health"},
        http_get=fake_get,
    )

    assert result[0].name == "engine"
    assert result[0].url == "http://engine.test/health"
    assert result[0].status == "ok"
    assert "http://bus.test/health" in seen


def test_task_snapshot_requires_token() -> None:
    result = task_snapshot({})
    assert result.status == "unavailable"
    assert result.stuck_tasks is None


def test_task_snapshot_counts_blocked_tasks_with_token() -> None:
    def fake_get(url: str, timeout: float, headers: dict[str, str] | None) -> tuple[int, str]:
        assert url == "http://squad.test/tasks?status=blocked&limit=25"
        assert headers == {"Authorization": "Bearer token"}
        return 200, json.dumps([{"id": "a"}, {"id": "b"}])

    result = task_snapshot(
        {"SOS_SQUAD_URL": "http://squad.test", "SOS_SQUAD_TOKEN": "token"},
        http_get=fake_get,
    )

    assert result.status == "ok"
    assert result.stuck_tasks == 2


def test_renderers_include_operator_sections() -> None:
    snapshot = OperatorSnapshot(
        services=[
            ServiceProbe(
                name="mcp",
                url="http://127.0.0.1:6070/health",
                status="ok",
                detail="{}",
            )
        ],
        redis=RedisSnapshot(
            status="ok",
            detail="redis://localhost connected",
            agents=["codex"],
            streams=[{"name": "sos:stream:global:agent:codex", "length": 3}],
            failed_wakeups=[],
            recent_gate_events=[],
        ),
        tasks=TaskSnapshot(status="unavailable", detail="missing token", stuck_tasks=None),
    )

    text = render_text(snapshot)
    assert "SOS Operator Snapshot" in text
    assert "Services" in text
    assert "Wakeups / Gates" in text
    assert "Tasks" in text

    data = json.loads(render_json(snapshot))
    assert data["services"][0]["name"] == "mcp"
    assert data["redis"]["agents"] == ["codex"]
