"""Small, network-free JSON boundary validation for runtime results.

Official runtimes validate their native structured-output formats. Grok Build
ACP currently returns text chunks, so Ionic also validates the useful JSON
Schema subset used by its judge contract. This module deliberately never
resolves remote ``$ref`` values.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .errors import RuntimeOutputError, RuntimePolicyError


_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "null": (type(None),),
}


def serialize_schema(schema: Mapping[str, Any], max_bytes: int) -> str:
    try:
        encoded = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise RuntimePolicyError(f"output schema is not JSON serializable: {exc}") from exc
    if len(encoded.encode("utf-8")) > max_bytes:
        raise RuntimePolicyError(
            f"output schema exceeds the {max_bytes}-byte runtime policy limit"
        )
    if not isinstance(schema, Mapping) or schema.get("type") != "object":
        raise RuntimePolicyError("subscription runtimes require an object-root JSON schema")
    _reject_remote_refs(schema)
    return encoded


def _reject_remote_refs(value: Any) -> None:
    if isinstance(value, Mapping):
        ref = value.get("$ref")
        if isinstance(ref, str) and not ref.startswith("#"):
            raise RuntimePolicyError("remote or filesystem JSON Schema references are disabled")
        for child in value.values():
            _reject_remote_refs(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            _reject_remote_refs(child)


def parse_json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if not candidate:
        raise RuntimeOutputError("runtime returned an empty response")
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, re.DOTALL | re.I)
    if fence:
        candidate = fence.group(1).strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise RuntimeOutputError(
            "runtime did not return a single valid JSON object"
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeOutputError("runtime returned JSON whose root is not an object")
    return value


def validate_payload(payload: Any, schema: Mapping[str, Any], path: str = "$") -> None:
    """Validate the non-ambiguous subset Ionic relies on.

    Vendor-native structured output remains authoritative. This check catches
    protocol/envelope mistakes and makes the experimental ACP path fail closed.
    """

    if "const" in schema and payload != schema["const"]:
        raise RuntimeOutputError(f"{path} does not match schema const")
    if "enum" in schema and payload not in schema["enum"]:
        raise RuntimeOutputError(f"{path} is not one of the schema enum values")

    expected = schema.get("type")
    if isinstance(expected, list):
        if not any(_matches_type(payload, item) for item in expected):
            raise RuntimeOutputError(f"{path} has the wrong JSON type")
    elif isinstance(expected, str) and not _matches_type(payload, expected):
        raise RuntimeOutputError(f"{path} must be {expected}")

    if isinstance(payload, dict):
        required = schema.get("required", [])
        if isinstance(required, list):
            missing = [key for key in required if key not in payload]
            if missing:
                raise RuntimeOutputError(f"{path} is missing required field(s): {', '.join(missing)}")
        properties = schema.get("properties", {})
        if isinstance(properties, Mapping):
            for key, child_schema in properties.items():
                if key in payload and isinstance(child_schema, Mapping):
                    validate_payload(payload[key], child_schema, f"{path}.{key}")
            if schema.get("additionalProperties") is False:
                extras = sorted(set(payload) - set(properties))
                if extras:
                    raise RuntimeOutputError(
                        f"{path} contains unexpected field(s): {', '.join(extras)}"
                    )
    elif isinstance(payload, list):
        items = schema.get("items")
        if isinstance(items, Mapping):
            for index, item in enumerate(payload):
                validate_payload(item, items, f"{path}[{index}]")


def _matches_type(value: Any, expected: str) -> bool:
    types = _JSON_TYPES.get(expected)
    if types is None:
        return True
    if expected in {"number", "integer"} and isinstance(value, bool):
        return False
    return isinstance(value, types)
