"""Small memory-owned serialization, time and lexical rules used by all writers."""

from datetime import datetime
import re

from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError


CLAIM_PREDICATE_PATTERN = r"[a-zA-Z0-9_.-]{1,128}"


def parse_time(value):
    try:
        value = datetime.fromisoformat(value) if isinstance(value, str) else value
    except ValueError as exc:
        raise AppError("aware_datetime_required") from exc
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise AppError("aware_datetime_required")
    return value


def optional_time(value):
    return parse_time(value) if value is not None else None


def lexical(text):
    """Native simple-TS lexemes plus Chinese characters/bigrams and intact names."""
    words = re.findall(r"[A-Za-z0-9_]+|[\u3400-\u9fff]+", text.casefold())
    tokens = []
    for word in words:
        tokens.append(word)
        if re.fullmatch(r"[\u3400-\u9fff]+", word):
            tokens.extend(word)
            tokens.extend(word[index : index + 2] for index in range(len(word) - 1))
    return " ".join(dict.fromkeys(tokens))


def unique_refs(refs):
    return tuple({body_hash(ref): ref for ref in refs}.values())


def semantic_fingerprint(body):
    return body_hash(
        {key: body.get(key) for key in (
            "actor_id", "subject_id", "speaker_id", "predicate", "value", "assertion",
            "polarity", "conditions", "valid_from", "valid_until", "memory_kind",
        )}
    )
