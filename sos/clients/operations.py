"""SOS Operations Client — run delivery pipelines."""
from __future__ import annotations

from typing import Any

from sos.services.operations.runner import run_operation, load_template


class OperationsClient:
    """Client for running operations (local execution)."""

    def run(self, customer: str, product: str, dry_run: bool = False) -> dict:
        """Run an operation cycle for a customer."""
        return run_operation(customer, product, dry_run=dry_run)

    def dry_run(self, customer: str, product: str) -> dict:
        """Preview what an operation would do."""
        return run_operation(customer, product, dry_run=True)

    def list_templates(self) -> list[str]:
        """List available operation templates."""
        from pathlib import Path
        ops_dir = Path("/home/mumega/SOS/operations")
        return [f.stem for f in ops_dir.glob("*.yaml")]

    def get_template(self, product: str) -> dict:
        """Load an operation template."""
        return load_template(product)
