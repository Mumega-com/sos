"""Contract tests for sos.bus.envelope — the canonical SOS message envelope.

These tests pin the sender/receiver contract so that a fourth drifting sender
can't silently drop message bodies (regression for the 2026-04-19 bug where
raw-string ``payload`` produced empty text on every receiver).
"""

from __future__ import annotations

import json

import pytest

from sos.bus import envelope


def test_build_returns_all_canonical_fields() -> None:
    env = envelope.build(
        msg_type="chat",
        source="agent:loom",
        target="agent:kasra",
        text="hello",
    )
    assert set(env.keys()) >= {"id", "type", "source", "target", "payload", "timestamp", "version"}
    assert env["type"] == "chat"
    assert env["source"] == "agent:loom"
    assert env["target"] == "agent:kasra"
    assert env["version"] == envelope.CANONICAL_VERSION

    payload = json.loads(env["payload"])
    assert payload["text"] == "hello"
    assert payload["source"] == "agent:loom"
    assert isinstance(payload["timestamp"], (int, float))


def test_build_requires_kind_prefix_on_source() -> None:
    with pytest.raises(ValueError, match="source must be prefixed"):
        envelope.build(msg_type="chat", source="loom", target="agent:kasra", text="hi")


def test_build_requires_kind_prefix_on_target() -> None:
    with pytest.raises(ValueError, match="target must be prefixed"):
        envelope.build(msg_type="chat", source="agent:loom", target="kasra", text="hi")


def test_build_rejects_none_text() -> None:
    with pytest.raises(ValueError, match="text must not be None"):
        envelope.build(msg_type="chat", source="agent:x", target="agent:y", text=None)  # type: ignore[arg-type]


def test_build_accepts_empty_text() -> None:
    env = envelope.build(msg_type="chat", source="agent:x", target="agent:y", text="")
    assert json.loads(env["payload"])["text"] == ""


def test_build_includes_project_when_given() -> None:
    env = envelope.build(
        msg_type="chat", source="agent:x", target="agent:y", text="hi", project="mumega"
    )
    assert env["project"] == "mumega"


def test_build_omits_project_when_missing() -> None:
    env = envelope.build(msg_type="chat", source="agent:x", target="agent:y", text="hi")
    assert "project" not in env


def test_build_merges_extras_into_payload() -> None:
    env = envelope.build(
        msg_type="remember",
        source="agent:loom",
        target="agent:loom",
        text="note body",
        extras={"remember": True, "content_type": "text/plain"},
    )
    payload = json.loads(env["payload"])
    assert payload["remember"] is True
    assert payload["content_type"] == "text/plain"
    assert payload["text"] == "note body"


def test_build_accepts_explicit_message_id() -> None:
    env = envelope.build(
        msg_type="chat",
        source="agent:x",
        target="agent:y",
        text="hi",
        message_id="fixed-id-123",
    )
    assert env["id"] == "fixed-id-123"


def test_parse_canonical_roundtrip() -> None:
    env = envelope.build(
        msg_type="chat", source="agent:loom", target="agent:kasra", text="roundtrip"
    )
    parsed = envelope.parse(env)
    assert parsed["type"] == "chat"
    assert parsed["source"] == "agent:loom"
    assert parsed["target"] == "agent:kasra"
    assert parsed["text"] == "roundtrip"
    assert isinstance(parsed["timestamp"], float)


def test_parse_tolerates_raw_string_payload() -> None:
    """The 2026-04-19 bug — raw string in payload must not drop the text."""
    fields = {
        "type": "chat",
        "source": "agent:buggy_sender",
        "target": "agent:loom",
        "payload": "this was a raw string not JSON",
    }
    parsed = envelope.parse(fields)
    assert parsed["text"] == "this was a raw string not JSON"
    assert parsed["source"] == "agent:buggy_sender"


def test_parse_tolerates_missing_payload() -> None:
    parsed = envelope.parse({"type": "heartbeat", "source": "agent:loom", "target": "squad:x"})
    assert parsed["text"] == ""
    assert parsed["source"] == "agent:loom"


def test_parse_tolerates_empty_payload_string() -> None:
    parsed = envelope.parse(
        {"type": "chat", "source": "agent:loom", "target": "agent:x", "payload": ""}
    )
    assert parsed["text"] == ""


def test_parse_tolerates_json_non_dict() -> None:
    """Payload JSON-loadable but not a dict (e.g. a bare string, number, list)."""
    fields = {
        "type": "chat",
        "source": "agent:x",
        "target": "agent:y",
        "payload": json.dumps("just a string"),
    }
    parsed = envelope.parse(fields)
    assert parsed["text"] == "just a string"


def test_parse_falls_back_to_field_source_when_payload_has_none() -> None:
    fields = {
        "type": "chat",
        "source": "agent:toplevel",
        "target": "agent:y",
        "payload": json.dumps({"text": "hi"}),
    }
    parsed = envelope.parse(fields)
    assert parsed["source"] == "agent:toplevel"


def test_parse_prefers_payload_source_over_field_source() -> None:
    """Hermes consumer does this — payload.source wins over top-level."""
    fields = {
        "type": "chat",
        "source": "agent:legacy",
        "target": "agent:y",
        "payload": json.dumps({"text": "hi", "source": "agent:canonical"}),
    }
    parsed = envelope.parse(fields)
    assert parsed["source"] == "agent:canonical"


def test_parse_extracts_extras() -> None:
    fields = {
        "type": "remember",
        "source": "agent:x",
        "target": "agent:x",
        "payload": json.dumps(
            {
                "text": "note",
                "source": "agent:x",
                "timestamp": 1234567890.0,
                "remember": True,
                "content_type": "text/plain",
            }
        ),
    }
    parsed = envelope.parse(fields)
    assert parsed["extras"] == {"remember": True, "content_type": "text/plain"}
    assert parsed["timestamp"] == 1234567890.0


def test_parse_coerces_invalid_timestamp_to_none() -> None:
    fields = {
        "type": "chat",
        "source": "agent:x",
        "target": "agent:y",
        "payload": json.dumps({"text": "hi", "timestamp": "not-a-number"}),
    }
    parsed = envelope.parse(fields)
    assert parsed["timestamp"] is None


def test_parse_returns_id_from_message_id_alias() -> None:
    """sos_mcp_sse remember path uses message_id instead of id."""
    fields = {
        "type": "send",
        "source": "agent:x",
        "target": "agent:x",
        "message_id": "uuid-abc-123",
        "payload": json.dumps({"text": "hi"}),
    }
    parsed = envelope.parse(fields)
    assert parsed["id"] == "uuid-abc-123"


# ── envelope signing (#185) — verify path, accept+flag ─────────────────────────

from sos.kernel import crypto  # noqa: E402 — co-located with the signing tests


def _keypair() -> tuple[str, str]:
    return crypto.generate_keypair()  # (private_b64, public_b64)


def _mk(signing_key: str | None = None, **over: str) -> dict[str, str]:
    """Build a stock loom→kasra envelope, optionally signed."""
    kw: dict[str, str] = dict(
        msg_type="chat", source="agent:loom", target="agent:kasra", text="hi"
    )
    kw.update(over)
    return envelope.build(signing_key=signing_key, **kw)


def test_unsigned_build_is_byte_identical_legacy_shape() -> None:
    """Omitting signing_key changes nothing: version 1.0, no sig key."""
    env = _mk()
    assert env["version"] == envelope.CANONICAL_VERSION
    assert "sig" not in env


def test_unsigned_parse_flags_unsigned() -> None:
    assert envelope.parse(_mk())["signature"] == "unsigned"


def test_signed_build_sets_version_and_sig() -> None:
    priv, _pub = _keypair()
    env = _mk(priv)
    assert env["version"] == envelope.SIGNED_VERSION
    assert env["sig"]


def test_signed_roundtrip_verifies_valid() -> None:
    priv, pub = _keypair()
    env = _mk(priv)
    resolver = lambda src: pub if src == "agent:loom" else None  # noqa: E731
    parsed = envelope.parse(env, verify_key_resolver=resolver)
    assert parsed["signature"] == "valid"
    assert parsed["text"] == "hi"  # body still parses normally


def test_signed_but_no_resolver_is_unverified() -> None:
    priv, _pub = _keypair()
    assert envelope.parse(_mk(priv))["signature"] == "unverified"


def test_resolver_without_key_is_no_key() -> None:
    priv, _pub = _keypair()
    parsed = envelope.parse(_mk(priv), verify_key_resolver=lambda _src: None)
    assert parsed["signature"] == "no_key"


def test_tampered_field_is_invalid() -> None:
    """Mutating any signed field after signing must fail verification."""
    priv, pub = _keypair()
    env = _mk(priv)
    env["target"] = "agent:victim"  # forge the recipient
    parsed = envelope.parse(env, verify_key_resolver=lambda _src: pub)
    assert parsed["signature"] == "invalid"


def test_tampered_payload_is_invalid() -> None:
    priv, pub = _keypair()
    env = _mk(priv)
    env["payload"] = json.dumps({"text": "DROP TABLE", "source": "agent:loom", "timestamp": 1})
    parsed = envelope.parse(env, verify_key_resolver=lambda _s: pub)
    assert parsed["signature"] == "invalid"


def test_impersonation_with_wrong_key_is_invalid() -> None:
    """A forger signs with their OWN key but claims another source → invalid under
    the claimed source's real key. This is the core anti-impersonation property."""
    forger_priv, _forger_pub = _keypair()
    _victim_priv, victim_pub = _keypair()
    env = _mk(forger_priv, source="agent:hadi", text="deploy")
    # Receiver resolves the REAL key for the claimed source (agent:hadi) → mismatch.
    parsed = envelope.parse(env, verify_key_resolver=lambda _s: victim_pub)
    assert parsed["signature"] == "invalid"


def test_payload_source_spoof_is_invalid() -> None:
    """P0 (gate): a valid sig for sender A whose payload claims sender B must NOT
    surface as valid/B. The sig binds the top-level source; a divergent payload
    source is an identity-spoof → invalid."""
    priv, pub = _keypair()  # loom's key
    env = _mk(priv, extras={"source": "agent:hadi"})  # signs as loom, payload says hadi
    parsed = envelope.parse(env, verify_key_resolver=lambda _s: pub)
    assert parsed["signature"] == "invalid"


def test_valid_signature_yields_signed_source_not_payload_mirror() -> None:
    """On valid, parsed['source'] is the proven (top-level, signed) identity."""
    priv, pub = _keypair()
    env = _mk(priv)  # build mirrors source into payload consistently
    parsed = envelope.parse(env, verify_key_resolver=lambda _s: pub)
    assert parsed["signature"] == "valid"
    assert parsed["source"] == "agent:loom"


def test_project_tamper_is_invalid() -> None:
    """P1 (gate): project (tenant scope) is signed — re-scoping a signed message
    to another tenant must invalidate."""
    priv, pub = _keypair()
    env = _mk(priv, project="mumega")
    env["project"] = "viamar"  # cross-tenant re-scope
    parsed = envelope.parse(env, verify_key_resolver=lambda _s: pub)
    assert parsed["signature"] == "invalid"


def test_project_is_in_signed_field_set() -> None:
    assert "project" in envelope._SIGNED_FIELDS


def test_resolver_exception_degrades_to_error_not_raise() -> None:
    """P1 (gate): a flaky key registry must not crash parse — it degrades to
    'error' (a non-trusted status), never propagates."""
    priv, _pub = _keypair()
    env = _mk(priv)

    def boom(_src: str) -> str | None:
        raise RuntimeError("key registry down")

    parsed = envelope.parse(env, verify_key_resolver=boom)  # must not raise
    assert parsed["signature"] == "error"


def test_signed_version_with_sig_stripped_flags_unsigned() -> None:
    """WARN (documented): stripping `sig` from a 1.1 envelope downgrades to
    'unsigned'. An enforcing receiver must treat version>=1.1 AND signature in
    {unsigned} as a policy failure — this test pins the downgrade behavior."""
    priv, _pub = _keypair()
    env = _mk(priv)
    assert env["version"] == envelope.SIGNED_VERSION
    del env["sig"]
    parsed = envelope.parse(env, verify_key_resolver=lambda _s: _pub)
    assert parsed["signature"] == "unsigned"
    assert parsed["version"] == envelope.SIGNED_VERSION  # the version tell survives
