"""
SOS CLI Package - Command-line interface for Sovereign Operating System.
"""

import importlib.util
from pathlib import Path
from typing import Any

from sos.cli.frontends import ChatConfig, get_frontend, list_frontends


def _legacy_module() -> Any:
    legacy_cli = Path(__file__).resolve().parents[1] / "cli.py"
    spec = importlib.util.spec_from_file_location("_sos_legacy_cli", legacy_cli)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load legacy CLI at {legacy_cli}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_legacy_cli = _legacy_module()
__version__ = _legacy_cli.__version__
cmd_version = _legacy_cli.cmd_version
cmd_doctor = _legacy_cli.cmd_doctor
cmd_status = _legacy_cli.cmd_status
cmd_chat = _legacy_cli.cmd_chat
cmd_start = _legacy_cli.cmd_start


def main(*args: Any, **kwargs: Any) -> Any:
    """Compatibility entrypoint for pyproject's ``sos.cli:main`` script."""
    return _legacy_cli.main(*args, **kwargs)


__all__ = [
    "get_frontend", "list_frontends", "ChatConfig",
    "cmd_version", "cmd_doctor", "cmd_status", "cmd_chat", "cmd_start",
    "main", "__version__",
]
