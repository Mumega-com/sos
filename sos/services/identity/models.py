"""SQLAlchemy declarative models for the Identity service.

Mirrors the on-disk schema that ``IdentityCore._init_db`` produces
today. Source of truth for the identity.db schema going forward —
Alembic autogenerate reads ``Base.metadata`` from here.
"""
from __future__ import annotations

from sqlalchemy import Column, Integer, PrimaryKeyConstraint, Text, text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Text, primary_key=True)
    name = Column(Text)
    bio = Column(Text)
    avatar_url = Column(Text)
    level = Column(Integer, server_default=text("1"))
    xp = Column(Integer, server_default=text("0"))
    metadata_ = Column("metadata", Text)
    created_at = Column(Text)


class Guild(Base):
    __tablename__ = "guilds"

    id = Column(Text, primary_key=True)
    name = Column(Text)
    owner_id = Column(Text)
    description = Column(Text)
    metadata_ = Column("metadata", Text)
    created_at = Column(Text)


class Membership(Base):
    __tablename__ = "memberships"

    guild_id = Column(Text, nullable=False)
    user_id = Column(Text, nullable=False)
    role = Column(Text)
    joined_at = Column(Text)

    __table_args__ = (
        PrimaryKeyConstraint("guild_id", "user_id"),
    )


class Pairing(Base):
    __tablename__ = "pairings"

    code = Column(Text, primary_key=True)
    channel = Column(Text)
    sender_id = Column(Text)
    agent_id = Column(Text)
    issued_at = Column(Text)
    expires_at = Column(Text)
    status = Column(Text)
    approved_by = Column(Text)
    approved_at = Column(Text)


class Allowlist(Base):
    __tablename__ = "allowlists"

    channel = Column(Text, nullable=False)
    sender_id = Column(Text, nullable=False)
    agent_id = Column(Text, nullable=False)
    added_at = Column(Text)
    added_by = Column(Text)

    __table_args__ = (
        PrimaryKeyConstraint("channel", "sender_id", "agent_id"),
    )


__all__ = ["Base", "User", "Guild", "Membership", "Pairing", "Allowlist"]
