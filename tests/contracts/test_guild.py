"""
§13 Guild contract tests — Sprint 003 Track C.

Integration tests require a live Mirror DB (MIRROR_DATABASE_URL or DATABASE_URL).
Unit tests (type validation) run without DB access.

Run all:     pytest tests/contracts/test_guild.py -v
Run unit:    pytest tests/contracts/test_guild.py -v -m "not db"
Run db:      pytest tests/contracts/test_guild.py -v -m db
"""
from __future__ import annotations

import os
import uuid

import pytest
from pydantic import ValidationError

from sos.contracts.guild import (
    Guild,
    GuildMember,
    GuildSpec,
    GuildTreasury,
    add_member,
    assert_member,
    can_act_for_guild,
    change_rank,
    create_guild,
    get_guild,
    get_treasury,
    list_guild_members,
    list_member_guilds,
    member_rank,
    remove_member,
)


# ── helpers ────────────────────────────────────────────────────────────────────

def _has_db() -> bool:
    return bool(os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL'))


def _slug() -> str:
    """Unique slug for each test run to avoid cross-test contamination."""
    return f'test-{uuid.uuid4().hex[:8]}'


db = pytest.mark.skipif(not _has_db(), reason='Mirror DB not configured')
requires_db = db


# ── Unit: type validation (no DB) ─────────────────────────────────────────────


class TestGuildModel:
    def test_valid_construction(self) -> None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        g = Guild(
            id='mumega-inc',
            name='Mumega Inc.',
            kind='company',
            parent_guild_id=None,
            founded_at=now,
            charter_doc_node_id=None,
            governance_tier='principal-only',
            status='active',
            metadata=None,
            created_at=now,
            updated_at=now,
        )
        assert g.id == 'mumega-inc'
        assert g.kind == 'company'
        assert g.status == 'active'

    def test_frozen(self) -> None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        g = Guild(
            id='test',
            name='Test',
            kind='project',
            parent_guild_id=None,
            founded_at=now,
            charter_doc_node_id=None,
            governance_tier='consensus',
            status='active',
            metadata=None,
            created_at=now,
            updated_at=now,
        )
        with pytest.raises(ValidationError):
            g.id = 'mutated'  # type: ignore[misc]

    def test_guild_spec_defaults(self) -> None:
        spec = GuildSpec(id='gaf', name='GAF', kind='project')
        assert spec.governance_tier == 'principal-only'
        assert spec.parent_guild_id is None
        assert spec.metadata is None

    def test_guild_spec_full(self) -> None:
        spec = GuildSpec(
            id='agentlink',
            name='AgentLink',
            kind='project',
            governance_tier='consensus',
            metadata={'public_listing': True},
        )
        assert spec.governance_tier == 'consensus'
        assert spec.metadata == {'public_listing': True}


# ── Integration: DB-backed reads + mutations ───────────────────────────────────


@requires_db
class TestCreateGuild:
    def test_creates_guild(self) -> None:
        slug = _slug()
        spec = GuildSpec(id=slug, name='Test Guild', kind='project')
        guild = create_guild(spec, created_by='kasra')

        assert guild.id == slug
        assert guild.name == 'Test Guild'
        assert guild.kind == 'project'
        assert guild.status == 'active'
        assert guild.governance_tier == 'principal-only'

    def test_idempotent_second_create(self) -> None:
        slug = _slug()
        spec = GuildSpec(id=slug, name='Idempotent Guild', kind='community')
        g1 = create_guild(spec, created_by='kasra')
        g2 = create_guild(spec, created_by='kasra')  # second call — should not raise

        assert g1.id == g2.id
        assert g1.created_at == g2.created_at

    def test_get_guild_round_trip(self) -> None:
        slug = _slug()
        spec = GuildSpec(
            id=slug,
            name='Round Trip',
            kind='company',
            governance_tier='delegated',
        )
        create_guild(spec, created_by='hadi')
        fetched = get_guild(slug)

        assert fetched is not None
        assert fetched.id == slug
        assert fetched.governance_tier == 'delegated'

    def test_get_guild_missing_returns_none(self) -> None:
        assert get_guild('definitely-not-a-real-guild-slug-xyz') is None


@requires_db
class TestMemberOps:
    def _make_guild(self) -> str:
        slug = _slug()
        create_guild(GuildSpec(id=slug, name='Member Test', kind='project'), created_by='hadi')
        return slug

    def test_add_member(self) -> None:
        slug = self._make_guild()
        m = add_member(slug, 'kasra', 'builder', added_by='hadi')

        assert m.guild_id == slug
        assert m.member_id == 'kasra'
        assert m.rank == 'builder'
        assert m.status == 'active'

    def test_add_member_idempotent_readd(self) -> None:
        slug = self._make_guild()
        add_member(slug, 'kasra', 'builder', added_by='hadi')
        # Re-add with new rank — should update, not fail
        m2 = add_member(slug, 'kasra', 'advisor', added_by='hadi')
        assert m2.rank == 'advisor'
        assert m2.status == 'active'

    def test_assert_member_true(self) -> None:
        slug = self._make_guild()
        add_member(slug, 'loom', 'coordinator', added_by='hadi')
        assert assert_member(slug, 'loom') is True

    def test_assert_member_false(self) -> None:
        slug = self._make_guild()
        assert assert_member(slug, 'nobody') is False

    def test_member_rank(self) -> None:
        slug = self._make_guild()
        add_member(slug, 'athena', 'quality_gate', added_by='hadi')
        assert member_rank(slug, 'athena') == 'quality_gate'
        assert member_rank(slug, 'nobody') is None

    def test_list_guild_members(self) -> None:
        slug = self._make_guild()
        add_member(slug, 'hadi', 'founder', added_by='hadi')
        add_member(slug, 'kasra', 'builder', added_by='hadi')
        members = list_guild_members(slug)
        member_ids = {m.member_id for m in members}
        assert {'hadi', 'kasra'}.issubset(member_ids)

    def test_list_member_guilds(self) -> None:
        slug = _slug()
        create_guild(GuildSpec(id=slug, name='Lookup Test', kind='project'), created_by='hadi')
        unique_member = f'member-{uuid.uuid4().hex[:6]}'
        add_member(slug, unique_member, 'observer', added_by='hadi')
        guilds = list_member_guilds(unique_member)
        assert any(g.id == slug for g in guilds)

    def test_change_rank(self) -> None:
        slug = self._make_guild()
        add_member(slug, 'gavin', 'partner', added_by='hadi')
        change_rank(slug, 'gavin', 'advisor', decided_by='hadi')
        assert member_rank(slug, 'gavin') == 'advisor'

    def test_change_rank_nonexistent_raises(self) -> None:
        slug = self._make_guild()
        with pytest.raises(ValueError, match='No active member'):
            change_rank(slug, 'nobody', 'founder', decided_by='hadi')

    def test_remove_member(self) -> None:
        slug = self._make_guild()
        add_member(slug, 'lex', 'advisor', added_by='hadi')
        remove_member(slug, 'lex', reason='departed', decided_by='hadi')
        assert assert_member(slug, 'lex') is False

    def test_remove_then_readd(self) -> None:
        slug = self._make_guild()
        add_member(slug, 'noor', 'operator', added_by='hadi')
        remove_member(slug, 'noor', reason='left', decided_by='hadi')
        assert assert_member(slug, 'noor') is False
        # Re-add — ON CONFLICT DO UPDATE should revive them
        add_member(slug, 'noor', 'builder', added_by='hadi')
        assert assert_member(slug, 'noor') is True
        assert member_rank(slug, 'noor') == 'builder'


@requires_db
class TestTreasury:
    def _make_guild(self) -> str:
        slug = _slug()
        create_guild(GuildSpec(id=slug, name='Treasury Test', kind='project'), created_by='hadi')
        return slug

    def test_get_treasury_missing_returns_zero(self) -> None:
        slug = self._make_guild()
        from decimal import Decimal
        assert get_treasury(slug, 'USD') == Decimal(0)


@requires_db
class TestCanActForGuild:
    def _make_guild(self) -> str:
        slug = _slug()
        create_guild(GuildSpec(id=slug, name='Perm Test', kind='project'), created_by='hadi')
        return slug

    def test_founder_can_act(self) -> None:
        slug = self._make_guild()
        add_member(slug, 'hadi', 'founder', added_by='hadi')
        assert can_act_for_guild('hadi', slug, 'any_action') is True

    def test_observer_cannot_act(self) -> None:
        slug = self._make_guild()
        add_member(slug, 'observer-user', 'observer', added_by='hadi')
        assert can_act_for_guild('observer-user', slug, 'any_action') is False

    def test_non_member_cannot_act(self) -> None:
        slug = self._make_guild()
        assert can_act_for_guild('non-member', slug, 'anything') is False
