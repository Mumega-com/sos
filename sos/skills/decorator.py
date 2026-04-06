"""
@skill decorator — register Python functions as Mumega skills.

Works alongside SKILL.md files. Use @skill for quick tools,
SKILL.md for formal skills with documentation.

Usage:
    from sos.skills.decorator import skill

    @skill(labels=["seo", "audit"], keywords=["audit", "crawl"])
    def audit_site(url: str) -> dict:
        '''Run a technical SEO audit. Use when starting SEO work on a new project.'''
        ...

    # Access all decorated skills:
    from sos.skills.decorator import REGISTERED_SKILLS
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, Optional, get_type_hints

from sos.contracts.squad import (
    LoadingLevel,
    SkillDescriptor,
    SkillStatus,
    TrustTier,
)

REGISTERED_SKILLS: dict[str, SkillDescriptor] = {}

_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    dict: "object",
    list: "array",
    type(None): "null",
}


def _python_type_to_json(t: type) -> str:
    if hasattr(t, "__origin__"):
        origin = getattr(t, "__origin__", None)
        if origin is list:
            return "array"
        if origin is dict:
            return "object"
    return _TYPE_MAP.get(t, "string")


def _build_schema(func: Callable, exclude: tuple[str, ...] = ("task", "ctx", "self", "context")) -> dict[str, Any]:
    hints = {}
    try:
        hints = get_type_hints(func)
    except Exception:
        pass

    params = inspect.signature(func).parameters
    properties: dict[str, Any] = {}
    required: list[str] = []

    for name, param in params.items():
        if name in exclude:
            continue
        hint = hints.get(name, str)
        prop: dict[str, Any] = {"type": _python_type_to_json(hint)}

        # Extract description from docstring param lines
        if func.__doc__:
            for line in func.__doc__.splitlines():
                stripped = line.strip()
                if stripped.startswith(f"{name}:") or stripped.startswith(f":param {name}:"):
                    prop["description"] = stripped.split(":", 2)[-1].strip()

        properties[name] = prop
        if param.default is inspect.Parameter.empty:
            required.append(name)

    return {"type": "object", "properties": properties, "required": required}


def _build_output_schema(func: Callable) -> dict[str, Any]:
    hints = {}
    try:
        hints = get_type_hints(func)
    except Exception:
        pass

    return_type = hints.get("return", Any)
    json_type = _python_type_to_json(return_type)
    return {"type": json_type}


def skill(
    _func: Optional[Callable] = None,
    *,
    name: Optional[str] = None,
    labels: Optional[list[str]] = None,
    keywords: Optional[list[str]] = None,
    fuel_grade: str = "diesel",
    trust_tier: int = 4,
    version: str = "1.0.0",
) -> Callable:
    """Register a function as a Mumega skill.

    Can be used with or without arguments:
        @skill
        def my_func(...): ...

        @skill(labels=["seo"])
        def my_func(...): ...
    """

    def decorator(func: Callable) -> Callable:
        skill_name = name or func.__name__.replace("_", "-")
        description = func.__doc__.strip() if func.__doc__ else f"Skill: {skill_name}"

        descriptor = SkillDescriptor(
            id=skill_name,
            name=skill_name.replace("-", " ").title(),
            description=description,
            input_schema=_build_schema(func),
            output_schema=_build_output_schema(func),
            labels=labels or [],
            keywords=keywords or [],
            entrypoint=f"{func.__module__}:{func.__name__}",
            status=SkillStatus.ACTIVE,
            trust_tier=TrustTier(trust_tier),
            loading_level=LoadingLevel.INSTRUCTIONS,
            fuel_grade=fuel_grade,
            version=version,
        )

        REGISTERED_SKILLS[skill_name] = descriptor
        func._skill_descriptor = descriptor
        return func

    if _func is not None:
        return decorator(_func)
    return decorator


def list_skills() -> list[SkillDescriptor]:
    return list(REGISTERED_SKILLS.values())


def get_skill(skill_id: str) -> Optional[SkillDescriptor]:
    return REGISTERED_SKILLS.get(skill_id)
