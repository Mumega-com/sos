from pathlib import Path


def test_operations_dir_prefers_explicit_env(monkeypatch, tmp_path: Path) -> None:
    from sos.services.operations import runner

    explicit = tmp_path / "explicit"
    explicit.mkdir()
    monkeypatch.setenv("SOS_OPERATIONS_DIR", str(explicit))
    monkeypatch.setenv("SOS_ADDONS_ROOT", str(tmp_path / "addons"))

    assert runner.resolve_operations_dir() == explicit


def test_operations_dir_uses_addons_root_before_legacy_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from sos.services.operations import runner

    addons_ops = tmp_path / "addons" / "operations"
    addons_ops.mkdir(parents=True)
    missing_root = tmp_path / "missing-sos-root"
    monkeypatch.delenv("SOS_OPERATIONS_DIR", raising=False)
    monkeypatch.setenv("SOS_ADDONS_ROOT", str(tmp_path / "addons"))
    monkeypatch.setattr(runner, "_SOS_ROOT", missing_root)

    assert runner.resolve_operations_dir() == addons_ops


def test_load_template_reads_from_addons_root(monkeypatch, tmp_path: Path) -> None:
    from sos.services.operations import runner

    addons_ops = tmp_path / "addons" / "operations"
    addons_ops.mkdir(parents=True)
    (addons_ops / "content-writer.yaml").write_text("product: content-writer\n", encoding="utf-8")
    monkeypatch.delenv("SOS_OPERATIONS_DIR", raising=False)
    monkeypatch.setenv("SOS_ADDONS_ROOT", str(tmp_path / "addons"))
    monkeypatch.setattr(runner, "_SOS_ROOT", tmp_path / "missing-sos-root")

    assert runner.load_template("content-writer")["product"] == "content-writer"
