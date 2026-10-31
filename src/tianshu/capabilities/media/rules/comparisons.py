"""Typed comparisons, with strict timezone-aware timestamps and closed ranges."""

import math
import re
from datetime import datetime

from .types import (
    BOOLEAN_FIELDS,
    BOOLEAN_OPS,
    ERROR_INVALID_OP,
    ERROR_INVALID_VALUE,
    FIELD_PART_COUNT,
    NUMBER_FIELDS,
    OP_BETWEEN,
    OP_EQUALS,
    OP_GT,
    OP_GTE,
    OP_LT,
    OP_LTE,
    OP_NOT_EQUALS,
    ORDERED_OPS,
    RuleValidationError,
    TEXT_FIELDS,
    TEXT_OPS,
    TIME_FIELDS,
)

_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)\Z"
)


def parse_timestamp(value):
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise ValueError("timezone-aware ISO8601 timestamp required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("timezone-aware ISO8601 timestamp required")
    return result


def validate_typed_value(value, name, op, path):
    allowed = (
        TEXT_OPS if name in TEXT_FIELDS else BOOLEAN_OPS if name in BOOLEAN_FIELDS else ORDERED_OPS
    )
    if op not in allowed:
        raise RuleValidationError(ERROR_INVALID_OP, path.rsplit(".", 1)[0] + ".op")
    if name in TEXT_FIELDS:
        return value  # Existing text validation retains its exact whitespace/size semantics.
    values = value if op == OP_BETWEEN and isinstance(value, list) and len(value) == 2 else [value]
    if op == OP_BETWEEN and (not isinstance(value, list) or len(value) != 2):
        raise RuleValidationError(ERROR_INVALID_VALUE, path)
    checked = []
    try:
        for entry in values:
            if name in BOOLEAN_FIELDS:
                if type(entry) is not bool:
                    raise ValueError
                checked.append(entry)
            elif name in NUMBER_FIELDS:
                if (
                    type(entry) not in (int, float)
                    or not math.isfinite(entry)
                    or entry < 0
                    or (name == FIELD_PART_COUNT and type(entry) is not int)
                ):
                    raise ValueError
                checked.append(entry)
            elif name in TIME_FIELDS:
                checked.append(parse_timestamp(entry))
        if op == OP_BETWEEN and checked[0] > checked[1]:
            raise ValueError
    except (ValueError, OverflowError, TypeError):
        raise RuleValidationError(ERROR_INVALID_VALUE, path) from None
    return tuple(value) if op == OP_BETWEEN else value


def typed_compare(actual, wanted, op, *, timestamp=False):
    if timestamp:
        actual = parse_timestamp(actual)
        wanted = (
            tuple(parse_timestamp(value) for value in wanted)
            if op == OP_BETWEEN
            else parse_timestamp(wanted)
        )
    if op == OP_EQUALS:
        return actual == wanted
    if op == OP_NOT_EQUALS:
        return actual != wanted
    if op == OP_GT:
        return actual > wanted
    if op == OP_GTE:
        return actual >= wanted
    if op == OP_LT:
        return actual < wanted
    if op == OP_LTE:
        return actual <= wanted
    if op == OP_BETWEEN:
        return wanted[0] <= actual <= wanted[1]
    raise ValueError(f"unknown typed media rule operation: {op}")
