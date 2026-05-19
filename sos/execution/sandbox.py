from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass(frozen=True)
class SandboxPolicy:
    """
    Minimal subprocess execution policy for v0.1.

    Note: This is not a hardened OS sandbox. It provides timeouts, controlled
    environment variables, and consistent output capture. Filesystem/network
    isolation is enforced at higher layers (Tools service) and will later be
    backed by real sandboxing (containers/seccomp).
    """

    timeout_seconds: float = 5.0
    env_allowlist: List[str] = field(default_factory=list)
    extra_env: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SandboxResult:
    success: bool
    exit_code: Optional[int]
    stdout: str
    stderr: str
    duration_ms: int
    error: Optional[str] = None


def _build_env(policy: SandboxPolicy) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if path := os.environ.get("PATH"):
        env["PATH"] = path
    for key in policy.env_allowlist:
        if key in os.environ:
            env[key] = os.environ[key]
    env.update(policy.extra_env)
    return env


def run_subprocess(
    args: List[str],
    *,
    policy: SandboxPolicy,
    cwd: Optional[Path] = None,
    input_text: Optional[str] = None,
) -> SandboxResult:
    start = time.perf_counter()
    try:
        completed = subprocess.run(
            args,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=policy.timeout_seconds,
            cwd=str(cwd) if cwd is not None else None,
            env=_build_env(policy) or None,
        )
        duration_ms = int((time.perf_counter() - start) * 1000)
        return SandboxResult(
            success=completed.returncode == 0,
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            duration_ms=duration_ms,
            error=None if completed.returncode == 0 else "nonzero_exit",
        )
    except subprocess.TimeoutExpired as e:
        duration_ms = int((time.perf_counter() - start) * 1000)
        return SandboxResult(
            success=False,
            exit_code=None,
            stdout=(e.stdout or "") if isinstance(e.stdout, str) else "",
            stderr=(e.stderr or "") if isinstance(e.stderr, str) else "",
            duration_ms=duration_ms,
            error="timeout",
        )

