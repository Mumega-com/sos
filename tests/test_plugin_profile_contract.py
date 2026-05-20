from pathlib import Path


def test_plugin_profile_contract_documents_public_boundary() -> None:
    text = Path("docs/PLUGIN_PROFILE_CONTRACT.md").read_text(encoding="utf-8")

    assert "Public `sos` must not import a host overlay" in text
    assert "Public SOS ships the `sos.agent_profiles.AgentProfile` dataclass" in text
    assert "Forbidden shim" in text
    assert "Deletion Checklist" in text
