"""
Sprint 006 A.3 / G53 — Squad Service dual-instance HA + claim_owner_pid tests.

Architecture under test:
  nginx :8060  →  upstream { :18060 (primary), :18061 (secondary) }

DB-level atomic claim prevents double-dispatch between instances.
claim_owner_pid records the claiming process's PID; reap_stale_claims()
detects dead PIDs and resets tasks back to BACKLOG.

These are smoke tests — they require live services to be running.
Run:  pytest tests/services/test_squad_ha.py -v
Skip: pytest tests/services/test_squad_ha.py -v -m "not live"

TC-G53a  both instances healthy → :8060 responds
TC-G53b  kill -9 primary → :8060 continues via secondary
TC-G53c  kill -9 secondary → :8060 continues via primary
TC-G53d  claim_owner_pid set on claim; reaper resets task when PID is dead
TC-G53e  concurrent claim: only one instance wins (DB atomic CAS)
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
import uuid

import pytest
import requests


_PRIMARY_PORT   = 18060
_SECONDARY_PORT = 18061
_NGINX_PORT     = 8060
_AUTH_HEADER    = {"Authorization": "Bearer sk-sos-system"}


def _healthy(port: int, timeout: float = 3.0) -> bool:
    try:
        r = requests.get(f'http://127.0.0.1:{port}/health', timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def _pid_for_unit(unit: str) -> int | None:
    out = subprocess.check_output(
        ['systemctl', '--user', 'show', unit, '-p', 'MainPID', '--value'],
        text=True,
    ).strip()
    pid = int(out)
    return pid if pid > 0 else None


def _create_task(port: int, squad_id: str = 'test-squad') -> dict:
    """Create a minimal backlog task via the Squad API."""
    task_id = f'tc-g53-{uuid.uuid4().hex[:8]}'
    r = requests.post(
        f'http://127.0.0.1:{port}/tasks',
        json={
            'id': task_id,
            'squad_id': squad_id,
            'title': f'TC-G53 test task {task_id}',
            'priority': 'low',
        },
        headers=_AUTH_HEADER,
        timeout=5,
    )
    r.raise_for_status()
    body = r.json()
    # API returns {"task": {...}, "response": {...}} or just {"id": ...}
    return body.get('task') or body


def _get_task(port: int, task_id: str) -> dict:
    r = requests.get(
        f'http://127.0.0.1:{port}/tasks/{task_id}',
        headers=_AUTH_HEADER,
        timeout=5,
    )
    r.raise_for_status()
    return r.json()


def _claim_task(port: int, task_id: str, assignee: str = 'test-agent', attempt: int = 0) -> requests.Response:
    return requests.post(
        f'http://127.0.0.1:{port}/tasks/{task_id}/claim',
        json={'assignee': assignee, 'attempt': attempt},
        headers=_AUTH_HEADER,
        timeout=5,
    )


live = pytest.mark.skipif(
    not (_healthy(_PRIMARY_PORT) and _healthy(_SECONDARY_PORT)),
    reason='Squad HA services not running — start sos-squad.service + sos-squad-secondary.service',
)


@live
class TestSquadHA:
    def test_g53a_both_instances_healthy(self) -> None:
        """TC-G53a: both backend instances up, nginx upstream responds."""
        assert _healthy(_PRIMARY_PORT),   'Primary :18060 not healthy'
        assert _healthy(_SECONDARY_PORT), 'Secondary :18061 not healthy'
        assert _healthy(_NGINX_PORT),     'nginx upstream :8060 not healthy'

    def test_g53b_kill_primary_secondary_serves(self) -> None:
        """TC-G53b: kill -9 primary → :8060 continues via secondary."""
        pid = _pid_for_unit('sos-squad.service')
        assert pid, 'sos-squad.service PID not found'
        subprocess.run(['kill', '-9', str(pid)], check=True)
        time.sleep(1)

        assert _healthy(_NGINX_PORT), ':8060 failed after primary kill'

        # Wait for primary auto-restart (RestartSec=5)
        deadline = time.time() + 15
        while time.time() < deadline:
            if _healthy(_PRIMARY_PORT):
                break
            time.sleep(1)

    def test_g53c_kill_secondary_primary_serves(self) -> None:
        """TC-G53c: kill -9 secondary → :8060 continues via primary."""
        pid = _pid_for_unit('sos-squad-secondary.service')
        assert pid, 'sos-squad-secondary.service PID not found'
        subprocess.run(['kill', '-9', str(pid)], check=True)
        time.sleep(1)

        assert _healthy(_NGINX_PORT), ':8060 failed after secondary kill'

        deadline = time.time() + 15
        while time.time() < deadline:
            if _healthy(_SECONDARY_PORT):
                break
            time.sleep(1)

    def test_g53d_claim_owner_pid_and_reaper(self) -> None:
        """TC-G53d: task claim sets claim_owner_pid; reaper resets task when PID is dead."""
        from pathlib import Path
        import sqlite3
        from sos.kernel.config import DB_PATH
        from sos.services.squad.tasks import SquadTaskService
        from sos.services.squad.service import SquadDB, SquadBus

        # Create a task and claim it via the API (primary or secondary — doesn't matter)
        task = _create_task(_NGINX_PORT)
        task_id = task['id']
        claim_resp = _claim_task(_NGINX_PORT, task_id, attempt=0)
        assert claim_resp.status_code == 200, f'Claim failed: {claim_resp.text}'

        # Verify claim_owner_pid is set in the DB (non-NULL, > 0)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            'SELECT status, claim_owner_pid FROM squad_tasks WHERE id = ?',
            (task_id,),
        ).fetchone()
        assert row is not None
        assert row['status'] == 'claimed'
        assert row['claim_owner_pid'] is not None and row['claim_owner_pid'] > 0, \
            'claim_owner_pid not set on claim'

        stored_pid = row['claim_owner_pid']
        conn.close()

        # Fake a dead PID by overwriting claim_owner_pid with a PID we know is dead
        # Use PID 1 as a sentinel that os.kill(pid, 0) won't raise ProcessLookupError for,
        # so instead set a definitely-dead PID: fork + immediately exit, capture the PID.
        dead_pid = os.fork()
        if dead_pid == 0:
            os._exit(0)  # child exits immediately
        os.waitpid(dead_pid, 0)  # reap to prevent zombie; PID is now free

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute(
            'UPDATE squad_tasks SET claim_owner_pid = ? WHERE id = ?',
            (dead_pid, task_id),
        )
        conn.commit()
        conn.close()

        # Run the reaper directly
        svc = SquadTaskService(SquadDB(), SquadBus())
        reset = svc.reap_stale_claims(tenant_id=None)
        assert reset >= 1, f'reap_stale_claims reset {reset} tasks, expected ≥ 1'

        # Verify task is back in BACKLOG
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            'SELECT status, claim_owner_pid, assignee FROM squad_tasks WHERE id = ?',
            (task_id,),
        ).fetchone()
        assert row['status'] == 'backlog', f'Task status after reap: {row["status"]}'
        assert row['claim_owner_pid'] is None, 'claim_owner_pid should be NULL after reap'
        conn.close()

    def test_g53e_concurrent_claim_atomic(self) -> None:
        """TC-G53e: two simultaneous claim requests → only one wins (DB CAS)."""
        import threading

        task = _create_task(_NGINX_PORT)
        task_id = task['id']
        results: list[requests.Response] = []

        def do_claim() -> None:
            r = _claim_task(_NGINX_PORT, task_id, assignee='agent-a', attempt=0)
            results.append(r)

        t1 = threading.Thread(target=do_claim)
        t2 = threading.Thread(target=do_claim)
        t1.start(); t2.start()
        t1.join(); t2.join()

        status_codes = sorted(r.status_code for r in results)
        # One 200 (winner), one 409 (concurrent claim rejected)
        assert 200 in status_codes, f'No claim succeeded: {status_codes}'
        assert 409 in status_codes or 422 in status_codes, \
            f'Expected 409/422 for loser, got: {status_codes}'
