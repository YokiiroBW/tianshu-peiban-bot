"""Strict shape validation, rule identity and the deterministic policy digest.

``parse_policy`` is a pure function: it reads no clock, opens no file, starts no process and holds
no cache, so it can run inside a request handler, a test or a future migration step. Two decisions
shape the whole module:

* the document is validated **whole**, including rules that a current evaluation would never reach.
  A regex with a syntax error may not hide behind an earlier rule, because "not evaluated yet" must
  never become "silently broken";
* the digest keeps legacy documents byte-for-byte compatible — arrays in input order, objects
  key-sorted, ``ensure_ascii=False``, compact separators and no trailing newline. The new optional
  ``negate=false`` is omitted canonically, so the default and an explicit false have one identity.
  It is an internal rule identity, not a cross-language publication contract.

Every refusal is a :class:`RuleValidationError` carrying a fixed code and the input path, and no
message ever repeats the value, the expression or the surrounding document.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Mapping

from .comparisons import validate_typed_value

from .types import (
    DOCUMENT_FIELDS,
    ERROR_DUPLICATE_GROUP_ID,
    ERROR_DUPLICATE_RULE_ID,
    ERROR_EMPTY_RULES,
    ERROR_INVALID_CASE_SENSITIVE,
    ERROR_INVALID_NEGATE,
    ERROR_INVALID_DOCUMENT,
    ERROR_INVALID_FIELD_NAME,
    ERROR_INVALID_GROUP,
    ERROR_INVALID_GROUP_ID,
    ERROR_INVALID_GROUP_LIST,
    ERROR_INVALID_OP,
    ERROR_INVALID_REVISION,
    ERROR_INVALID_RULE,
    ERROR_INVALID_RULE_ID,
    ERROR_INVALID_RULES,
    ERROR_INVALID_SCHEMA_VERSION,
    ERROR_INVALID_VALUE,
    ERROR_MISSING_FIELD,
    ERROR_TOO_MANY_GROUPS,
    ERROR_TOO_MANY_RULES,
    ERROR_UNKNOWN_FIELD,
    ERROR_VALUE_TOO_LONG,
    FIELDS,
    GROUPS_MAX,
    GROUP_FIELDS,
    ID_PATTERN,
    OPS,
    OP_REGEX,
    REGEX_VALUE_MAX,
    REVISION_MAX,
    REVISION_MIN,
    RULE_FIELDS,
    OPTIONAL_RULE_FIELDS,
    TEXT_FIELDS,
    RULES_PER_GROUP_MAX,
    Group,
    Rule,
    RulePolicy,
    RuleValidationError,
    SCHEMA_VERSION,
    VALUE_MAX,
)

GROUP_ID = re.compile(ID_PATTERN + r"\Z")
RULE_ID = GROUP_ID

_NUL = "\x00"


def _refuse(code: str, field: str) -> RuleValidationError:
    return RuleValidationError(code, field)


def _forbidden_character(text: str) -> bool:
    """True for NUL and for lone surrogate code points, and nothing else.

    NUL is refused because every transport in this feature is line- or byte-oriented, and a lone
    surrogate cannot be encoded as UTF-8 at all: accepting one would make the digest of an
    otherwise identical document depend on the container's error strategy. Ordinary control
    characters, emoji, CJK text and regular-expression metacharacters are preserved, because a
    pattern must reach the regex worker byte for byte.
    """

    if _NUL in text:
        return True
    return any(0xD800 <= ord(character) <= 0xDFFF for character in text)


def _validate_id(value: object, *, field: str, code: str) -> str:
    if not isinstance(value, str):
        raise _refuse(code, field)
    if GROUP_ID.match(value) is None:
        raise _refuse(code, field)
    return value


def _validate_value(value: object, *, field: str, op: str) -> str:
    if not isinstance(value, str):
        raise _refuse(ERROR_INVALID_VALUE, field)
    if value == "":
        raise _refuse(ERROR_INVALID_VALUE, field)
    limit = REGEX_VALUE_MAX if op == OP_REGEX else VALUE_MAX
    if len(value) > limit:
        raise _refuse(ERROR_VALUE_TOO_LONG, field)
    if _forbidden_character(value):
        raise _refuse(ERROR_INVALID_VALUE, field)
    return value


def _validate_rule(record: object, field: str) -> Rule:
    if not isinstance(record, Mapping):
        raise _refuse(ERROR_INVALID_RULE, field)
    try:
        keys = set(record)
    except TypeError as error:  # a mapping whose keys cannot be hashed
        raise _refuse(ERROR_INVALID_RULE, field) from error
    if keys - set(OPTIONAL_RULE_FIELDS) != set(RULE_FIELDS):
        code = (
            ERROR_UNKNOWN_FIELD
            if keys - set(RULE_FIELDS + OPTIONAL_RULE_FIELDS)
            else ERROR_INVALID_RULE
        )
        raise _refuse(code, field)
    rule_id = _validate_id(record["id"], field=f"{field}.id", code=ERROR_INVALID_RULE_ID)
    name = record["field"]
    if not isinstance(name, str) or name not in FIELDS:
        raise _refuse(ERROR_INVALID_FIELD_NAME, f"{field}.field")
    op = record["op"]
    if not isinstance(op, str) or op not in OPS:
        raise _refuse(ERROR_INVALID_OP, f"{field}.op")
    case_sensitive = record["case_sensitive"]
    if type(case_sensitive) is not bool:
        raise _refuse(ERROR_INVALID_CASE_SENSITIVE, f"{field}.case_sensitive")
    negate = record.get("negate", False)
    if type(negate) is not bool:
        raise _refuse(ERROR_INVALID_NEGATE, f"{field}.negate")
    value = validate_typed_value(record["value"], name, op, f"{field}.value")
    if name in TEXT_FIELDS:
        value = _validate_value(value, field=f"{field}.value", op=op)
    return Rule(
        id=rule_id, field=name, op=op, value=value, case_sensitive=case_sensitive, negate=negate
    )


def _validate_group(record: object, field: str) -> Group:
    if not isinstance(record, Mapping):
        raise _refuse(ERROR_INVALID_GROUP, field)
    try:
        keys = set(record)
    except TypeError as error:
        raise _refuse(ERROR_INVALID_GROUP, field) from error
    if keys != set(GROUP_FIELDS):
        code = ERROR_UNKNOWN_FIELD if keys - set(GROUP_FIELDS) else ERROR_INVALID_GROUP
        raise _refuse(code, field)
    group_id = _validate_id(record["id"], field=f"{field}.id", code=ERROR_INVALID_GROUP_ID)
    rules_raw = record["rules"]
    if not isinstance(rules_raw, list):
        raise _refuse(ERROR_INVALID_RULES, f"{field}.rules")
    if not rules_raw:
        raise _refuse(ERROR_EMPTY_RULES, f"{field}.rules")
    if len(rules_raw) > RULES_PER_GROUP_MAX:
        raise _refuse(ERROR_TOO_MANY_RULES, f"{field}.rules")
    rules = tuple(
        _validate_rule(entry, f"{field}.rules[{position}]")
        for position, entry in enumerate(rules_raw)
    )
    return Group(id=group_id, rules=rules)


def _check_group_budget(whitelist_raw: object, blacklist_raw: object) -> None:
    """Enforce the combined group budget before a single group field is read.

    Both lists must be real lists and their combined length must fit ``GROUPS_MAX`` *before* any
    group is validated. Otherwise an oversized document would still cost one full traversal — and a
    caller-supplied sequence could make that traversal arbitrarily large — while the answer is
    already known. Only ``len`` is used here, so nothing is iterated.
    """

    if not isinstance(whitelist_raw, list):
        raise _refuse(ERROR_INVALID_GROUP_LIST, "whitelist")
    if not isinstance(blacklist_raw, list):
        raise _refuse(ERROR_INVALID_GROUP_LIST, "blacklist")
    if len(whitelist_raw) + len(blacklist_raw) > GROUPS_MAX:
        raise _refuse(ERROR_TOO_MANY_GROUPS, "document")


def _collect_groups(raw: object, name: str) -> tuple[Group, ...]:
    """Validate one list of groups in input order.

    The list type and the combined 20-group budget are enforced by :func:`_check_group_budget`
    before this runs, so a document that is only too large is reported as one budget failure rather
    than as a shape error, and no group is read on the way to that failure.
    """

    if not isinstance(raw, list):
        # Unreachable through ``parse_policy`` (the pre-flight check refuses first), but a direct
        # caller of this helper still gets the same named refusal instead of an AttributeError.
        raise _refuse(ERROR_INVALID_GROUP_LIST, name)
    return tuple(
        _validate_group(entry, f"{name}[{position}]") for position, entry in enumerate(raw)
    )


def _register_identifiers(whitelist: tuple[Group, ...], blacklist: tuple[Group, ...]) -> None:
    """Refuse a repeated group id or rule id **anywhere** in the policy.

    Identity is policy-wide, not per list: a whitelist group and a blacklist group that share an id
    would make a trace ambiguous, and a rule id reused in another group would make an explanation
    unreadable. Lists are walked in the frozen trace order (blacklist first), so the reported
    position is both the later occurrence and the first path a reader would look at.
    """

    seen_groups: set[str] = set()
    seen_rules: set[str] = set()
    for name, groups in (("blacklist", blacklist), ("whitelist", whitelist)):
        for position, group in enumerate(groups):
            field = f"{name}[{position}]"
            if group.id in seen_groups:
                raise _refuse(ERROR_DUPLICATE_GROUP_ID, f"{field}.id")
            seen_groups.add(group.id)
            for index, rule in enumerate(group.rules):
                if rule.id in seen_rules:
                    raise _refuse(ERROR_DUPLICATE_RULE_ID, f"{field}.rules[{index}].id")
                seen_rules.add(rule.id)


def _canonical_document(
    whitelist: tuple[Group, ...], blacklist: tuple[Group, ...], revision: int
) -> dict[str, object]:
    """Rebuild the accepted document as plain JSON containers, preserving input order."""

    def rule_document(rule: Rule) -> dict[str, object]:
        return {
            "id": rule.id,
            "field": rule.field,
            "op": rule.op,
            "value": rule.value,
            "case_sensitive": rule.case_sensitive,
            **({"negate": True} if rule.negate else {}),
        }

    def group_document(group: Group) -> dict[str, object]:
        return {"id": group.id, "rules": [rule_document(rule) for rule in group.rules]}

    return {
        "schema_version": SCHEMA_VERSION,
        "revision": revision,
        "whitelist": [group_document(group) for group in whitelist],
        "blacklist": [group_document(group) for group in blacklist],
    }


def policy_digest(whitelist: tuple[Group, ...], blacklist: tuple[Group, ...], revision: int) -> str:
    """SHA256 over the canonical UTF-8 JSON encoding of the accepted document."""

    payload = json.dumps(
        _canonical_document(whitelist, blacklist, revision),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_policy(document: object) -> RulePolicy:
    """Validate one policy document and return the frozen policy plus its digest."""

    if not isinstance(document, Mapping):
        raise _refuse(ERROR_INVALID_DOCUMENT, "document")
    try:
        keys = set(document)
    except TypeError as error:
        raise _refuse(ERROR_INVALID_DOCUMENT, "document") from error
    if keys - set(DOCUMENT_FIELDS):
        raise _refuse(ERROR_UNKNOWN_FIELD, "document")
    missing = [name for name in DOCUMENT_FIELDS if name not in document]
    if missing:
        raise _refuse(ERROR_MISSING_FIELD, missing[0])

    schema_version = document["schema_version"]
    if type(schema_version) is not int or schema_version != SCHEMA_VERSION:
        raise _refuse(ERROR_INVALID_SCHEMA_VERSION, "schema_version")

    revision = document["revision"]
    if type(revision) is not int or not REVISION_MIN <= revision <= REVISION_MAX:
        raise _refuse(ERROR_INVALID_REVISION, "revision")

    _check_group_budget(document["whitelist"], document["blacklist"])
    whitelist = _collect_groups(document["whitelist"], "whitelist")
    blacklist = _collect_groups(document["blacklist"], "blacklist")
    _register_identifiers(whitelist, blacklist)

    return RulePolicy(
        revision=revision,
        whitelist=whitelist,
        blacklist=blacklist,
        policy_digest=policy_digest(whitelist, blacklist, revision),
    )


def regex_rules(policy: RulePolicy) -> tuple[tuple[int, int, Rule], ...]:
    """Every regex rule of the policy as ``(group index, rule index, rule)``.

    Group indices follow the frozen trace order — blacklist first, then whitelist — so a syntax
    failure found here can be attached to the same position an evaluation trace would use.
    """

    rules: list[tuple[int, int, Rule]] = []
    position = 0
    for group in policy.blacklist + policy.whitelist:
        for index, rule in enumerate(group.rules):
            if rule.op == OP_REGEX:
                rules.append((position, index, rule))
        position += 1
    return tuple(rules)


__all__ = [
    "GROUP_ID",
    "RULE_ID",
    "parse_policy",
    "policy_digest",
    "regex_rules",
]
