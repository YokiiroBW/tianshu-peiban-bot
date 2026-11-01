"""PostgreSQL-safe display/derived text; transport originals stay archived."""

import re

UNSTORABLE = re.compile(r"[\x00\ud800-\udfff]")


def database_text(value):
    return UNSTORABLE.sub("\ufffd", value)


def database_value(value):
    if isinstance(value, str):
        return database_text(value)
    if isinstance(value, dict):
        return {database_text(key) if isinstance(key, str) else key: database_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [database_value(item) for item in value]
    return value


def needs_normalization(value):
    if isinstance(value, str):
        return bool(UNSTORABLE.search(value))
    if isinstance(value, dict):
        return any(needs_normalization(key) or needs_normalization(item) for key, item in value.items())
    if isinstance(value, (tuple, list)):
        return any(needs_normalization(item) for item in value)
    return False
