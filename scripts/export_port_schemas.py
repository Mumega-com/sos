"""Export JSON Schemas for every Pydantic port model.

Walks :mod:`sos.contracts.ports` with :mod:`pkgutil`, discovers every
:class:`pydantic.BaseModel` subclass, and writes its
``model_json_schema()`` to
``sos/contracts/ports/schemas/<module>_<ModelName>.json``.

Two modes:

* **write** (default) — regenerate all schema files on disk.
* **``--check``** — compare what would be written to what is already on
  disk. Exit 1 with a diff-style message if anything drifts. Used by CI
  so drift forces a local ``make contracts`` before merge.

Output is byte-stable: keys sorted, two-space indent, trailing newline.
Running twice in a row is a no-op.
"""

from __future__ import annotations

import argparse
import difflib
import importlib
import inspect
import json
import logging
import pkgutil
import sys
from pathlib import Path
from typing import Iterator

from pydantic import BaseModel

logger = logging.getLogger("sos.contracts.export")

PORTS_PACKAGE = "sos.contracts.ports"
REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / "sos" / "contracts" / "ports" / "schemas"

# Make the script work regardless of how it's invoked (bare `python
# scripts/...`, from CI, from a hook, from an editor). Without this,
# import_module("sos.contracts.ports") silently returns zero ports and
# `--check` trivially passes — a false green.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _iter_port_modules() -> Iterator[str]:
    """Yield every submodule name under :data:`PORTS_PACKAGE`."""
    try:
        package = importlib.import_module(PORTS_PACKAGE)
    except ModuleNotFoundError as exc:
        # The whole pipeline hinges on finding this package. If it's
        # genuinely missing, that's an error we want to see loudly, not
        # swallow to a zero-model no-op.
        raise SystemExit(
            f"ports package {PORTS_PACKAGE} not importable "
            f"(sys.path[0]={sys.path[0]!r}): {exc}"
        ) from exc

    package_path = getattr(package, "__path__", None)
    if package_path is None:
        return

    for module_info in pkgutil.walk_packages(package_path, prefix=f"{PORTS_PACKAGE}."):
        if module_info.ispkg:
            continue
        yield module_info.name


def _discover_models() -> list[tuple[str, str, type[BaseModel]]]:
    """Return ``(module_leaf, model_name, model_cls)`` for each port model.

    ``module_leaf`` is the last segment of the dotted module path (e.g.
    ``bus`` for ``sos.contracts.ports.bus``). It's used as the filename
    prefix so schemas read naturally on disk.
    """
    discovered: list[tuple[str, str, type[BaseModel]]] = []
    for module_name in _iter_port_modules():
        module = importlib.import_module(module_name)
        leaf = module_name.rsplit(".", 1)[-1]
        for attr_name, attr in inspect.getmembers(module, inspect.isclass):
            if not issubclass(attr, BaseModel) or attr is BaseModel:
                continue
            # Only include classes defined in this module (skip re-exports).
            if attr.__module__ != module_name:
                continue
            discovered.append((leaf, attr_name, attr))
    # Sort for deterministic ordering.
    discovered.sort(key=lambda item: (item[0], item[1]))
    return discovered


def _schema_path(module_leaf: str, model_name: str) -> Path:
    return SCHEMAS_DIR / f"{module_leaf}_{model_name}.json"


def _render_schema(model: type[BaseModel]) -> str:
    schema = model.model_json_schema()
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def _write_mode() -> int:
    SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
    models = _discover_models()
    for module_leaf, model_name, model_cls in models:
        path = _schema_path(module_leaf, model_name)
        content = _render_schema(model_cls)
        if path.exists() and path.read_text(encoding="utf-8") == content:
            continue
        path.write_text(content, encoding="utf-8")
        logger.info("wrote %s", path.relative_to(REPO_ROOT))
    logger.info("exported %d port schema(s)", len(models))
    return 0


def _check_mode() -> int:
    models = _discover_models()
    drift: list[str] = []
    for module_leaf, model_name, model_cls in models:
        path = _schema_path(module_leaf, model_name)
        expected = _render_schema(model_cls)
        if not path.exists():
            drift.append(
                f"missing: {path.relative_to(REPO_ROOT)} (model "
                f"{model_cls.__module__}.{model_name})"
            )
            continue
        actual = path.read_text(encoding="utf-8")
        if actual != expected:
            diff = "".join(
                difflib.unified_diff(
                    actual.splitlines(keepends=True),
                    expected.splitlines(keepends=True),
                    fromfile=f"{path.relative_to(REPO_ROOT)} (on disk)",
                    tofile=f"{path.relative_to(REPO_ROOT)} (expected)",
                )
            )
            drift.append(diff)
    if drift:
        sys.stderr.write(
            "Port schemas are out of date. Run `make contracts` locally "
            "and commit the result.\n\n"
        )
        for entry in drift:
            sys.stderr.write(entry)
            if not entry.endswith("\n"):
                sys.stderr.write("\n")
        return 1
    logger.info("all %d port schema(s) match on-disk files", len(models))
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail (exit 1) if on-disk schemas differ from freshly generated ones.",
    )
    args = parser.parse_args()
    if args.check:
        return _check_mode()
    return _write_mode()


if __name__ == "__main__":
    sys.exit(main())
