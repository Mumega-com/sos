"""
Sprint 006 A.2 / G52 — Mirror API dual-instance HA tests.

Architecture under test:
  nginx :8844  →  upstream { :18844 (primary), :18845 (secondary) }

Passive health: max_fails=1 fail_timeout=5s.  Kill either instance and
the next request hits the other.  Systemd RestartSec=15 auto-recovers.

These are smoke tests — they require live services to be running.
Run:  DATABASE_URL=... pytest tests/services/test_mirror_ha.py -v
Skip: pytest tests/services/test_mirror_ha.py -v -m "not live"

TC-G52a  both instances healthy → :8844 responds
TC-G52b  primary killed → :8844 continues via secondary
TC-G52c  secondary killed → :8844 continues via primary
TC-G52d  upstream health endpoint reachable via :8844/health
"""
from __future__ import annotations

import subprocess
import time

import pytest
import requests


_PRIMARY_PORT   = 18844
_SECONDARY_PORT = 18845
_NGINX_PORT     = 8844


def _healthy(port: int, timeout: float = 3.0) -> bool:
    try:
        r = requests.get(f'http://127.0.0.1:{port}/health', timeout=timeout)
        return r.status_code == 200 and r.json().get('status') == 'healthy'
    except Exception:
        return False


def _pid_for_unit(unit: str) -> int | None:
    out = subprocess.check_output(
        ['systemctl', '--user', 'show', unit, '-p', 'MainPID', '--value'],
        text=True,
    ).strip()
    pid = int(out)
    return pid if pid > 0 else None


live = pytest.mark.skipif(
    not (_healthy(_PRIMARY_PORT) and _healthy(_SECONDARY_PORT)),
    reason='Mirror HA services not running — start mirror.service + mirror-secondary.service',
)


@live
class TestMirrorHA:
    def test_g52a_both_instances_healthy(self) -> None:
        """TC-G52a: both backend instances up, nginx upstream responds."""
        assert _healthy(_PRIMARY_PORT),   'Primary :18844 not healthy'
        assert _healthy(_SECONDARY_PORT), 'Secondary :18845 not healthy'
        assert _healthy(_NGINX_PORT),     'nginx upstream :8844 not healthy'

    def test_g52b_kill_primary_secondary_serves(self) -> None:
        """TC-G52b: kill -9 primary → :8844 continues via secondary."""
        pid = _pid_for_unit('mirror.service')
        assert pid, 'mirror.service PID not found'
        subprocess.run(['kill', '-9', str(pid)], check=True)
        time.sleep(1)  # give nginx one passive-fail window

        # :8844 must still serve (secondary absorbs traffic)
        assert _healthy(_NGINX_PORT), ':8844 failed after primary kill'

        # Let primary auto-restart (RestartSec=15) before next test
        deadline = time.time() + 20
        while time.time() < deadline:
            if _healthy(_PRIMARY_PORT):
                break
            time.sleep(1)

    def test_g52c_kill_secondary_primary_serves(self) -> None:
        """TC-G52c: kill -9 secondary → :8844 continues via primary."""
        pid = _pid_for_unit('mirror-secondary.service')
        assert pid, 'mirror-secondary.service PID not found'
        subprocess.run(['kill', '-9', str(pid)], check=True)
        time.sleep(1)

        assert _healthy(_NGINX_PORT), ':8844 failed after secondary kill'

        deadline = time.time() + 20
        while time.time() < deadline:
            if _healthy(_SECONDARY_PORT):
                break
            time.sleep(1)

    def test_g52d_health_endpoint_via_upstream(self) -> None:
        """TC-G52d: /health reachable through nginx upstream."""
        r = requests.get(f'http://127.0.0.1:{_NGINX_PORT}/health', timeout=5)
        assert r.status_code == 200
        body = r.json()
        assert body['status'] == 'healthy'
        assert body['service'] == 'mirror'
