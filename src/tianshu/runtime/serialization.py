"""One JSON representation for shared dataclasses, dates, UTC times and unknown values."""

from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from collections.abc import Mapping


def wire(value):
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: wire(getattr(value, item.name)) for item in fields(value)
                if not item.name.startswith("_")}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive_datetime")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [wire(item) for item in value]
    return value
