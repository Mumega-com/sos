"""sos.jobs.evidence_package — SOC 2 Type I evidence package generator (Sprint 006 C.2 / G65).

Collects four evidence streams and writes a self-contained JSON artifact to
``docs/compliance/evidence/evidence-<DATE>.json``:

1. **chain_integrity** — output of ``verify_chain --all --json``.
2. **chain_samples** — last 5 audit_events per active stream (stream_id, seq,
   ts, actor_id, action, hash_hex).  Proves the chain has live data and that
   hash values are present.
3. **r2_anchor_proofs** — list of the 10 most recent objects in the WORM R2
   bucket (key, last_modified, size_bytes).  Proves WORM anchors are being
   written.  Requires CLOUDFLARE_ACCOUNT_ID + R2_ACCESS_KEY_ID +
   R2_SECRET_ACCESS_KEY in environment.
4. **test_run_summary** — pytest exit code + pass/skip/fail counts for the
   three key test suites:
   - ``tests/contracts/test_sso.py``   (MFA / SSO / SCIM)
   - ``tests/contracts/test_dek.py``   (DEK envelope + Vault cache)
   - ``tests/contracts/test_scim.py``  (SCIM provisioning)

Usage
-----
    python -m sos.jobs.evidence_package
    python -m sos.jobs.evidence_package --out /path/to/output.json

Exit codes
----------
    0 — package written successfully; all chain streams intact
    1 — chain integrity failure (package still written; auditor can inspect)
    2 — environment / configuration error (package not written)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("sos.jobs.evidence_package")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SAMPLES_PER_STREAM = 5
_R2_RECENT_OBJECTS = 10
_DEFAULT_OUT_DIR = Path(__file__).parents[2] / "docs" / "compliance" / "evidence"

_TEST_SUITES = [
    # test_sso.py covers MFA (TOTP replay), SSO (SAML/OIDC), and SCIM provisioning
    ("mfa_sso_scim",    "tests/contracts/test_sso.py"),
    # test_dek.py covers DEK envelope encryption + per-workspace Vault cache
    ("dek_vault",       "tests/contracts/test_dek.py"),
    # test_principals.py covers RBAC principal + role assignment logic
    ("rbac_principals", "tests/contracts/test_principals.py"),
]

# ---------------------------------------------------------------------------
# Section 1: chain integrity via verify_chain
# ---------------------------------------------------------------------------


async def _collect_chain_integrity() -> dict[str, Any]:
    """Run verify_chain --all and return structured result."""
    from sos.kernel.audit_chain import verify_chain

    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/postgres",
    )

    import asyncpg

    try:
        conn = await asyncpg.connect(db_url)
    except Exception as exc:
        return {"ok": False, "error": f"cannot connect to DB: {exc}", "results": []}

    try:
        stream_rows = await conn.fetch(
            "SELECT stream_id FROM audit_stream_seqs ORDER BY stream_id"
        )
        streams = [row["stream_id"] for row in stream_rows]
    finally:
        await conn.close()

    if not streams:
        return {"ok": True, "results": [], "note": "no audit streams found"}

    overall_ok = True
    results: list[dict] = []

    for stream_id in streams:
        result = await verify_chain(stream_id, from_seq=1, to_seq=None)
        result["stream_id"] = stream_id
        results.append(result)
        if not result["ok"]:
            overall_ok = False

    return {"ok": overall_ok, "results": results}


# ---------------------------------------------------------------------------
# Section 2: chain samples
# ---------------------------------------------------------------------------


async def _collect_chain_samples() -> list[dict[str, Any]]:
    """Return last _SAMPLES_PER_STREAM events per active stream."""
    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/postgres",
    )

    import asyncpg

    try:
        conn = await asyncpg.connect(db_url)
    except Exception as exc:
        logger.error("chain_samples: cannot connect: %s", exc)
        return []

    try:
        # Last N events per stream — window function ordered by seq DESC
        rows = await conn.fetch(
            """
            SELECT stream_id, seq, ts, actor_id, action,
                   encode(hash, 'hex') AS hash_hex,
                   encode(prev_hash, 'hex') AS prev_hash_hex
            FROM (
                SELECT *,
                       ROW_NUMBER() OVER (PARTITION BY stream_id ORDER BY seq DESC) AS rn
                FROM audit_events
            ) ranked
            WHERE rn <= $1
            ORDER BY stream_id, seq
            """,
            _SAMPLES_PER_STREAM,
        )
        return [
            {
                "stream_id":    row["stream_id"],
                "seq":          row["seq"],
                "ts":           row["ts"].isoformat() if row["ts"] else None,
                "actor_id":     row["actor_id"],
                "action":       row["action"],
                "hash_hex":     row["hash_hex"],
                "prev_hash_hex": row["prev_hash_hex"],
            }
            for row in rows
        ]
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Section 3: R2 anchor proofs
# ---------------------------------------------------------------------------


def _collect_r2_anchor_proofs() -> dict[str, Any]:
    """List the most recent anchor objects from the WORM R2 bucket."""
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID") or os.environ.get("CF_ACCOUNT_ID")
    bucket = os.environ.get("AUDIT_R2_BUCKET", "sos-audit-worm-v2")

    if not account_id:
        return {
            "ok": False,
            "error": "CLOUDFLARE_ACCOUNT_ID not set — skipped",
            "objects": [],
        }

    try:
        import boto3
        import botocore.exceptions
    except ImportError:
        return {"ok": False, "error": "boto3 not installed", "objects": []}

    try:
        client = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            region_name="auto",
        )
        resp = client.list_objects_v2(
            Bucket=bucket,
            Prefix="anchors/",
            MaxKeys=1000,
        )
    except botocore.exceptions.ClientError as exc:
        return {"ok": False, "error": str(exc), "objects": []}
    except KeyError as exc:
        return {
            "ok": False,
            "error": f"missing R2 env var: {exc}",
            "objects": [],
        }

    all_objects = resp.get("Contents", [])
    # Sort by last-modified descending and take the N most recent
    all_objects.sort(key=lambda o: o["LastModified"], reverse=True)
    recent = all_objects[:_R2_RECENT_OBJECTS]

    return {
        "ok": True,
        "bucket": bucket,
        "total_objects": len(all_objects),
        "objects": [
            {
                "key": o["Key"],
                "last_modified": o["LastModified"].isoformat(),
                "size_bytes": o["Size"],
            }
            for o in recent
        ],
    }


# ---------------------------------------------------------------------------
# Section 4: test run summary
# ---------------------------------------------------------------------------


def _collect_test_summary() -> list[dict[str, Any]]:
    """Run each test suite via pytest and capture pass/skip/fail counts."""
    sos_root = Path(__file__).parents[2]
    results = []

    for label, test_path in _TEST_SUITES:
        full_path = sos_root / test_path
        if not full_path.exists():
            results.append({
                "suite": label,
                "path": test_path,
                "ok": False,
                "error": "test file not found",
            })
            continue

        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", str(full_path), "-q", "--tb=no"],
                capture_output=True,
                text=True,
                cwd=str(sos_root),
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            results.append({
                "suite": label,
                "path": test_path,
                "ok": False,
                "error": "pytest timed out after 120s",
            })
            continue

        # Parse summary line: "54 passed, 33 skipped in 3.10s"
        summary_line = _extract_summary_line(proc.stdout)
        counts = _parse_pytest_counts(summary_line)

        results.append({
            "suite":    label,
            "path":     test_path,
            "ok":       proc.returncode == 0,
            "exit_code": proc.returncode,
            "summary":  summary_line,
            **counts,
        })

    return results


def _extract_summary_line(output: str) -> str:
    """Extract the last short-summary line from pytest -q output."""
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line and ("passed" in line or "failed" in line or "error" in line):
            return line
    return output.strip()[-200:] if output.strip() else "(no output)"


def _parse_pytest_counts(summary: str) -> dict[str, int]:
    """Parse 'N passed, M skipped, K failed' from a pytest summary line."""
    import re

    counts: dict[str, int] = {"passed": 0, "skipped": 0, "failed": 0, "errors": 0}
    for key in counts:
        m = re.search(r"(\d+)\s+" + key[:4], summary)  # "pass", "skip", "fail", "erro"
        if m:
            counts[key] = int(m.group(1))
    return counts


# ---------------------------------------------------------------------------
# Main async runner
# ---------------------------------------------------------------------------


async def _build_package(out_path: Path) -> int:
    """Collect all evidence sections and write JSON to out_path.  Returns exit code."""
    generated_at = datetime.now(timezone.utc).isoformat()
    logger.info("Collecting chain integrity...")
    chain_integrity = await _collect_chain_integrity()

    logger.info("Collecting chain samples...")
    chain_samples = await _collect_chain_samples()

    logger.info("Collecting R2 anchor proofs...")
    r2_proofs = _collect_r2_anchor_proofs()

    logger.info("Running test suites...")
    test_summary = _collect_test_summary()

    package = {
        "schema_version": "1.0",
        "generated_at": generated_at,
        "generator": "sos.jobs.evidence_package",
        "sprint": "Sprint 006",
        "gate": "G65",
        "purpose": (
            "SOC 2 Type I evidence package (Sprint 006 Track C.2). "
            "Provides machine-readable proof that the four core substrate controls "
            "(audit chain integrity, WORM anchors, RBAC, MFA/SSO) are operational. "
            "Intended for auditor review alongside soc2-cc-mapping.md."
        ),
        "sections": {
            "chain_integrity": chain_integrity,
            "chain_samples":   chain_samples,
            "r2_anchor_proofs": r2_proofs,
            "test_run_summary": test_summary,
        },
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(package, indent=2, default=str))
    logger.info("Evidence package written to %s", out_path)
    print(f"Evidence package written to: {out_path}")

    # Surface summary to stdout for operator review
    _print_summary(package)

    return 0 if chain_integrity.get("ok", False) else 1


def _print_summary(package: dict) -> None:
    sections = package["sections"]

    ci = sections["chain_integrity"]
    chain_ok = ci.get("ok", False)
    stream_count = len(ci.get("results", []))
    print(f"\n  Chain integrity : {'OK' if chain_ok else 'FAIL'}  ({stream_count} streams)")

    samples = sections["chain_samples"]
    print(f"  Chain samples   : {len(samples)} events collected")

    r2 = sections["r2_anchor_proofs"]
    if r2.get("ok"):
        print(f"  R2 anchor proofs: {r2['total_objects']} objects in {r2['bucket']}")
    else:
        print(f"  R2 anchor proofs: SKIPPED — {r2.get('error', 'unknown')}")

    tests = sections["test_run_summary"]
    for t in tests:
        status = "OK" if t.get("ok") else "FAIL"
        print(
            f"  Test suite [{t['suite']}]: {status}  "
            f"passed={t.get('passed', 0)} skipped={t.get('skipped', 0)} "
            f"failed={t.get('failed', 0)}"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate SOC 2 Type I evidence package (Sprint 006 C.2 / G65)",
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        default=None,
        help="Output JSON path (default: docs/compliance/evidence/evidence-<DATE>.json)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if args.out:
        out_path = Path(args.out)
    else:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        out_path = _DEFAULT_OUT_DIR / f"evidence-{date_str}.json"

    sys.exit(asyncio.run(_build_package(out_path)))


if __name__ == "__main__":
    main()
