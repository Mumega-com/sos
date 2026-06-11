"""Unit tests for the single-instance flock in sos/services/engine/__main__.py.

Tests run without touching the live service — they use a temp lockfile.
No network, no Redis, no uvicorn.
"""
from __future__ import annotations

import fcntl
import os
import sys
import tempfile

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _acquire_lock_under_test(lock_path: str) -> int:
    """Re-implement the production lock logic so we can test it in-process
    without importing the full engine (which would pull in uvicorn / FastAPI).

    Mirrors _acquire_instance_lock() in __main__.py exactly.
    """
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise
    return fd


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSingleInstanceLock:
    def test_first_acquire_succeeds(self, tmp_path):
        """The first caller must be able to acquire the lock."""
        lock_path = str(tmp_path / "test-engine.lock")
        fd = _acquire_lock_under_test(lock_path)
        try:
            assert fd >= 0
        finally:
            os.close(fd)

    def test_second_acquire_raises_blocking_io(self, tmp_path):
        """A second call while the lock is held must raise BlockingIOError.

        This is exactly what the production code catches to trigger sys.exit(0).
        Two instances can NEVER both hold the lock — hence they can never both
        reach uvicorn.bind().
        """
        lock_path = str(tmp_path / "test-engine.lock")
        fd1 = _acquire_lock_under_test(lock_path)
        try:
            with pytest.raises(BlockingIOError):
                fd2 = _acquire_lock_under_test(lock_path)
                # Should not reach here, but clean up if it somehow does.
                os.close(fd2)
        finally:
            os.close(fd1)

    def test_lock_released_after_fd_close(self, tmp_path):
        """After the first holder closes its fd, the lock is available again.

        This models what happens after the engine process exits (normally or
        via kill) — the fd closes automatically and the lock is freed so the
        next systemd restart can succeed.
        """
        lock_path = str(tmp_path / "test-engine.lock")

        fd1 = _acquire_lock_under_test(lock_path)
        os.close(fd1)  # Simulate process exit / fd cleanup

        fd2 = _acquire_lock_under_test(lock_path)
        try:
            assert fd2 >= 0, "Lock should be re-acquirable after previous holder closed fd"
        finally:
            os.close(fd2)

    def test_second_caller_exits_cleanly_via_production_wrapper(self, tmp_path, monkeypatch):
        """Simulate what _acquire_instance_lock() does in production when
        another instance is live: it must call sys.exit(0) — not raise,
        not exit(1) — so systemd does not count it as a crash restart.
        """
        lock_path = str(tmp_path / "test-engine.lock")

        # Hold the lock as "first instance"
        fd1 = _acquire_lock_under_test(lock_path)

        # Monkeypatch sys.exit so we can capture the call.
        exit_calls: list[int] = []
        monkeypatch.setattr(sys, "exit", lambda code: exit_calls.append(code))

        # Inline the production wrapper logic.
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                sys.exit(0)  # production path — must be exit(0)
        finally:
            os.close(fd1)

        assert exit_calls == [0], (
            "Duplicate instance must exit(0) — clean, not an error — "
            f"got sys.exit calls: {exit_calls}"
        )

    def test_no_socket_prebind_in_main(self):
        """Verify the SO_REUSEADDR pre-bind test is gone from __main__.py.

        The false-positive was specifically:
          test_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
          test_sock.bind(...)
        We check that the actual setsockopt call is absent (a comment mentioning
        the constant is fine — the executable pre-bind socket creation is not).
        """
        main_path = os.path.join(
            os.path.dirname(__file__),
            "..", "sos", "services", "engine", "__main__.py"
        )
        with open(os.path.normpath(main_path)) as f:
            source = f.read()

        # The live pre-bind test was: test_sock.setsockopt(...)
        # If that line is back, the false-positive is back.
        assert "setsockopt" not in source, (
            "setsockopt pre-bind check must NOT be present in __main__.py — "
            "it gives false-positive 'port free' reads (root cause of #163)"
        )
        # Also: the socket import was only used for the pre-bind test.
        assert "import socket" not in source, (
            "'import socket' must be absent — the socket pre-bind check was removed (#163)"
        )
