"""Guard: every port model has an up-to-date JSON Schema on disk.

This test walks ``sos.contracts.ports`` via :mod:`pkgutil`, finds every
:class:`pydantic.BaseModel` subclass, and checks two things:

1. ``sos/contracts/ports/schemas/<module>_<ModelName>.json`` exists.
2. Its contents match :func:`model.model_json_schema` exactly.

If either fails, the fix is to run ``make contracts`` and commit the
regenerated schemas (and any downstream TypeScript).
"""

from __future__ import annotations

import importlib
import inspect
import json
import pkgutil
from pathlib import Path

import pytest
from pydantic import BaseModel

PORTS_PACKAGE = "sos.contracts.ports"
REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMAS_DIR = REPO_ROOT / "sos" / "contracts" / "ports" / "schemas"
REGEN_HINT = "Run `make contracts` from the SOS repo to regenerate."


def _discover_port_models() -> list[tuple[str, str, type[BaseModel]]]:
    try:
        package = importlib.import_module(PORTS_PACKAGE)
    except ModuleNotFoundError:
        return []

    package_path = getattr(package, "__path__", None)
    if package_path is None:
        return []

    discovered: list[tuple[str, str, type[BaseModel]]] = []
    for module_info in pkgutil.walk_packages(package_path, prefix=f"{PORTS_PACKAGE}."):
        if module_info.ispkg:
            continue
        module = importlib.import_module(module_info.name)
        leaf = module_info.name.rsplit(".", 1)[-1]
        for attr_name, attr in inspect.getmembers(module, inspect.isclass):
            if not issubclass(attr, BaseModel) or attr is BaseModel:
                continue
            if attr.__module__ != module_info.name:
                continue
            discovered.append((leaf, attr_name, attr))
    discovered.sort(key=lambda item: (item[0], item[1]))
    return discovered


def test_all_port_schemas_are_exported_and_match() -> None:
    models = _discover_port_models()
    if not models:
        pytest.skip(
            f"{PORTS_PACKAGE} has no models yet — nothing to guard."
        )

    missing: list[str] = []
    drift: list[str] = []

    for module_leaf, model_name, model_cls in models:
        path = SCHEMAS_DIR / f"{module_leaf}_{model_name}.json"
        expected = model_cls.model_json_schema()
        if not path.exists():
            missing.append(str(path.relative_to(REPO_ROOT)))
            continue
        actual = json.loads(path.read_text(encoding="utf-8"))
        if actual != expected:
            drift.append(str(path.relative_to(REPO_ROOT)))

    failures: list[str] = []
    if missing:
        failures.append("missing schema files:\n  - " + "\n  - ".join(missing))
    if drift:
        failures.append("schemas out of date:\n  - " + "\n  - ".join(drift))

    if failures:
        pytest.fail("\n\n".join(failures) + f"\n\n{REGEN_HINT}")
