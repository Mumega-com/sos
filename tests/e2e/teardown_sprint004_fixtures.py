"""
Sprint 004 E2E Fixture Teardown.

Removes all rows inserted by setup_sprint004_fixtures.py.
Keyed on 'cit:*', 'q:t*', 'guild:alpha', 'guild:beta' ID prefixes.
Run after test_sprint004_e2e.py completes (pass or fail).
"""
from __future__ import annotations

import os
import sys

import psycopg2
import psycopg2.extras

_DB_URL = os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL')
if not _DB_URL:
    print('ERROR: MIRROR_DATABASE_URL or DATABASE_URL not set', file=sys.stderr)
    sys.exit(1)


def _connect():
    return psycopg2.connect(_DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def teardown(conn) -> None:
    with conn.cursor() as cur:
        # match_history — cascade-deleted when quests deleted, but also explicit for safety
        cur.execute("DELETE FROM match_history WHERE quest_id LIKE 'q:t%'")
        print(f'  match_history: {cur.rowcount} rows deleted')

        # match_history for prior offers (also keyed on q:t prefix, covered above)

        # reputation events from e2e outcomes
        cur.execute("DELETE FROM reputation_events WHERE evidence_ref LIKE 'match:%' AND holder_id LIKE 'cit:%'")
        print(f'  reputation_events (match outcomes): {cur.rowcount} rows deleted')

        # quest_vectors
        cur.execute("DELETE FROM quest_vectors WHERE quest_id LIKE 'q:t%'")
        print(f'  quest_vectors: {cur.rowcount} rows deleted')

        # quests (cascades to match_history)
        cur.execute("DELETE FROM quests WHERE id LIKE 'q:t%'")
        print(f'  quests: {cur.rowcount} rows deleted')

        # mirror_engrams (synthetic FRC verdicts)
        cur.execute("DELETE FROM mirror_engrams WHERE owner_id LIKE 'cit:%' AND content LIKE 'synthetic-e2e%'")
        print(f'  mirror_engrams (FRC): {cur.rowcount} rows deleted')

        # citizen_vectors
        cur.execute("DELETE FROM citizen_vectors WHERE holder_id LIKE 'cit:%'")
        print(f'  citizen_vectors: {cur.rowcount} rows deleted')

        # reputation_state
        cur.execute("DELETE FROM reputation_state WHERE holder_id LIKE 'cit:%'")
        print(f'  reputation_state: {cur.rowcount} rows deleted')

        # inventory_grants
        cur.execute("DELETE FROM inventory_grants WHERE holder_id LIKE 'cit:%'")
        print(f'  inventory_grants: {cur.rowcount} rows deleted')

        # guild_members
        cur.execute("DELETE FROM guild_members WHERE member_id LIKE 'cit:%'")
        print(f'  guild_members: {cur.rowcount} rows deleted')

        # guilds
        cur.execute("DELETE FROM guilds WHERE id IN ('guild:alpha', 'guild:beta')")
        print(f'  guilds: {cur.rowcount} rows deleted')

    conn.commit()


def main() -> None:
    print('Sprint 004 E2E fixture teardown starting...')
    with _connect() as conn:
        teardown(conn)
    print('Teardown complete.')


if __name__ == '__main__':
    main()
