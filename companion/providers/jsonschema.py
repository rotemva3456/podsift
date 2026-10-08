"""Just enough JSON Schema to check a model's answer, with no extra dependency.

Supported: type (one or a list), enum, const, properties, required, additionalProperties,
items, minItems, maxItems, minLength, maxLength, minimum, maximum, exclusiveMinimum,
exclusiveMaximum, anyOf, oneOf (treated as anyOf), allOf and local $ref (#/$defs/…,
#/definitions/…), which covers the schemas pydantic's `model_json_schema()` writes.
Unknown keywords are ignored, so a schema never fails because of a keyword this file skips.
"""
from __future__ import annotations

from typing import Any

MAX_DEPTH = 40


class SchemaError(ValueError):
    """The schema itself can't be used (an unknown or recursive $ref)."""


def _target(root: dict, ref: str) -> dict:
    for prefix in ("#/$defs/", "#/definitions/"):
        if ref.startswith(prefix):
            found = root.get(prefix[2:-1], {}).get(ref[len(prefix):])
            if isinstance(found, dict):
                return found
    raise SchemaError(f"The schema refers to {ref!r}, which it doesn't define.")


def inline_refs(schema: dict) -> dict:
    """The schema with every local $ref replaced by what it points at, for providers that don't
    follow $ref. Raises SchemaError for a recursive schema; send those unchanged."""
    def walk(node: Any, depth: int) -> Any:
        if depth > MAX_DEPTH:
            raise SchemaError("The schema is recursive or too deep to inline.")
        if isinstance(node, list):
            return [walk(item, depth + 1) for item in node]
        if not isinstance(node, dict):
            return node
        out = {key: walk(value, depth + 1) for key, value in node.items()
               if key not in ("$ref", "$defs", "definitions")}
        if isinstance(node.get("$ref"), str):
            return {**walk(_target(schema, node["$ref"]), depth + 1), **out}
        return out
    return walk(schema, 0)


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is(value: Any, kind: str) -> bool:
    if kind == "integer":
        return (isinstance(value, int) and not isinstance(value, bool)) or (
            isinstance(value, float) and value.is_integer())
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    expected = _TYPES.get(kind)
    return expected is None or isinstance(value, expected)   # an unknown type name never fails


def errors(value: Any, schema: dict, *, limit: int = 5) -> list[str]:
    """Why `value` doesn't match `schema` (at most `limit` reasons); empty when it matches."""
    found: list[str] = []
    _check(value, schema, schema, "$", found, limit, 0)
    return found


def _check(value: Any, schema: Any, root: dict, path: str, out: list[str], limit: int, depth: int) -> None:
    if not isinstance(schema, dict) or len(out) >= limit:
        return
    if depth > MAX_DEPTH:
        out.append(f"{path}: nested too deeply")
        return
    if isinstance(schema.get("$ref"), str):
        _check(value, _target(root, schema["$ref"]), root, path, out, limit, depth + 1)
    for key in ("anyOf", "oneOf"):
        options = schema.get(key)
        if isinstance(options, list) and options and not any(
                not _sub(value, option, root, path, depth) for option in options):
            out.append(f"{path}: matches none of the allowed shapes")
    for part in schema.get("allOf", []) if isinstance(schema.get("allOf"), list) else []:
        _check(value, part, root, path, out, limit, depth + 1)
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: not one of the allowed values")
    if "const" in schema and value != schema["const"]:
        out.append(f"{path}: not the required value")
    kinds = schema.get("type")
    if kinds is not None:
        kinds = kinds if isinstance(kinds, list) else [kinds]
        if not any(_is(value, kind) for kind in kinds if isinstance(kind, str)):
            out.append(f"{path}: expected {' or '.join(map(str, kinds))}")
            return
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                out.append(f"{path}: missing {name!r}")
        properties = schema.get("properties", {}) if isinstance(schema.get("properties"), dict) else {}
        extra = schema.get("additionalProperties", True)
        for name, item in value.items():
            if name in properties:
                _check(item, properties[name], root, f"{path}.{name}", out, limit, depth + 1)
            elif extra is False:
                out.append(f"{path}: unexpected {name!r}")
            elif isinstance(extra, dict):
                _check(item, extra, root, f"{path}.{name}", out, limit, depth + 1)
    elif isinstance(value, list):
        if isinstance(schema.get("minItems"), int) and len(value) < schema["minItems"]:
            out.append(f"{path}: fewer than {schema['minItems']} items")
        if isinstance(schema.get("maxItems"), int) and len(value) > schema["maxItems"]:
            out.append(f"{path}: more than {schema['maxItems']} items")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                _check(item, schema["items"], root, f"{path}[{index}]", out, limit, depth + 1)
    elif isinstance(value, str):
        if isinstance(schema.get("minLength"), int) and len(value) < schema["minLength"]:
            out.append(f"{path}: shorter than {schema['minLength']} characters")
        if isinstance(schema.get("maxLength"), int) and len(value) > schema["maxLength"]:
            out.append(f"{path}: longer than {schema['maxLength']} characters")
    elif _is(value, "number"):
        for key, fails in (("minimum", lambda v, n: v < n), ("maximum", lambda v, n: v > n),
                           ("exclusiveMinimum", lambda v, n: v <= n), ("exclusiveMaximum", lambda v, n: v >= n)):
            limit_value = schema.get(key)
            if _is(limit_value, "number") and fails(value, limit_value):
                out.append(f"{path}: outside the allowed range")


def _sub(value: Any, schema: Any, root: dict, path: str, depth: int) -> list[str]:
    found: list[str] = []
    _check(value, schema, root, path, found, 1, depth + 1)
    return found
