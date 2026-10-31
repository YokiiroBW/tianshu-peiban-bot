"""Project-owned small validators and fingerprints, reused by bounded file readers."""

from datetime import UTC, datetime
from ..contracts import Fault, canonical, digest as fingerprint, require


def now():
    return datetime.now(UTC)


def utc(value):
    return value.isoformat().replace("+00:00", "Z")


def text(value, maximum=2048):
    require(isinstance(value, str) and 0 < len(value) <= maximum, "invalid_input", 400)
    return value


def integer(value, minimum=0, maximum=2**31):
    require(type(value) is int and minimum <= value <= maximum, "invalid_input", 400)
    return value


def strings(value, maximum=2000, count=16, minimum=0):
    require(isinstance(value, list) and minimum <= len(value) <= count, "invalid_input", 400)
    return [text(item, maximum) for item in value]


def exact(value, fields):
    require(isinstance(value, dict) and set(value) == set(fields.split()), "invalid_input", 400)


def refs(value):
    from tianshu.types import SourceRef

    require(isinstance(value, (list, tuple)), "invalid_source_refs", 400)
    return tuple(item if isinstance(item, SourceRef) else SourceRef(**item) for item in value)
