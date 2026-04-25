"""
Sprint 004 E2E Fixture Setup — §16 Matchmaking Substrate.

Loads the controlled synthetic dataset from E2E_SPRINT_004.md §2:
  - 2 guilds (guild:alpha, guild:beta)
  - 10 citizens with reputation_state, guild_members, citizen_vectors
  - 10 citizens with inventory_grants (capabilities: cap:audit, cap:review, cap:sign)
  - 2 synthetic FRC verdicts (mirror_engrams for cit:06, cit:07)
  - 20 quests (5 per tier) with quest_vectors
  - 2 prior match_history rows for cit:10 (exploration test)

All inserts are idempotent (ON CONFLICT DO NOTHING or DO UPDATE).
Run before test_sprint004_e2e.py.
"""
from __future__ import annotations

import hashlib
import json
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


def _grant_id(holder_id: str, kind: str, ref: str) -> str:
    ref_hash = hashlib.sha256(ref.encode()).hexdigest()[:8]
    return f'inv:{kind}:{holder_id}:{ref_hash}'


# ── 16D vectors ────────────────────────────────────────────────────────────────

CITIZEN_VECTORS: dict[str, list[float]] = {
    'cit:02': [0.2] * 8 + [0.8] * 8,
    'cit:03': [0.5] * 16,
    'cit:04': [0.7, 0.8, 0.6, 0.7, 0.8, 0.6, 0.7, 0.8, 0.3, 0.2, 0.4, 0.3, 0.2, 0.4, 0.3, 0.2],
    'cit:05': [0.9] * 8 + [0.1] * 8,
    'cit:06': [0.5] * 16,
    'cit:07': [0.5] * 16,
    'cit:08': [0.7] * 16,
    'cit:09': [0.4] * 16,
    'cit:10': [0.6] * 16,
    # cit:01 intentionally absent (cold-start)
}

QUEST_VECTORS: dict[str, list[float]] = {
    'q:t1-global-01': [0.8] * 8 + [0.2] * 8,
    'q:t1-global-02': [0.2] * 8 + [0.8] * 8,
    'q:t1-alpha-01':  [0.5] * 16,
    'q:t1-alpha-02':  [0.6] * 16,
    'q:t1-global-03': [0.7] * 16,
    'q:t2-global-01': [0.9] * 8 + [0.1] * 8,
    'q:t2-global-02': [0.5] * 16,
    'q:t2-beta-01':   [0.6] * 8 + [0.4] * 8,
    'q:t2-beta-02':   [0.4] * 16,
    'q:t2-global-03': [0.8] * 16,
    'q:t3-global-01': [0.8] * 8 + [0.2] * 8,
    'q:t3-global-02': [0.7] * 16,
    'q:t3-alpha-01':  [0.6] * 16,
    'q:t3-alpha-02':  [0.9] * 8 + [0.1] * 8,
    'q:t3-global-03': [0.5] * 16,
    'q:t4-global-01': [0.9] * 8 + [0.1] * 8,
    'q:t4-global-02': [0.8] * 16,
    'q:t4-alpha-01':  [0.7] * 16,
    'q:t4-global-03': [0.1] * 8 + [0.9] * 8,
    'q:t4-global-04': [0.5] * 16,
}


def load_guilds(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO guilds (id, name, kind, governance_tier)
               VALUES
                 ('guild:alpha', 'Alpha Guild', 'project', 'principal-only'),
                 ('guild:beta',  'Beta Guild',  'project', 'principal-only')
               ON CONFLICT (id) DO NOTHING""",
        )
    conn.commit()
    print('  guilds: 2 loaded')


def load_reputation_state(conn) -> None:
    rows = [
        # (holder_id,  kind,      guild_scope, mu,       phi,       sigma,  sample_size)
        ('cit:02', 'overall', None, 0.0,  2.014732, 0.06,  0),
        ('cit:03', 'overall', None, 1.5,  1.2,      0.05,  8),
        ('cit:04', 'overall', None, 2.0,  0.8,      0.04, 15),
        ('cit:05', 'overall', None, 3.5,  0.6,      0.03, 24),
        ('cit:06', 'overall', None, 1.0,  1.5,      0.05,  6),
        ('cit:07', 'overall', None, 1.0,  1.5,      0.05,  6),
        ('cit:08', 'overall', None, 2.0,  0.8,      0.04, 15),
        ('cit:09', 'overall', None, 0.5,  2.0,      0.06,  3),
        ('cit:10', 'overall', None, 1.0,  1.2,      0.05,  5),
        # cit:01 intentionally absent (cold-start)
    ]
    with conn.cursor() as cur:
        for holder_id, kind, guild_scope, mu, phi, sigma, sample_size in rows:
            if guild_scope is None:
                cur.execute(
                    """INSERT INTO reputation_state
                           (holder_id, kind, guild_scope, mu, phi, sigma, sample_size)
                       VALUES (%s, %s, NULL, %s, %s, %s, %s)
                       ON CONFLICT (holder_id, kind) WHERE guild_scope IS NULL
                       DO UPDATE SET mu=%s, phi=%s, sigma=%s, sample_size=%s,
                                     last_updated=now()""",
                    (holder_id, kind, mu, phi, sigma, sample_size,
                     mu, phi, sigma, sample_size),
                )
    conn.commit()
    print(f'  reputation_state: {len(rows)} rows loaded')


def load_guild_members(conn) -> None:
    # (guild_id, member_id) — member_type='human', rank='member'
    memberships = [
        ('guild:alpha', 'cit:02'),
        ('guild:alpha', 'cit:03'), ('guild:beta', 'cit:03'),
        ('guild:alpha', 'cit:04'), ('guild:beta', 'cit:04'),
        ('guild:alpha', 'cit:05'), ('guild:beta', 'cit:05'),
        ('guild:alpha', 'cit:06'), ('guild:beta', 'cit:06'),
        ('guild:alpha', 'cit:07'), ('guild:beta', 'cit:07'),
        # cit:08: no guild memberships
        ('guild:alpha', 'cit:09'), ('guild:beta', 'cit:09'),
        ('guild:alpha', 'cit:10'), ('guild:beta', 'cit:10'),
    ]
    with conn.cursor() as cur:
        for guild_id, member_id in memberships:
            cur.execute(
                """INSERT INTO guild_members (guild_id, member_type, member_id, rank)
                   VALUES (%s, 'human', %s, 'member')
                   ON CONFLICT (guild_id, member_type, member_id) DO NOTHING""",
                (guild_id, member_id),
            )
    conn.commit()
    print(f'  guild_members: {len(memberships)} rows loaded')


def load_inventory_grants(conn) -> None:
    # (holder_id, capability_kind, capability_ref)
    grants = [
        # cap:audit
        ('cit:03', 'capability', 'cap:audit'),
        ('cit:04', 'capability', 'cap:audit'),
        ('cit:05', 'capability', 'cap:audit'),
        ('cit:06', 'capability', 'cap:audit'),
        ('cit:07', 'capability', 'cap:audit'),
        ('cit:10', 'capability', 'cap:audit'),
        # cap:review
        ('cit:04', 'capability', 'cap:review'),
        ('cit:05', 'capability', 'cap:review'),
        ('cit:08', 'capability', 'cap:review'),
        # cap:sign
        ('cit:05', 'capability', 'cap:sign'),
    ]
    with conn.cursor() as cur:
        for holder_id, kind, ref in grants:
            gid = _grant_id(holder_id, kind, ref)
            cur.execute(
                """INSERT INTO inventory_grants
                       (grant_id, holder_type, holder_id, capability_kind, capability_ref,
                        source_domain, granted_by, last_verified_at, status)
                   VALUES (%s, 'human', %s, %s, %s, 'e2e-fixture', 'system', now(), 'active')
                   ON CONFLICT (grant_id) DO UPDATE SET
                       status='active', last_verified_at=now()""",
                (gid, holder_id, kind, ref),
            )
    conn.commit()
    print(f'  inventory_grants: {len(grants)} rows loaded')


def load_citizen_vectors(conn) -> None:
    with conn.cursor() as cur:
        for holder_id, vector in CITIZEN_VECTORS.items():
            cur.execute(
                """INSERT INTO citizen_vectors (holder_id, vector, updated_at)
                   VALUES (%s, %s, now())
                   ON CONFLICT (holder_id) DO UPDATE SET vector=%s, updated_at=now()""",
                (holder_id, vector, vector),
            )
    conn.commit()
    print(f'  citizen_vectors: {len(CITIZEN_VECTORS)} rows loaded')


def load_frc_verdicts(conn) -> None:
    """Synthetic classifier_run_log entries in mirror_engrams for FRC Stage 2 test."""
    verdicts = [
        # (owner_id, content, confidence, days_ago)
        ('cit:06', 'synthetic-e2e-frc-failed',   0.30, 5),
        ('cit:07', 'synthetic-e2e-frc-degraded',  0.60, 5),
    ]
    with conn.cursor() as cur:
        for owner_id, content, confidence, days_ago in verdicts:
            run_log = json.dumps([{'pass_number': 1, 'confidence': confidence, 'parse_error': False}])
            cur.execute(
                """INSERT INTO mirror_engrams (owner_id, owner_type, content, timestamp, classifier_run_log)
                   VALUES (%s, 'human', %s, now() - (%s || ' days')::interval, %s::jsonb)
                   ON CONFLICT DO NOTHING""",
                (owner_id, content, str(days_ago), run_log),
            )
    conn.commit()
    print(f'  mirror_engrams (FRC verdicts): {len(verdicts)} rows loaded')


def load_quests(conn) -> None:
    quests = [
        # (id, title, tier, guild_scope, required_capabilities)
        ('q:t1-global-01', 'T1 Global Quest 01', 'T1', None,          []),
        ('q:t1-global-02', 'T1 Global Quest 02', 'T1', None,          []),
        ('q:t1-alpha-01',  'T1 Alpha Quest 01',  'T1', 'guild:alpha',  []),
        ('q:t1-alpha-02',  'T1 Alpha Quest 02',  'T1', 'guild:alpha',  [{'kind':'capability','ref':'cap:audit','action':'use'}]),
        ('q:t1-global-03', 'T1 Global Quest 03', 'T1', None,          [{'kind':'capability','ref':'cap:audit','action':'use'}]),
        ('q:t2-global-01', 'T2 Global Quest 01', 'T2', None,          []),
        ('q:t2-global-02', 'T2 Global Quest 02', 'T2', None,          [{'kind':'capability','ref':'cap:audit','action':'use'}]),
        ('q:t2-beta-01',   'T2 Beta Quest 01',   'T2', 'guild:beta',   []),
        ('q:t2-beta-02',   'T2 Beta Quest 02',   'T2', 'guild:beta',   [{'kind':'capability','ref':'cap:audit','action':'use'}]),
        ('q:t2-global-03', 'T2 Global Quest 03', 'T2', None,          [{'kind':'capability','ref':'cap:review','action':'use'}]),
        ('q:t3-global-01', 'T3 Global Quest 01', 'T3', None,          []),
        ('q:t3-global-02', 'T3 Global Quest 02', 'T3', None,          [{'kind':'capability','ref':'cap:audit','action':'use'}]),
        ('q:t3-alpha-01',  'T3 Alpha Quest 01',  'T3', 'guild:alpha',  []),
        ('q:t3-alpha-02',  'T3 Alpha Quest 02',  'T3', 'guild:alpha',  [{'kind':'capability','ref':'cap:audit','action':'use'},
                                                                         {'kind':'capability','ref':'cap:review','action':'use'}]),
        ('q:t3-global-03', 'T3 Global Quest 03', 'T3', None,          [{'kind':'capability','ref':'cap:review','action':'use'}]),
        ('q:t4-global-01', 'T4 Global Quest 01', 'T4', None,          []),
        ('q:t4-global-02', 'T4 Global Quest 02', 'T4', None,          [{'kind':'capability','ref':'cap:sign','action':'use'}]),
        ('q:t4-alpha-01',  'T4 Alpha Quest 01',  'T4', 'guild:alpha',  [{'kind':'capability','ref':'cap:sign','action':'use'}]),
        ('q:t4-global-03', 'T4 Global Quest 03', 'T4', None,          []),
        ('q:t4-global-04', 'T4 Global Quest 04', 'T4', None,          []),
    ]
    with conn.cursor() as cur:
        for qid, title, tier, guild_scope, req_caps in quests:
            cur.execute(
                """INSERT INTO quests (id, title, tier, guild_scope, required_capabilities, status, created_by)
                   VALUES (%s, %s, %s, %s, %s::jsonb, 'open', 'e2e-fixture')
                   ON CONFLICT (id) DO UPDATE SET
                       title=%s, tier=%s, guild_scope=%s,
                       required_capabilities=%s::jsonb, status='open'""",
                (qid, title, tier, guild_scope, json.dumps(req_caps),
                 title, tier, guild_scope, json.dumps(req_caps)),
            )
    conn.commit()
    print(f'  quests: {len(quests)} rows loaded')


def load_quest_vectors(conn) -> None:
    with conn.cursor() as cur:
        for quest_id, vector in QUEST_VECTORS.items():
            cur.execute(
                """INSERT INTO quest_vectors (quest_id, vector, named_dims, source, extracted_at)
                   VALUES (%s, %s, NULL, 'e2e-fixture', now())
                   ON CONFLICT (quest_id) DO UPDATE SET vector=%s, extracted_at=now()""",
                (quest_id, vector, vector),
            )
    conn.commit()
    print(f'  quest_vectors: {len(QUEST_VECTORS)} rows loaded')


def load_prior_offers(conn) -> None:
    """2 prior match_history offers for cit:10 on q:t1-global-01 (exploration TC-05)."""
    with conn.cursor() as cur:
        # Check if already exists to keep idempotent
        cur.execute(
            """SELECT COUNT(*) AS cnt FROM match_history
                WHERE candidate_id = 'cit:10'
                  AND quest_id = 'q:t1-global-01'
                  AND composite_score = 0.5
                  AND offer_count IN (1, 2)""",
        )
        existing = cur.fetchone()['cnt']
        if existing >= 2:
            print('  match_history (prior offers): already present, skipping')
            return

        cur.execute(
            """INSERT INTO match_history (quest_id, candidate_id, composite_score, offer_count, outcome, outcome_at)
               VALUES
                 ('q:t1-global-01', 'cit:10', 0.5, 1, 'abandoned', now() - interval '10 days'),
                 ('q:t1-global-01', 'cit:10', 0.5, 2, 'rejected',  now() - interval '5 days')""",
        )
    conn.commit()
    print('  match_history (prior offers): 2 rows loaded')


def main() -> None:
    print('Sprint 004 E2E fixture setup starting...')
    with _connect() as conn:
        load_guilds(conn)
        load_reputation_state(conn)
        load_guild_members(conn)
        load_inventory_grants(conn)
        load_citizen_vectors(conn)
        load_frc_verdicts(conn)
        load_quests(conn)
        load_quest_vectors(conn)
        load_prior_offers(conn)
    print('Fixture setup complete.')


if __name__ == '__main__':
    main()
