"""Finite, actionable diagnostics at Memory's untrusted proposal boundary."""

import json

from tianshu.runtime.errors import AppError


DIAGNOSTIC_BYTES = 1200
FRAGMENT_BYTES = 512
DIAGNOSTIC_FIELDS = (
    "path", "validator", "actual", "expected", "allowed", "missing", "extra",
    "hint", "line", "column", "position",
)
_MISSING = object()


def bounded(value, *, depth=2, characters=160, items=12):
    if isinstance(value, str):
        return value if len(value) <= characters else value[:characters] + "…"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if depth <= 0:
        return bounded(json.dumps(value, ensure_ascii=False, default=str), characters=characters)
    if isinstance(value, dict):
        return {str(key)[:64]: bounded(item, depth=depth-1, characters=characters, items=items)
                for key, item in list(value.items())[:items]}
    if isinstance(value, (tuple, list, set)):
        return [bounded(item, depth=depth-1, characters=characters, items=items)
                for item in list(value)[:items]]
    return bounded(str(value), characters=characters)


def proposal_error(code, path, *, actual=_MISSING, **details):
    data = {"category": "output_contract_violation", "path": list(path), **details}
    if actual is not _MISSING:
        data["actual"] = bounded(actual)
    return AppError(code, status=502, details=data)


def schema_error(error):
    """Select the failing discriminator branch instead of an opaque oneOf envelope."""
    if error.validator in {"oneOf", "anyOf"} and error.context:
        choices = error.schema.get(error.validator, ())
        discriminator = error.instance if isinstance(error.instance, dict) else {}
        matching = []
        for index, choice in enumerate(choices):
            properties = choice.get("properties", {})
            if any(properties.get(key, {}).get("const") == discriminator.get(key)
                   for key in ("kind", "revision_mode") if key in discriminator):
                matching.extend(item for item in error.context if item.schema_path[0] == index)
        candidates = matching or [item for item in error.context if item.validator != "const"]
        if candidates:
            return schema_error(sorted(candidates, key=lambda item: (
                -len(item.absolute_path), str(list(item.absolute_path))))[0])
    details = {"validator": error.validator, "expected": bounded(error.validator_value)}
    if error.validator == "enum":
        details["allowed"] = bounded(error.validator_value)
    if error.validator == "required" and isinstance(error.instance, dict):
        details["missing"] = sorted(set(error.validator_value) - set(error.instance))
    if error.validator in {"additionalProperties", "unevaluatedProperties"} and isinstance(error.instance, dict):
        allowed = error.schema.get("properties", {})
        if allowed:
            details["extra"] = sorted(set(error.instance) - set(allowed))
    return proposal_error("memory_consolidation_invalid", list(error.absolute_path),
                          actual=error.instance, **details)


def repair_diagnostic(error):
    data = error.details or {}
    result = {"error_code": error.code, **{key: bounded(data[key])
              for key in DIAGNOSTIC_FIELDS if key in data}}
    # Path is server-owned and must remain usable even when the bad value is big.
    if "path" in data:
        result["path"] = list(data["path"])[:20]
    if len(json.dumps(result, ensure_ascii=False).encode()) > DIAGNOSTIC_BYTES:
        result = {key: value if key in {"error_code", "path", "validator", "line", "column", "position"}
                  else bounded(value, depth=1, characters=80, items=4)
                  for key, value in result.items()}
    if len(json.dumps(result, ensure_ascii=False).encode()) > DIAGNOSTIC_BYTES:
        for key in ("allowed", "extra", "missing", "hint", "expected", "actual"):
            if key in result:
                result[key] = bounded(json.dumps(result[key], ensure_ascii=False), characters=40)
    return result


def record_context_error(path, aliases):
    aliases = list(dict.fromkeys(aliases))
    return proposal_error("memory_consolidation_record_context_required", path,
        actual=aliases, expected="record content_omitted=false",
        hint="先needs_context请求record正文；索引不是已读证据，补查不可用则unresolved。",
        requests=[{"request_id": "record_context_" + str(index+1), "kind": "record", "ref": alias,
                   "start": None, "end": None, "query": None,
                   "reason": "正文未提供，需原件后才能读取或修订"}
                  for index, alias in enumerate(aliases[:4])])
