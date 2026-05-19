"""Etsy receipt personalization parser.

Etsy sellers encode customization in inconsistent places: transaction
variations, personalization fields, buyer notes, or free-form labels such as
"Name for the artwork: Hadi". This parser extracts likely customer-supplied
customization without emitting buyer contact/address data.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from sos.kernel.information_event import safe_summary

_LINE_RE = re.compile(r"^\s*([^:=\-|]{2,80})\s*(?::|=|\-|\|)\s*(.{1,240})\s*$")
_BRACKET_RE = re.compile(r"\[([^\[\]]{1,120})\]")
_NOISE_VALUE_RE = re.compile(r"^(none|n/a|na|no|not applicable|skip|blank)$", re.I)
_CONTACT_KEY_RE = re.compile(
    r"(email|phone|address|first_line|second_line|city|state|zip|postal|country|buyer)",
    re.I,
)

_HIGH_SIGNAL_KEYS = {
    "personalization",
    "personalization_text",
    "personalization_info",
    "customization",
    "customizations",
    "custom_text",
    "custom_details",
    "message_from_buyer",
    "buyer_message",
    "gift_message",
    "note_from_buyer",
}

_LABEL_HINTS = {
    "name",
    "artwork name",
    "name for artwork",
    "name for the artwork",
    "child name",
    "baby name",
    "pet name",
    "family name",
    "couple name",
    "bride name",
    "groom name",
    "date",
    "birth date",
    "wedding date",
    "anniversary date",
    "quote",
    "text",
    "wording",
    "message",
    "phrase",
    "color",
    "colour",
    "font",
    "style",
}


@dataclass(frozen=True)
class PersonalizationField:
    label: str
    value: str
    source_path: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PersonalizationParseResult:
    receipt_id: str
    detected: bool
    confidence: str
    fields: list[PersonalizationField]
    raw_candidates: list[str]
    hermes_reasoning_required: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "detected": self.detected,
            "confidence": self.confidence,
            "fields": [field.to_dict() for field in self.fields],
            "raw_candidates": self.raw_candidates,
            "hermes_reasoning_required": self.hermes_reasoning_required,
            "pii_excluded": True,
        }


def parse_personalization(receipt: dict[str, Any]) -> PersonalizationParseResult:
    receipt_id = str(receipt.get("receipt_id") or receipt.get("id") or "")
    fields: list[PersonalizationField] = _extract_variation_pairs(receipt)
    raw_candidates: list[str] = []

    for path, key, value in _walk(receipt):
        if _CONTACT_KEY_RE.search(key) and not _is_candidate_key(key):
            continue
        if isinstance(value, str):
            parsed = _parse_string_value(key, value, path)
            if parsed:
                fields.extend(parsed)
            elif _is_candidate_key(key) and _clean_value(value):
                raw_candidates.append(safe_summary(value, limit=240))
        elif isinstance(value, (int, float)) and _is_candidate_key(key):
            fields.append(
                PersonalizationField(
                    label=_clean_label(key),
                    value=str(value),
                    source_path=path,
                    confidence=0.55,
                )
            )

    fields = _dedupe_fields(fields)
    raw_candidates = _dedupe_strings(raw_candidates)
    max_conf = max((field.confidence for field in fields), default=0.0)
    confidence = "none"
    if max_conf >= 0.8:
        confidence = "high"
    elif max_conf >= 0.55:
        confidence = "medium"
    elif raw_candidates:
        confidence = "low"

    return PersonalizationParseResult(
        receipt_id=receipt_id,
        detected=bool(fields),
        confidence=confidence,
        fields=fields,
        raw_candidates=raw_candidates[:10],
        hermes_reasoning_required=bool(max_conf < 0.8 and (fields or raw_candidates)),
    )


def personalization_event_payload(result: PersonalizationParseResult, *, shop_id: str) -> dict[str, Any]:
    """Build the public-safe payload for `personalization.detected`."""
    return {
        "source": "etsy",
        "shop_id": shop_id,
        "receipt_id": result.receipt_id,
        "fields": [field.to_dict() for field in result.fields],
        "confidence": result.confidence,
        "hermes_reasoning_required": result.hermes_reasoning_required,
        "pii_excluded": True,
    }


def _walk(value: Any, path: str = "receipt", key: str = "") -> list[tuple[str, str, Any]]:
    rows: list[tuple[str, str, Any]] = []
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            child_key_str = str(child_key)
            child_path = f"{path}.{child_key_str}" if path else child_key_str
            rows.append((child_path, child_key_str, child_value))
            rows.extend(_walk(child_value, child_path, child_key_str))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            child_path = f"{path}[{index}]"
            rows.extend(_walk(item, child_path, key))
    return rows


def _extract_variation_pairs(value: Any, path: str = "receipt") -> list[PersonalizationField]:
    fields: list[PersonalizationField] = []
    if isinstance(value, list):
        for index, item in enumerate(value):
            fields.extend(_extract_variation_pairs(item, f"{path}[{index}]"))
        return fields
    if not isinstance(value, dict):
        return fields

    lowered = {str(key).lower(): key for key in value}
    label_key = next(
        (
            lowered[key]
            for key in ("formatted_name", "property_name", "name", "label", "question")
            if key in lowered
        ),
        None,
    )
    value_key = next(
        (
            lowered[key]
            for key in ("formatted_value", "property_value", "value", "answer")
            if key in lowered
        ),
        None,
    )
    if label_key is not None and value_key is not None:
        label = _clean_label(str(value.get(label_key) or ""))
        parsed_value = _clean_value(str(value.get(value_key) or ""))
        if parsed_value and _label_is_personalization(label):
            fields.append(
                PersonalizationField(
                    label=label,
                    value=parsed_value,
                    source_path=path,
                    confidence=0.88,
                )
            )

    for child_key, child_value in value.items():
        fields.extend(_extract_variation_pairs(child_value, f"{path}.{child_key}"))
    return fields


def _parse_string_value(key: str, value: str, path: str) -> list[PersonalizationField]:
    text = value.replace("\r\n", "\n").strip()
    if not text or _NOISE_VALUE_RE.match(text):
        return []

    fields: list[PersonalizationField] = []
    if _is_variation_value(path, key):
        label = _clean_label(key)
        fields.append(PersonalizationField(label=label, value=safe_summary(text), source_path=path, confidence=0.65))

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        match = _LINE_RE.match(line)
        if match:
            label = _clean_label(match.group(1))
            parsed_value = _clean_value(match.group(2))
            if parsed_value and _label_is_personalization(label):
                fields.append(
                    PersonalizationField(
                        label=label,
                        value=parsed_value,
                        source_path=path,
                        confidence=0.9 if "name" in label.lower() else 0.78,
                    )
                )
                continue
        bracket_match = _BRACKET_RE.search(line)
        if bracket_match and _is_candidate_key(key):
            parsed_value = _clean_value(bracket_match.group(1))
            if parsed_value:
                fields.append(
                    PersonalizationField(
                        label=_clean_label(key),
                        value=parsed_value,
                        source_path=path,
                        confidence=0.82,
                    )
                )

    if not fields and _is_candidate_key(key):
        parsed_value = _clean_value(text)
        if parsed_value:
            fields.append(
                PersonalizationField(
                    label=_clean_label(key),
                    value=parsed_value,
                    source_path=path,
                    confidence=0.72 if key.lower() in _HIGH_SIGNAL_KEYS else 0.55,
                )
            )
    return fields


def _is_candidate_key(key: str) -> bool:
    normalized = _clean_label(key).lower()
    return normalized in _HIGH_SIGNAL_KEYS or _label_is_personalization(normalized)


def _label_is_personalization(label: str) -> bool:
    normalized = _clean_label(label).lower()
    if normalized in _LABEL_HINTS:
        return True
    return any(
        hint in normalized
        for hint in ("name", "custom", "personal", "engraving", "wording", "artwork", "quote", "message", "phrase")
    )


def _is_variation_value(path: str, key: str) -> bool:
    path_lower = path.lower()
    key_lower = key.lower()
    if not any(part in path_lower for part in ("variation", "property", "personalization")):
        return False
    return key_lower in {"formatted_value", "value", "property_value", "selected_value"}


def _clean_label(value: str) -> str:
    text = str(value or "").replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", text).strip(" :[]").title()


def _clean_value(value: str) -> str:
    text = str(value or "").strip().strip("[]")
    text = re.sub(r"\s+", " ", text)
    if not text or _NOISE_VALUE_RE.match(text):
        return ""
    if len(text) > 240:
        text = safe_summary(text, limit=240)
    return text


def _dedupe_fields(fields: list[PersonalizationField]) -> list[PersonalizationField]:
    result: list[PersonalizationField] = []
    seen: set[tuple[str, str]] = set()
    for field in sorted(fields, key=lambda item: item.confidence, reverse=True):
        key = (field.label.lower(), field.value.lower())
        if key in seen:
            continue
        seen.add(key)
        result.append(field)
    return result[:20]


def _dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result
