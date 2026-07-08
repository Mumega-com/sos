from __future__ import annotations

import asyncio
import fcntl
import os
import sys

import uvicorn
from sos.services.engine.app import app

# Default lockfile path; override via env for non-root / container setups.
_DEFAULT_LOCK = "/tmp/sos-engine.lock"


def _acquire_instance_lock() -> int:
    """Open and exclusively lock the engine lockfile (non-blocking).

    Returns the open file descriptor so the caller can keep it alive for
    the process lifetime (lock is released automatically when the fd closes
    on process exit or kill).

    Raises SystemExit(0) — *not* an error — when another live instance
    already holds the lock. This is the structural fix for the restart-loop:
    systemd starts a duplicate instance, it sees the lock is taken, exits
    cleanly (exit 0), and the loop stops accumulating zombies.
    """
    lock_path = os.getenv("SOS_ENGINE_LOCK", _DEFAULT_LOCK)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        # Filesystem issue — log and bail with an error so systemd knows.
        print(
            f"[engine] FATAL: cannot open lockfile {lock_path}: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        # Another engine instance is live and owns the lock.
        # Exit 0 = clean / expected — do NOT let systemd count this as a crash.
        print(
            "[engine] Another instance is already running (lock held). "
            "Exiting cleanly — this instance is a duplicate.",
            file=sys.stderr,
        )
        os.close(fd)
        sys.exit(0)

    # Write our PID so operators can inspect without lsof.
    try:
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
    except OSError:
        pass  # Non-fatal — the lock itself is what matters.

    return fd  # Caller must keep this alive for the process lifetime.


def main() -> None:
    # -------------------------------------------------------------------------
    # Single-instance gate (#163 structural fix)
    # Acquire an exclusive non-blocking flock before doing anything else.
    # If another engine process is alive the lock is already held and we exit
    # cleanly (exit 0).  No two instances can ever both reach uvicorn.bind().
    # -------------------------------------------------------------------------
    _lock_fd = _acquire_instance_lock()  # noqa: F841 — kept alive intentionally

    host = os.getenv("SOS_ENGINE_HOST", "127.0.0.1")
    port = int(os.getenv("SOS_ENGINE_PORT", "6060"))

    # The SO_REUSEADDR pre-bind test that was here (G80) gave FALSE "port free"
    # reads — it would succeed even when a live listener existed (SO_REUSEADDR
    # is specifically designed to allow re-binding addresses, so it cannot
    # distinguish "port truly free" from "port in TIME_WAIT").  That test is
    # now REMOVED.  The single-instance lock above guarantees only one engine
    # process ever reaches this point, so a bind failure here means a genuine
    # external conflict — let uvicorn raise it directly so systemd sees a real
    # error and ExecStopPost can handle cleanup.

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level=os.getenv("SOS_LOG_LEVEL", "info"),
    )
    server = uvicorn.Server(config)
    asyncio.run(server.serve())


if __name__ == "__main__":
    main()
