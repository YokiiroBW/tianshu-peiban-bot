"""Local protocol schemas and effective configuration; never fetch schema URLs."""

import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, ValidationError, SchemaError
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable

from ..contracts import Fault, require

SCHEMAS = Path(__file__).parent / "schemas"
DOCUMENTS = [json.loads(path.read_text(encoding="utf-8")) for path in SCHEMAS.glob("*.json")]
REGISTRY = Registry().with_resources(
    (value["$id"], Resource.from_contents(value)) for value in DOCUMENTS
)


def check(schema, value):
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, registry=REGISTRY, format_checker=FormatChecker()).validate(
            value
        )
    except (ValidationError, SchemaError, ValueError, TypeError, Unresolvable):
        raise Fault("invalid_input", 400) from None


def protocol(name, value):
    check(
        next(
            document
            for document in DOCUMENTS
            if document["$id"].endswith("/" + name + ".schema.json")
        ),
        value,
    )


def capabilities(manifest):
    return {
        item["id"]
        for kind in ("required", "optional")
        for item in manifest["capabilities"][kind]
        if item["major"] == 1
    }


def merge(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        result[key] = (
            merge(result[key], value)
            if isinstance(result.get(key), dict) and isinstance(value, dict)
            else copy.deepcopy(value)
        )
    return result


def defaults(schema):
    return fill_defaults(schema, {})


def fill_defaults(schema, value):
    result = copy.deepcopy(value)
    if isinstance(result, dict):
        for key, field in schema.get("properties", {}).items():
            if field.get("x-secret"):
                continue
            if key not in result and "default" in field:
                result[key] = copy.deepcopy(field["default"])
            if key in result:
                result[key] = fill_defaults(field, result[key])
    elif isinstance(result, list):
        result = [fill_defaults(schema.get("items", {}), item) for item in result]
    return result


def secret_names(schema):
    return {key for key, value in schema.get("properties", {}).items() if value.get("x-secret")}


def validate_config(schema, value, secrets):
    names = secret_names(schema)
    require(not names.intersection(value), "invalid_input", 400)
    public = copy.deepcopy(schema)
    public["properties"] = {
        key: item for key, item in public.get("properties", {}).items() if key not in names
    }
    public["required"] = [key for key in public.get("required", []) if key not in names]
    check(public, value)
    require(
        all(secrets.get(key) for key in schema.get("required", []) if key in names),
        "config_invalid",
        400,
    )


def validate_description(description, manifest):
    protocol("contributions", description)
    ids = [item["id"] for item in description["actions"]]
    require(len(ids) == len(set(ids)), "invalid_input", 400)
    declared = capabilities(manifest)
    actions = {item["id"]: item for item in description["actions"]}
    for item in description["actions"]:
        for name in ("input_schema", "output_schema"):
            try:
                Draft202012Validator.check_schema(item[name])
            except SchemaError:
                raise Fault("invalid_input", 400) from None
        require(
            all(port["id"] in declared for port in item["required_capabilities"]),
            "invalid_input",
            400,
        )
    for item in description.get("commands", []):
        require(item["action"] in actions, "invalid_input", 400)
        action = actions[item["action"]]
        mapped = {argument["property"] for argument in item.get("arguments", [])}
        require(set(action["input_schema"].get("required", [])) <= mapped, "invalid_input", 400)
    for item in description.get("tools", []):
        require(item["action"] in actions, "invalid_input", 400)
