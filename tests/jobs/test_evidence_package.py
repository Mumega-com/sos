"""Tests for sos.jobs.evidence_package (Sprint 006 C.2 / G65).

TC-G65a — _extract_summary_line extracts the correct line from pytest -q output
TC-G65b — _parse_pytest_counts parses pass/skip/fail counts
TC-G65c — _collect_test_summary runs all three suites and reports correct structure
TC-G65d — package JSON schema has required top-level and section keys
TC-G65e — r2_anchor_proofs returns ok=False with explanatory error when CLOUDFLARE_ACCOUNT_ID absent
TC-G65f — evidence_package --help succeeds (CLI is wired up)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# TC-G65a — summary line extraction
# ---------------------------------------------------------------------------


class TestExtractSummaryLine:
    def test_extracts_passed_line(self) -> None:
        from sos.jobs.evidence_package import _extract_summary_line

        output = (
            ".\n"
            ".ssssssssssssssssssss\n"
            "...\n"
            "54 passed, 33 skipped in 3.10s\n"
            "\n"
        )
        result = _extract_summary_line(output)
        assert "54 passed" in result

    def test_extracts_failed_line(self) -> None:
        from sos.jobs.evidence_package import _extract_summary_line

        output = ".\nF\n2 passed, 1 failed in 0.5s\n"
        result = _extract_summary_line(output)
        assert "1 failed" in result

    def test_empty_output_returns_string(self) -> None:
        from sos.jobs.evidence_package import _extract_summary_line

        result = _extract_summary_line("")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# TC-G65b — count parsing
# ---------------------------------------------------------------------------


class TestParsePytestCounts:
    def test_all_counts_present(self) -> None:
        from sos.jobs.evidence_package import _parse_pytest_counts

        counts = _parse_pytest_counts("54 passed, 33 skipped in 3.10s")
        assert counts["passed"] == 54
        assert counts["skipped"] == 33
        assert counts["failed"] == 0

    def test_failure_count(self) -> None:
        from sos.jobs.evidence_package import _parse_pytest_counts

        counts = _parse_pytest_counts("2 passed, 1 failed in 0.5s")
        assert counts["passed"] == 2
        assert counts["failed"] == 1

    def test_zero_counts_on_empty(self) -> None:
        from sos.jobs.evidence_package import _parse_pytest_counts

        counts = _parse_pytest_counts("no tests ran")
        assert counts["passed"] == 0
        assert counts["skipped"] == 0
        assert counts["failed"] == 0


# ---------------------------------------------------------------------------
# TC-G65c — test suite collection
# ---------------------------------------------------------------------------


class TestCollectTestSummary:
    def test_returns_list_of_three_suites(self) -> None:
        from sos.jobs.evidence_package import _collect_test_summary

        results = _collect_test_summary()
        assert isinstance(results, list)
        assert len(results) == 3

    def test_each_suite_has_required_keys(self) -> None:
        from sos.jobs.evidence_package import _collect_test_summary

        results = _collect_test_summary()
        for r in results:
            assert "suite" in r
            assert "path" in r
            assert "ok" in r
            assert "passed" in r
            assert "skipped" in r
            assert "failed" in r

    def test_mfa_sso_suite_passes(self) -> None:
        from sos.jobs.evidence_package import _collect_test_summary

        results = _collect_test_summary()
        sso_suite = next(r for r in results if r["suite"] == "mfa_sso_scim")
        assert sso_suite["ok"] is True
        # test_sso.py currently has 54 passing
        assert sso_suite["passed"] >= 50

    def test_dek_vault_suite_passes(self) -> None:
        from sos.jobs.evidence_package import _collect_test_summary

        results = _collect_test_summary()
        dek_suite = next(r for r in results if r["suite"] == "dek_vault")
        assert dek_suite["ok"] is True
        assert dek_suite["passed"] >= 20

    def test_rbac_principals_suite_passes(self) -> None:
        from sos.jobs.evidence_package import _collect_test_summary

        results = _collect_test_summary()
        rbac_suite = next(r for r in results if r["suite"] == "rbac_principals")
        assert rbac_suite["ok"] is True
        assert rbac_suite["passed"] >= 8


# ---------------------------------------------------------------------------
# TC-G65d — package JSON schema
# ---------------------------------------------------------------------------


class TestPackageSchema:
    def test_package_has_required_top_level_keys(self, tmp_path: Path) -> None:
        import asyncio
        from sos.jobs.evidence_package import _build_package

        out = tmp_path / "evidence.json"
        asyncio.run(_build_package(out))

        pkg = json.loads(out.read_text())
        for key in ("schema_version", "generated_at", "generator", "sprint", "gate", "sections"):
            assert key in pkg, f"missing top-level key: {key}"

    def test_sections_has_required_keys(self, tmp_path: Path) -> None:
        import asyncio
        from sos.jobs.evidence_package import _build_package

        out = tmp_path / "evidence.json"
        asyncio.run(_build_package(out))

        sections = json.loads(out.read_text())["sections"]
        for key in ("chain_integrity", "chain_samples", "r2_anchor_proofs", "test_run_summary"):
            assert key in sections, f"missing section: {key}"

    def test_gate_id_is_g65(self, tmp_path: Path) -> None:
        import asyncio
        from sos.jobs.evidence_package import _build_package

        out = tmp_path / "evidence.json"
        asyncio.run(_build_package(out))

        pkg = json.loads(out.read_text())
        assert pkg["gate"] == "G65"


# ---------------------------------------------------------------------------
# TC-G65e — R2 proofs skip gracefully without credentials
# ---------------------------------------------------------------------------


class TestR2AnchorProofs:
    def test_returns_ok_false_without_credentials(self) -> None:
        from sos.jobs.evidence_package import _collect_r2_anchor_proofs

        env_backup = {
            k: os.environ.pop(k, None)
            for k in ("CLOUDFLARE_ACCOUNT_ID", "CF_ACCOUNT_ID")
        }
        try:
            result = _collect_r2_anchor_proofs()
            assert result["ok"] is False
            assert "error" in result
            assert result["objects"] == []
        finally:
            for k, v in env_backup.items():
                if v is not None:
                    os.environ[k] = v

    def test_result_always_has_objects_key(self) -> None:
        from sos.jobs.evidence_package import _collect_r2_anchor_proofs

        result = _collect_r2_anchor_proofs()
        assert "objects" in result
        assert isinstance(result["objects"], list)


# ---------------------------------------------------------------------------
# TC-G65f — CLI is wired up
# ---------------------------------------------------------------------------


class TestCLI:
    def test_help_exits_zero(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "sos.jobs.evidence_package", "--help"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parents[2]),
        )
        assert proc.returncode == 0
        assert "evidence package" in proc.stdout.lower() or "SOC" in proc.stdout
