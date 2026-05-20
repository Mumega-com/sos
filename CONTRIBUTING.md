# Contributing To SOS

SOS is an open-source local-first coordination kernel for heterogeneous AI
agents. Contributions should keep the public core small, installable, and free
of Mumega-private deployment assumptions.

## Contribution Posture

Public contributions are welcome through GitHub issues and pull requests. By
contributing, you certify that you have the right to submit the work and that
your contribution is licensed under the same MIT License as this repository.
Do not submit secrets, customer data, private deployment paths, or proprietary
Mumega add-on code to the public repo.

## Before You Open A PR

1. Check whether the change belongs in public SOS or in a host overlay.
2. Open or reference a GitHub issue for non-trivial work.
3. Keep the kernel small: bus, identity, schemas, task substrate, MCP surface,
   and explicit extension contracts.
4. Add or update focused tests for behavior changes.
5. Update `CHANGELOG.md` and docs when the public surface changes.

## Public/Private Boundary

Public SOS must not contain:

- real token registries or secrets
- Mumega customer/product services
- billing, provider integration, or tenant-specific deployment modules
- private operational paths, systemd overlays, or host-only add-ons

Run the release boundary check before opening a PR:

```bash
python scripts/check_public_release_boundary.py --show-ok
```

CI also runs this check on every PR.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

For quickstart behavior, follow [docs/quickstart-local.md](docs/quickstart-local.md).

## Style

- Python 3.10+
- Type hints on public functions where practical
- Small, focused PRs
- Conventional commit-style titles are preferred
- No unrelated formatting churn

## Review Expectations

PR descriptions should include:

- what changed
- why it belongs in public SOS
- test output
- docs or changelog updates when applicable
- any residual risk or follow-up issue
