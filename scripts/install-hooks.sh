#!/usr/bin/env bash
# Phase 1.5 — one-command hook bootstrap. Idempotent.
#
# Installs the pre-commit framework (if missing), then wires both
# the pre-commit and pre-push git hooks declared in
# .pre-commit-config.yaml at the repo root.
#
# Usage:
#   bash scripts/install-hooks.sh
#
# After install, every `git commit` runs ruff + black + import-linter +
# port schema drift (~seconds). Every `git push` runs the contract
# test suite (~10-15s). To bypass for one commit:
#   git commit --no-verify -m "reason"

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Prefer repo's .venv so the hook runs the same pre-commit that ships
# in dev deps. Fall back to pipx, then system pip with a helpful error.
if [[ -x "$REPO_ROOT/.venv/bin/pre-commit" ]]; then
  export PATH="$REPO_ROOT/.venv/bin:$PATH"
elif ! command -v pre-commit >/dev/null 2>&1; then
  echo "pre-commit not found and no .venv present."
  echo "create a venv and install dev deps first:"
  echo "  uv venv && uv pip install -e '.[dev]'"
  echo "  # or: python -m venv .venv && .venv/bin/pip install -e '.[dev]'"
  exit 1
fi

pre-commit install --hook-type pre-commit
pre-commit install --hook-type pre-push

echo ""
echo "hooks installed:"
echo "  .git/hooks/pre-commit  (ruff, black, import-linter, contracts-check)"
echo "  .git/hooks/pre-push    (pytest tests/contracts/)"
echo ""
echo "dry run:  pre-commit run --all-files"
echo "push run: pre-commit run --all-files --hook-stage push"
