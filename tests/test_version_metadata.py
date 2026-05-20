from __future__ import annotations

import importlib.metadata
import subprocess
import sys
import tomllib
from pathlib import Path

import sos
import sos.cli


ROOT = Path(__file__).resolve().parents[1]


def test_version_metadata_is_synchronized() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    package_version = pyproject["project"]["version"]

    assert sos.__version__ == package_version
    assert sos.cli.__version__ == package_version
    assert importlib.metadata.version(pyproject["project"]["name"]) == package_version


def test_cli_version_reports_package_version() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from sos.cli import main; sys.argv=['mumega','version']; main()",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "0.10.3" in result.stdout
