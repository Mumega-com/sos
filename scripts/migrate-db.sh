#!/usr/bin/env bash
# Run Alembic migrations for one SOS service.
#
# Usage: scripts/migrate-db.sh <service>
#
# Valid services: squad, identity
#
# Each service owns its own alembic.ini and migrations. The DB URL is
# resolved by the service's env.py from its *_DB_URL env var, falling
# back to ~/.sos/data/<name>.db.
#
# Examples:
#   scripts/migrate-db.sh squad
#   SQUAD_DB_URL=sqlite:///tmp/test.db scripts/migrate-db.sh squad
#
# Not wired into service startup yet (v0.6.0 baseline only).

set -euo pipefail

SERVICE="${1:-}"

if [[ -z "$SERVICE" ]]; then
    echo "usage: $0 <service>" >&2
    echo "  services: squad, identity" >&2
    exit 64
fi

case "$SERVICE" in
    squad|identity)
        ;;
    *)
        echo "error: unknown service '$SERVICE' (expected: squad, identity)" >&2
        exit 64
        ;;
esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$REPO_ROOT/sos/services/$SERVICE/alembic.ini"

if [[ ! -f "$CONFIG" ]]; then
    echo "error: alembic config not found at $CONFIG" >&2
    exit 1
fi

exec alembic -c "$CONFIG" upgrade head
