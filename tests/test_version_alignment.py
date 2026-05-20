import importlib.metadata
import tomllib
from pathlib import Path

import sos
from sos import cli


def test_runtime_version_matches_distribution_metadata():
    try:
        expected = importlib.metadata.version("mumega")
    except importlib.metadata.PackageNotFoundError:
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        expected = tomllib.loads(pyproject.read_text())["project"]["version"]

    assert sos.__version__ == expected


def test_cli_uses_runtime_version():
    assert cli.__version__ == sos.__version__
