"""Contract tests for Tenant v1 — JSON Schema + Pydantic binding.

Freeze point for the Tenant shape shared between saas, billing and cli.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from pydantic import ValidationError

from sos.contracts.tenant import (
    SCHEMA_PATH,
    Tenant,
    TenantCreate,
    TenantPlan,
    TenantStatus,
    TenantUpdate,
    load_schema,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def schema() -> dict[str, Any]:
    return load_schema()


def _tenant_kwargs() -> dict[str, Any]:
    return {
        "slug": "acme",
        "label": "ACME Corp",
        "email": "ops@acme.test",
        "subdomain": "acme.mumega.com",
        "plan": TenantPlan.GROWTH,
        "status": TenantStatus.ACTIVE,
        "created_at": "2026-04-17T12:00:00Z",
        "updated_at": "2026-04-17T12:00:00Z",
    }


# ---------------------------------------------------------------------------
# 1. Schema itself is a valid Draft 2020-12 document
# ---------------------------------------------------------------------------


def test_schema_is_valid_draft_2020_12(schema):
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_schema_path_exists():
    assert SCHEMA_PATH.exists()
    assert SCHEMA_PATH.name == "tenant_v1.json"


def test_schema_defs_contain_expected_shapes(schema):
    defs = schema["$defs"]
    assert {"Tenant", "TenantCreate", "TenantUpdate", "TenantPlan", "TenantStatus"} <= set(defs)


# ---------------------------------------------------------------------------
# 2. Pydantic round-trip: model → dump → validate → equal
# ---------------------------------------------------------------------------


def test_tenant_roundtrip():
    t = Tenant(**_tenant_kwargs())
    t2 = Tenant.model_validate(t.model_dump())
    assert t2 == t


def test_tenant_create_roundtrip():
    payload = {
        "slug": "acme",
        "label": "ACME",
        "email": "hi@acme.test",
        "plan": TenantPlan.STARTER,
        "industry": "saas",
        "services": ["consulting", "support"],
    }
    tc = TenantCreate(**payload)
    tc2 = TenantCreate.model_validate(tc.model_dump())
    assert tc2 == tc


def test_tenant_update_roundtrip_partial():
    tu = TenantUpdate(status=TenantStatus.SUSPENDED, label="Renamed")
    tu2 = TenantUpdate.model_validate(tu.model_dump())
    assert tu2 == tu
    # Unset fields remain None
    assert tu2.plan is None
    assert tu2.stripe_customer_id is None


# ---------------------------------------------------------------------------
# 3. JSON Schema conformance from Pydantic dump
# ---------------------------------------------------------------------------


def test_tenant_dump_validates_against_schema(schema):
    t = Tenant(**_tenant_kwargs())
    jsonschema.validate(
        instance=t.model_dump(mode="json"),
        schema={"$ref": "#/$defs/Tenant", **schema},
    )


def test_tenant_create_dump_validates_against_schema(schema):
    tc = TenantCreate(slug="acme", label="ACME", email="hi@acme.test")
    jsonschema.validate(
        instance=tc.model_dump(mode="json", exclude_none=True),
        schema={"$ref": "#/$defs/TenantCreate", **schema},
    )


# ---------------------------------------------------------------------------
# 4. extra='forbid' — unknown fields rejected
# ---------------------------------------------------------------------------


def test_tenant_extra_fields_rejected():
    with pytest.raises(ValidationError):
        Tenant(**_tenant_kwargs(), unknown_field="nope")


def test_tenant_create_extra_fields_rejected():
    with pytest.raises(ValidationError):
        TenantCreate(slug="acme", label="ACME", email="hi@acme.test", nonsense=True)


def test_tenant_update_extra_fields_rejected():
    with pytest.raises(ValidationError):
        TenantUpdate(label="x", not_a_field=1)


# ---------------------------------------------------------------------------
# 5. Enum constraints — invalid status/plan raise ValidationError
# ---------------------------------------------------------------------------


def test_tenant_invalid_plan_rejected():
    bad = _tenant_kwargs()
    bad["plan"] = "enterprise"  # not in enum
    with pytest.raises(ValidationError):
        Tenant(**bad)


def test_tenant_invalid_status_rejected():
    bad = _tenant_kwargs()
    bad["status"] = "dormant"  # not in enum
    with pytest.raises(ValidationError):
        Tenant(**bad)


def test_tenant_update_invalid_plan_rejected():
    with pytest.raises(ValidationError):
        TenantUpdate(plan="platinum")


# ---------------------------------------------------------------------------
# 6. Slug pattern constraint
# ---------------------------------------------------------------------------


def test_tenant_create_rejects_bad_slug():
    with pytest.raises(ValidationError):
        TenantCreate(slug="ACME Corp", label="ACME", email="hi@acme.test")


def test_tenant_create_rejects_short_slug():
    with pytest.raises(ValidationError):
        TenantCreate(slug="a", label="ACME", email="hi@acme.test")
