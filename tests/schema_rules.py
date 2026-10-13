"""Shared check: a schema is valid for Claude structured outputs (strict JSON Schema, no Gemini dialect)."""

_UNSUPPORTED = {"nullable", "propertyOrdering", "minimum", "maximum", "minLength", "maxLength", "pattern",
                "minItems", "maxItems", "multipleOf"}
_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}


def assert_strict_schema(schema: dict, path: str = "$") -> None:
    """Every object lists all its properties as required and sets additionalProperties: false; types are lowercase."""
    assert isinstance(schema, dict), path
    bad = _UNSUPPORTED & set(schema)
    assert not bad, f"{path}: unsupported keywords {bad}"
    if "type" in schema:
        assert schema["type"] in _TYPES, f"{path}: type {schema['type']!r}"
    if schema.get("type") == "object":
        assert schema.get("additionalProperties") is False, f"{path}: additionalProperties must be false"
        assert schema.get("required") == list(schema["properties"]), f"{path}: required must list every property"
        for name, sub in schema["properties"].items():
            assert_strict_schema(sub, f"{path}.{name}")
    if schema.get("type") == "array":
        assert_strict_schema(schema["items"], f"{path}[]")
    for i, sub in enumerate(schema.get("anyOf", [])):
        assert_strict_schema(sub, f"{path}|{i}")
