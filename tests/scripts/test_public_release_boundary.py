from scripts.check_public_release_boundary import _matches


def test_boundary_match_exact_path():
    assert _matches("sos/bus/qnft_registry.json", "sos/bus/qnft_registry.json")


def test_boundary_match_directory_prefix():
    assert _matches("sos/services/billing/app.py", "sos/services/billing")


def test_boundary_match_glob():
    assert _matches("sos/bus/qnft_registry.json.pre-mizan-backup", "sos/bus/qnft_registry.json.*")


def test_boundary_no_false_prefix_match():
    assert not _matches("sos/services/billing_extra/app.py", "sos/services/billing")


def test_boundary_no_false_suffix_match():
    assert not _matches("docs/.sprint_markers.md", ".sprint_markers")
