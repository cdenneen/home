"""Strict, bounded JSON parsing and canonicalization.

The stdlib decoder is deliberately wrapped here: its defaults accept duplicate
object names and non-finite numbers, neither of which is safe for signed input.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any, NoReturn

MAX_JSON_BYTES = 4 * 1024 * 1024


class StrictJSONError(ValueError):
    """Input is not unambiguous JSON or does not satisfy its schema."""


def _reject_constant(value: str) -> NoReturn:
    raise StrictJSONError(f"non-finite JSON number is forbidden: {value}")


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise StrictJSONError(f"duplicate object member: {key}")
        value[key] = item
    return value


def loads(data: bytes | str, *, maximum: int = MAX_JSON_BYTES) -> Any:
    """Decode one bounded JSON value, rejecting duplicates and extensions."""
    if isinstance(data, bytes):
        if len(data) > maximum:
            raise StrictJSONError("JSON document exceeds size limit")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise StrictJSONError("JSON document is not UTF-8") from exc
    elif isinstance(data, str):
        if len(data.encode("utf-8")) > maximum:
            raise StrictJSONError("JSON document exceeds size limit")
        text = data
    else:
        raise StrictJSONError("JSON input must be bytes or text")
    try:
        return json.loads(
            text,
            object_pairs_hook=_object,
            parse_constant=_reject_constant,
        )
    except StrictJSONError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise StrictJSONError("invalid JSON document") from exc


def loads_canonical(data: bytes | str, *, maximum: int = MAX_JSON_BYTES) -> Any:
    """Decode a document only when its bytes are the canonical signed form."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    value = loads(raw, maximum=maximum)
    if raw != canonical(value):
        raise StrictJSONError("signed JSON is not in canonical byte form")
    return value


def canonical(value: Any) -> bytes:
    """Return the sole canonical representation used by hashes/signatures."""
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise StrictJSONError("value is not canonical JSON") from exc


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def exact_object(
    value: Any, fields: set[str] | frozenset[str], label: str
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(fields):
        raise StrictJSONError(f"{label} must contain exactly {sorted(fields)}")
    return value


def typed(value: Any, expected: type, label: str) -> Any:
    """Check a JSON type without Python's bool-is-int ambiguity."""
    if expected is int:
        valid = isinstance(value, int) and not isinstance(value, bool)
    else:
        valid = isinstance(value, expected)
    if not valid:
        raise StrictJSONError(f"{label} must be {expected.__name__}")
    return value


def validate(value: Any, schema: Mapping[str, Any], path: str = "$") -> None:
    """Validate the strict JSON-Schema subset used by Phase B artifacts.

    Unknown schema keywords fail closed. This keeps checked-in schemas useful
    without adding a runtime dependency or silently weakening validation.
    """
    known = {
        "$schema",
        "$id",
        "type",
        "const",
        "enum",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "minProperties",
        "maxProperties",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "pattern",
        "oneOf",
    }
    unknown = set(schema) - known
    if unknown:
        raise StrictJSONError(
            f"unsupported schema keyword at {path}: {sorted(unknown)}"
        )
    if "oneOf" in schema:
        branches = schema["oneOf"]
        if not isinstance(branches, list) or not branches:
            raise StrictJSONError(f"invalid oneOf at {path}")
        matches = 0
        for branch in branches:
            try:
                validate(value, branch, path)
            except StrictJSONError:
                continue
            matches += 1
        if matches != 1:
            raise StrictJSONError(f"oneOf matched {matches} branches at {path}")
        return
    if "const" in schema and value != schema["const"]:
        raise StrictJSONError(f"unexpected value at {path}")
    if "enum" in schema and value not in schema["enum"]:
        raise StrictJSONError(f"unexpected value at {path}")
    kind = schema.get("type")
    kinds: dict[str, type | tuple[type, ...]] = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "null": type(None),
    }
    if kind is not None:
        declared = kind if isinstance(kind, list) else [kind]
        if not declared or any(item not in kinds for item in declared):
            raise StrictJSONError(f"unsupported schema type at {path}")
        valid_type = any(
            not (item in {"integer", "number"} and isinstance(value, bool))
            and isinstance(value, kinds[item])
            for item in declared
        )
        if not valid_type:
            raise StrictJSONError(f"wrong type at {path}")
    if isinstance(value, dict):
        if len(value) < schema.get("minProperties", 0):
            raise StrictJSONError(f"too few properties at {path}")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            raise StrictJSONError(f"too many properties at {path}")
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list):
            raise StrictJSONError(f"invalid object schema at {path}")
        missing = set(required) - set(value)
        if missing:
            raise StrictJSONError(f"missing members at {path}: {sorted(missing)}")
        extras = set(value) - set(properties)
        additional = schema.get("additionalProperties", True)
        if extras and additional is False:
            raise StrictJSONError(f"unsupported members at {path}: {sorted(extras)}")
        if extras and isinstance(additional, dict):
            for key in extras:
                validate(value[key], additional, f"{path}.{key}")
        for key in set(value) & set(properties):
            validate(value[key], properties[key], f"{path}.{key}")
    if isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if minimum is not None and len(value) < minimum:
            raise StrictJSONError(f"too few items at {path}")
        if maximum is not None and len(value) > maximum:
            raise StrictJSONError(f"too many items at {path}")
        if "items" in schema:
            for index, item in enumerate(value):
                validate(item, schema["items"], f"{path}[{index}]")
    if isinstance(value, str):
        import re

        if len(value) < schema.get("minLength", 0):
            raise StrictJSONError(f"string too short at {path}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise StrictJSONError(f"string too long at {path}")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            raise StrictJSONError(f"string does not match pattern at {path}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise StrictJSONError(f"number too small at {path}")
        if "maximum" in schema and value > schema["maximum"]:
            raise StrictJSONError(f"number too large at {path}")


def is_json_value(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool)):
        return True
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and is_json_value(item) for key, item in value.items()
        )
    return False
