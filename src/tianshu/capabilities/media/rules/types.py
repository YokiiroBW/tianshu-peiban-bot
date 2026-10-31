"""Immutable public value types, failure vocabulary and named errors for subscription rules.

TS-098 is the rule/试算 sub-block of the subscription feature: it turns one subscription policy
document and one normalized metadata record into an explained decision. Nothing here reads a clock,
a file, the network, an environment variable or a database, and nothing here can enqueue a download.

Three properties of the published vocabulary matter to every consumer:

* a *decision* is one of ``download`` / ``skip`` / ``rule_error`` and never silently turns a rule
  failure into "the blacklist did not match";
* a *trace* records only group and rule identity, the field and operation name, one of the four
  result words and a fixed reason word. Titles, descriptions, uploader nicknames, tags and the rule
  values themselves never enter a decision, so an explanation can be logged or shown without
  copying source text;
* every public value is a frozen dataclass over tuples, so a caller cannot mutate a decision in
  place nor smuggle a mutable ``dict``/``list`` out of the evaluator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = 1

REVISION_MIN = 1
REVISION_MAX = 2147483647

ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}"
SNAPSHOT_REVISION_PATTERN = r"[A-Za-z0-9_-]{1,128}"

GROUPS_MAX = 20
RULES_PER_GROUP_MIN = 1
RULES_PER_GROUP_MAX = 10
VALUE_MAX = 4096
REGEX_VALUE_MAX = 512

FIELD_TITLE = "title"
FIELD_DESCRIPTION = "description"
FIELD_UPLOADER_ID = "uploader_id"
FIELD_UPLOADER_NAME = "uploader_name"
FIELD_TAGS = "tags"
TEXT_FIELDS: tuple[str, ...] = (
    FIELD_TITLE,
    FIELD_DESCRIPTION,
    FIELD_UPLOADER_ID,
    FIELD_UPLOADER_NAME,
    FIELD_TAGS,
)
FIELD_PUBLISHED_AT = "published_at"
FIELD_FAVORITED_AT = "favorited_at"
FIELD_PART_COUNT = "part_count"
FIELD_DURATION_SECONDS = "duration_seconds"
FIELD_IS_COLLABORATION = "is_collaboration"
TIME_FIELDS = (FIELD_PUBLISHED_AT, FIELD_FAVORITED_AT)
NUMBER_FIELDS = (FIELD_PART_COUNT, FIELD_DURATION_SECONDS)
BOOLEAN_FIELDS = (FIELD_IS_COLLABORATION,)
FIELDS = TEXT_FIELDS + TIME_FIELDS + NUMBER_FIELDS + BOOLEAN_FIELDS

OP_EQUALS = "equals"
OP_CONTAINS = "contains"
OP_PREFIX = "prefix"
OP_SUFFIX = "suffix"
OP_REGEX = "regex"
OP_NOT_EQUALS = "not_equals"
OP_GT = "gt"
OP_GTE = "gte"
OP_LT = "lt"
OP_LTE = "lte"
OP_BETWEEN = "between"
TEXT_OPS = (OP_EQUALS, OP_CONTAINS, OP_PREFIX, OP_SUFFIX, OP_REGEX, OP_NOT_EQUALS)
ORDERED_OPS = (OP_EQUALS, OP_NOT_EQUALS, OP_GT, OP_GTE, OP_LT, OP_LTE, OP_BETWEEN)
BOOLEAN_OPS = (OP_EQUALS, OP_NOT_EQUALS)
OPS = TEXT_OPS + (OP_GT, OP_GTE, OP_LT, OP_LTE, OP_BETWEEN)
REGEX_OPS: tuple[str, ...] = (OP_REGEX,)

DOCUMENT_FIELDS: tuple[str, ...] = ("schema_version", "revision", "whitelist", "blacklist")
GROUP_FIELDS: tuple[str, ...] = ("id", "rules")
RULE_FIELDS: tuple[str, ...] = ("id", "field", "op", "value", "case_sensitive")
OPTIONAL_RULE_FIELDS = ("negate",)

LIST_BLACKLIST = "blacklist"
LIST_WHITELIST = "whitelist"

DECISION_DOWNLOAD = "download"
DECISION_SKIP = "skip"
DECISION_RULE_ERROR = "rule_error"
DECISIONS: tuple[str, ...] = (DECISION_DOWNLOAD, DECISION_SKIP, DECISION_RULE_ERROR)

REASON_ELIGIBLE = "eligible"
REASON_INACCESSIBLE = "inaccessible"
REASON_QUALITY_SATISFIED = "quality_satisfied"
REASON_BLACKLIST_MATCH = "blacklist_match"
REASON_WHITELIST_NO_MATCH = "whitelist_no_match"
NORMAL_REASONS: tuple[str, ...] = (
    REASON_ELIGIBLE,
    REASON_INACCESSIBLE,
    REASON_QUALITY_SATISFIED,
    REASON_BLACKLIST_MATCH,
    REASON_WHITELIST_NO_MATCH,
)

REASON_REGEX_TIMEOUT = "regex_timeout"
REASON_REGEX_WORKER_FAILED = "regex_worker_failed"
REASON_EVALUATION_TIMEOUT = "evaluation_timeout"
REASON_BUSY = "busy"
REASON_INPUT_TOO_LARGE = "input_too_large"
FAILURE_REASONS: tuple[str, ...] = (
    REASON_REGEX_TIMEOUT,
    REASON_REGEX_WORKER_FAILED,
    REASON_EVALUATION_TIMEOUT,
    REASON_BUSY,
    REASON_INPUT_TOO_LARGE,
)
REASONS: tuple[str, ...] = NORMAL_REASONS + FAILURE_REASONS

RESULT_MATCHED = "matched"
RESULT_NOT_MATCHED = "not_matched"
RESULT_NOT_EVALUATED = "not_evaluated"
RESULT_ERROR = "error"
RESULTS: tuple[str, ...] = (
    RESULT_MATCHED,
    RESULT_NOT_MATCHED,
    RESULT_NOT_EVALUATED,
    RESULT_ERROR,
)

TRACE_RULE_SHORT_CIRCUIT = "rule_short_circuit"
TRACE_PRIORITY_SHORT_CIRCUIT = "priority_short_circuit"
TRACE_BLACKLIST_MATCHED = "blacklist_matched"
TRACE_WHITELIST_MATCHED = "whitelist_matched"
TRACE_ERROR_SHORT_CIRCUIT = "error_short_circuit"
TRACE_FIELD_MISSING = "field_missing"
TRACE_REASONS: tuple[str, ...] = (
    RESULT_MATCHED,
    RESULT_NOT_MATCHED,
    TRACE_RULE_SHORT_CIRCUIT,
    TRACE_PRIORITY_SHORT_CIRCUIT,
    TRACE_BLACKLIST_MATCHED,
    TRACE_WHITELIST_MATCHED,
    TRACE_ERROR_SHORT_CIRCUIT,
    TRACE_FIELD_MISSING,
) + FAILURE_REASONS

ERROR_INVALID_DOCUMENT = "invalid_document"
ERROR_UNKNOWN_FIELD = "unknown_field"
ERROR_MISSING_FIELD = "missing_field"
ERROR_INVALID_SCHEMA_VERSION = "invalid_schema_version"
ERROR_INVALID_REVISION = "invalid_revision"
ERROR_INVALID_GROUP_LIST = "invalid_group_list"
ERROR_TOO_MANY_GROUPS = "too_many_groups"
ERROR_INVALID_GROUP = "invalid_group"
ERROR_INVALID_GROUP_ID = "invalid_group_id"
ERROR_DUPLICATE_GROUP_ID = "duplicate_group_id"
ERROR_INVALID_RULES = "invalid_rules"
ERROR_EMPTY_RULES = "empty_rules"
ERROR_TOO_MANY_RULES = "too_many_rules"
ERROR_INVALID_RULE = "invalid_rule"
ERROR_INVALID_RULE_ID = "invalid_rule_id"
ERROR_DUPLICATE_RULE_ID = "duplicate_rule_id"
ERROR_INVALID_FIELD_NAME = "invalid_field"
ERROR_INVALID_OP = "invalid_op"
ERROR_INVALID_VALUE = "invalid_value"
ERROR_VALUE_TOO_LONG = "value_too_long"
ERROR_INVALID_CASE_SENSITIVE = "invalid_case_sensitive"
ERROR_INVALID_NEGATE = "invalid_negate"
ERROR_INVALID_REGEX = "invalid_regex"
ERROR_INVALID_METADATA = "invalid_metadata"
ERROR_INVALID_ACCESSIBLE = "invalid_accessible"
ERROR_INVALID_QUALITY_SATISFIED = "invalid_quality_satisfied"
ERROR_INVALID_SNAPSHOT_REVISION = "invalid_snapshot_revision"

ERROR_CLOSED = "closed"

POLICY_ERROR_CODES: tuple[str, ...] = (
    ERROR_INVALID_DOCUMENT,
    ERROR_UNKNOWN_FIELD,
    ERROR_MISSING_FIELD,
    ERROR_INVALID_SCHEMA_VERSION,
    ERROR_INVALID_REVISION,
    ERROR_INVALID_GROUP_LIST,
    ERROR_TOO_MANY_GROUPS,
    ERROR_INVALID_GROUP,
    ERROR_INVALID_GROUP_ID,
    ERROR_DUPLICATE_GROUP_ID,
    ERROR_INVALID_RULES,
    ERROR_EMPTY_RULES,
    ERROR_TOO_MANY_RULES,
    ERROR_INVALID_RULE,
    ERROR_INVALID_RULE_ID,
    ERROR_DUPLICATE_RULE_ID,
    ERROR_INVALID_FIELD_NAME,
    ERROR_INVALID_OP,
    ERROR_INVALID_VALUE,
    ERROR_VALUE_TOO_LONG,
    ERROR_INVALID_CASE_SENSITIVE,
    ERROR_INVALID_NEGATE,
    ERROR_INVALID_REGEX,
    ERROR_INVALID_METADATA,
    ERROR_INVALID_ACCESSIBLE,
    ERROR_INVALID_QUALITY_SATISFIED,
    ERROR_INVALID_SNAPSHOT_REVISION,
)

EVALUATION_ERROR_CODES: tuple[str, ...] = (
    ERROR_INVALID_REGEX,
    ERROR_CLOSED,
) + FAILURE_REASONS


class RuleValidationError(ValueError):
    """A policy document, a rule or one caller-supplied fact was refused.

    ``code`` is a fixed machine word and ``field`` is the offending input path. The message repeats
    only those two words: a rule value may be a regular expression, an uploader nickname may be
    personal data, and neither belongs in a log line just because it was malformed.
    """

    def __init__(self, code: str, field: str) -> None:
        super().__init__(f"subscription rule refused: {code} at {field}")
        self.code = code
        self.field = field

    def __str__(self) -> str:  # pragma: no cover - mirrors __init__
        return f"subscription rule refused: {self.code} at {self.field}"


class RuleEvaluationError(RuntimeError):
    """A bounded resource failed: the regex process, a budget or a transport limit.

    This is raised by ``validate_policy`` and by the process layer. ``evaluate`` never raises it for
    a rule failure; it returns ``decision=rule_error`` instead, so that "we could not decide" can
    never be read as "the blacklist did not match".
    """

    def __init__(self, code: str, field: str) -> None:
        super().__init__(f"subscription rule evaluation failed: {code} at {field}")
        self.code = code
        self.field = field

    def __str__(self) -> str:  # pragma: no cover - mirrors __init__
        return f"subscription rule evaluation failed: {self.code} at {self.field}"


class _Frozen:
    """Refuse attribute assignment after construction for the immutable public value types."""

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")


@dataclass(frozen=True)
class Rule(_Frozen):
    """One typed rule. Text is verbatim; range values are immutable two-element tuples."""

    id: str
    field: str
    op: str
    value: str | int | float | bool | tuple[str | int | float, str | int | float]
    case_sensitive: bool
    negate: bool = False


@dataclass(frozen=True)
class Group(_Frozen):
    """One OR-group of AND-ed rules. An empty group cannot be constructed by the parser."""

    id: str
    rules: tuple[Rule, ...]


@dataclass(frozen=True)
class RulePolicy(_Frozen):
    """A validated policy document plus its deterministic digest."""

    revision: int
    whitelist: tuple[Group, ...]
    blacklist: tuple[Group, ...]
    policy_digest: str

    @property
    def group_count(self) -> int:
        return len(self.whitelist) + len(self.blacklist)

    @property
    def rule_count(self) -> int:
        return sum(len(group.rules) for group in self.whitelist + self.blacklist)

    @property
    def has_regex(self) -> bool:
        return any(
            rule.op == OP_REGEX for group in self.whitelist + self.blacklist for rule in group.rules
        )


@dataclass(frozen=True)
class RuleTrace(_Frozen):
    """One rule's place in an explanation: identity, shape and result word only."""

    rule_id: str
    field: str
    op: str
    result: str
    reason: str
    negate: bool = False


@dataclass(frozen=True)
class GroupTrace(_Frozen):
    """One group's place in an explanation, with its rules in input order.

    ``list_kind`` is structural (``blacklist``/``whitelist``): the trace is ordered blacklist first,
    then whitelist, and a consumer holding a decision alone must still be able to tell which list a
    group belongs to without re-reading the policy. It carries no source text.
    """

    group_id: str
    list_kind: str
    result: str
    reason: str
    rules: tuple[RuleTrace, ...]


@dataclass(frozen=True)
class RuleDecision(_Frozen):
    """The explained outcome of one试算. ``trace`` is ordered blacklist first, then whitelist."""

    item_key: str
    snapshot_revision: str
    policy_revision: int
    policy_digest: str
    decision: str
    reason: str
    automatic_enqueue_allowed: bool
    requires_rule_attention: bool
    trace: tuple[GroupTrace, ...]

    @property
    def group_count(self) -> int:
        return len(self.trace)

    @property
    def rule_count(self) -> int:
        return sum(len(group.rules) for group in self.trace)

    @property
    def evaluated_rule_count(self) -> int:
        return sum(
            1
            for group in self.trace
            for rule in group.rules
            if rule.result in (RESULT_MATCHED, RESULT_NOT_MATCHED)
        )


__all__ = [
    "BOOLEAN_FIELDS",
    "BOOLEAN_OPS",
    "ERROR_INVALID_NEGATE",
    "FIELD_PUBLISHED_AT",
    "FIELD_FAVORITED_AT",
    "FIELD_PART_COUNT",
    "FIELD_DURATION_SECONDS",
    "FIELD_IS_COLLABORATION",
    "NUMBER_FIELDS",
    "OP_NOT_EQUALS",
    "OP_GT",
    "OP_GTE",
    "OP_LT",
    "OP_LTE",
    "OP_BETWEEN",
    "OPTIONAL_RULE_FIELDS",
    "ORDERED_OPS",
    "TEXT_FIELDS",
    "TEXT_OPS",
    "TIME_FIELDS",
    "TRACE_FIELD_MISSING",
    "DECISIONS",
    "DECISION_DOWNLOAD",
    "DECISION_RULE_ERROR",
    "DECISION_SKIP",
    "DOCUMENT_FIELDS",
    "ERROR_CLOSED",
    "ERROR_DUPLICATE_GROUP_ID",
    "ERROR_DUPLICATE_RULE_ID",
    "ERROR_EMPTY_RULES",
    "ERROR_INVALID_ACCESSIBLE",
    "ERROR_INVALID_CASE_SENSITIVE",
    "ERROR_INVALID_DOCUMENT",
    "ERROR_INVALID_FIELD_NAME",
    "ERROR_INVALID_GROUP",
    "ERROR_INVALID_GROUP_ID",
    "ERROR_INVALID_GROUP_LIST",
    "ERROR_INVALID_METADATA",
    "ERROR_INVALID_OP",
    "ERROR_INVALID_QUALITY_SATISFIED",
    "ERROR_INVALID_REGEX",
    "ERROR_INVALID_REVISION",
    "ERROR_INVALID_RULE",
    "ERROR_INVALID_RULE_ID",
    "ERROR_INVALID_RULES",
    "ERROR_INVALID_SCHEMA_VERSION",
    "ERROR_INVALID_SNAPSHOT_REVISION",
    "ERROR_INVALID_VALUE",
    "ERROR_MISSING_FIELD",
    "ERROR_TOO_MANY_GROUPS",
    "ERROR_TOO_MANY_RULES",
    "ERROR_UNKNOWN_FIELD",
    "ERROR_VALUE_TOO_LONG",
    "EVALUATION_ERROR_CODES",
    "FAILURE_REASONS",
    "FIELDS",
    "FIELD_DESCRIPTION",
    "FIELD_TAGS",
    "FIELD_TITLE",
    "FIELD_UPLOADER_ID",
    "FIELD_UPLOADER_NAME",
    "GROUPS_MAX",
    "GROUP_FIELDS",
    "Group",
    "GroupTrace",
    "ID_PATTERN",
    "LIST_BLACKLIST",
    "LIST_WHITELIST",
    "NORMAL_REASONS",
    "OPS",
    "OP_CONTAINS",
    "OP_EQUALS",
    "OP_PREFIX",
    "OP_REGEX",
    "OP_SUFFIX",
    "POLICY_ERROR_CODES",
    "REASONS",
    "REASON_BLACKLIST_MATCH",
    "REASON_BUSY",
    "REASON_ELIGIBLE",
    "REASON_EVALUATION_TIMEOUT",
    "REASON_INACCESSIBLE",
    "REASON_INPUT_TOO_LARGE",
    "REASON_QUALITY_SATISFIED",
    "REASON_REGEX_TIMEOUT",
    "REASON_REGEX_WORKER_FAILED",
    "REASON_WHITELIST_NO_MATCH",
    "REGEX_OPS",
    "REGEX_VALUE_MAX",
    "RESULTS",
    "RESULT_ERROR",
    "RESULT_MATCHED",
    "RESULT_NOT_EVALUATED",
    "RESULT_NOT_MATCHED",
    "REVISION_MAX",
    "REVISION_MIN",
    "RULE_FIELDS",
    "RULES_PER_GROUP_MAX",
    "RULES_PER_GROUP_MIN",
    "Rule",
    "RuleDecision",
    "RuleEvaluationError",
    "RulePolicy",
    "RuleTrace",
    "RuleValidationError",
    "SCHEMA_VERSION",
    "SNAPSHOT_REVISION_PATTERN",
    "TRACE_BLACKLIST_MATCHED",
    "TRACE_ERROR_SHORT_CIRCUIT",
    "TRACE_PRIORITY_SHORT_CIRCUIT",
    "TRACE_REASONS",
    "TRACE_RULE_SHORT_CIRCUIT",
    "TRACE_WHITELIST_MATCHED",
    "VALUE_MAX",
]
