from __future__ import annotations

from pathlib import Path


def test_default_service_map_is_public_kernel_generic() -> None:
    repo = Path(__file__).resolve().parents[3]
    text = (repo / "sos" / "services" / "dashboard" / "service_map.svg").read_text()

    forbidden = (
        "Mumega",
        "Customer Edge",
        "SaaS/Signup",
        "PRODUCT LAYER",
        "PROPRIETARY CORE",
        "Commercial overlays",
        "mcp.mumega.com",
        "app.mumega.com",
        "Stripe",
    )
    for term in forbidden:
        assert term not in text


def test_service_map_loader_accepts_operator_overlay(monkeypatch, tmp_path) -> None:
    from sos.services.dashboard.templates import sos_operator

    overlay = tmp_path / "map.svg"
    overlay.write_text("<svg><text>operator overlay</text></svg>", encoding="utf-8")

    monkeypatch.setenv("SOS_SERVICE_MAP_SVG_PATH", str(overlay))
    sos_operator._service_map_cache = None
    sos_operator._service_map_cache_path = None

    assert sos_operator._load_service_map_svg() == "<svg><text>operator overlay</text></svg>"


def test_s112_inventory_documents_required_candidates() -> None:
    repo = Path(__file__).resolve().parents[3]
    text = (repo / "docs" / "plans" / "s112-hosted-service-extraction-inventory.md").read_text()

    required = (
        "sos/services/dashboard/",
        "sos/services/economy/",
        "sos/services/analytics/",
        "sos/services/atelier/",
        "sos/services/glass/",
        "sos/docs/ux/dashboard-design.md",
    )
    for path in required:
        assert path in text
