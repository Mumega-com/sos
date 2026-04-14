from __future__ import annotations


def test_non_coordinator_cannot_delegate():
    from sos.kernel.coordination import Coordinator

    coord = Coordinator.__new__(Coordinator)
    try:
        coord._validate_delegate_route("worker", "gemma")
    except PermissionError as exc:
        assert "not allowed to dispatch" in str(exc)
    else:
        raise AssertionError("expected PermissionError")


def test_coordinator_can_delegate_to_worker():
    from sos.kernel.coordination import Coordinator

    coord = Coordinator.__new__(Coordinator)
    coord._validate_delegate_route("kasra", "worker")


def test_task_must_have_coordinator_label():
    from sos.services.health.task_poller import _task_is_coordinator_routed

    assert _task_is_coordinator_routed(["delegated", "from:kasra"]) is True
    assert _task_is_coordinator_routed(["queued"]) is False


def test_coordinator_tasks_not_fetched():
    from sos.services.health import task_poller

    tasks = task_poller.fetch_assigned_tasks("kasra")
    assert tasks == []
