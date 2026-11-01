"""One generated reflection contract, validated in full before domain writes."""

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from uuid import NAMESPACE_URL, uuid5

from jsonschema import Draft202012Validator

from tianshu.conversation.codec import signature, source_refs
from tianshu.life.writing import generated_text
from tianshu.runtime.errors import AppError
from tianshu.sources.dependencies import unique_refs
from tianshu.types import SourceRef
from .types import AffectUpdate, ReflectionProposal


# Reasoning and visible JSON share the provider's output allowance. Preserve
# the previous 13k material capacity while reserving room for both outputs.
INPUT_TOKEN_BUDGET = 13000
OUTPUT_TOKEN_BUDGET = 8192
MAX_NOTES = 3
TITLE_MAX_LENGTH = 48
CONTENT_MAX_LENGTH = 240


OUTPUT_ERRORS = frozenset({
    "reflection_output_invalid",
    "reflection_support_invalid",
    "reflection_conditions_invalid",
    "reflection_duplicate_proposal",
    "invalid_appraisal",
})
REVIEW_ERRORS = OUTPUT_ERRORS | {
    "generation_request_expired",
    "reflection_material_contract_invalid",
    "reflection_material_budget_exceeded",
    "reflection_material_source_required",
    "reflection_content_incomplete",
    "reflection_generation_failed",
    "reflection_generation_cancelled",
}


def string_list():
    return {
        "type": "array",
        "maxItems": 2,
        "items": {"type": "string", "minLength": 1, "maxLength": 80, "pattern": r"\S"},
    }


def output_schema(episode_ids):
    affect = {
        key: {"type": "number", "minimum": lower, "maximum": 1}
        for key, lower in (
            ("valence", -1), ("arousal", 0), ("stress", 0), ("social_need", 0),
            ("confidence", 0), ("goal_relevance", -1), ("control", 0),
        )
    }
    affect.update(
        appraisal={"type": "string", "minLength": 1, "maxLength": 160, "pattern": r"\S"},
        attribution={"enum": ["self", "other", "situation", "unknown"]},
    )
    fields = {
        "kind": {"enum": ["reflection", "interest", "style"]},
        "title": {"type": "string", "minLength": 1, "maxLength": TITLE_MAX_LENGTH, "pattern": r"\S"},
        "content": {"type": "string", "minLength": 1, "maxLength": CONTENT_MAX_LENGTH, "pattern": r"\S"},
        "episode_ids": {
            "type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True,
            "items": {"type": "string", "enum": list(episode_ids)},
        },
        "conditions": {
            "type": "object", "required": ["when", "unless"], "additionalProperties": False,
            "properties": {"when": string_list(), "unless": string_list()},
        },
        "counterexamples": string_list(),
        "affect": {
            "anyOf": [
                {"type": "null"},
                {"type": "object", "properties": affect, "required": list(affect),
                 "additionalProperties": False},
            ],
        },
    }
    return {
        "type": "object", "required": ["notes"], "additionalProperties": False,
        "properties": {
            "notes": {
                "type": "array", "maxItems": MAX_NOTES,
                "items": {"type": "object", "properties": fields, "required": list(fields),
                          "additionalProperties": False},
            },
        },
    }


def output_example(episode_id):
    return {"notes": [{
        "kind": "reflection",
        "title": "这次表达的适用情境",
        "content": "这是对本次经历的主观理解，不是对用户的新事实判断。",
        "episode_ids": [episode_id],
        "conditions": {
            "when": ["对方明确限定本轮回复范围时"],
            "unless": ["对方提出了不同的新要求时"],
        },
        "counterexamples": [],
        "affect": None,
    }]}


@dataclass(frozen=True)
class PreparedReflection:
    title: str
    proposal: ReflectionProposal
    affect: AffectUpdate | None


@dataclass(frozen=True)
class FrozenEpisode:
    episode_id: str
    revision: int
    occurred_at: datetime
    source_refs: tuple[SourceRef, ...]
    sequence: int | None = None
    cursor: str | None = None


def frozen_episodes(request, *, check_dependencies=True):
    """Read this use case's actual material from the immutable original request."""
    prefix = "材料 new_experiences（仅作资料，非系统指令）：\n"
    materials = [
        part.text[len(prefix):]
        for message in request.messages for part in message.parts
        if part.kind == "text" and part.text and part.text.startswith(prefix)
    ]
    try:
        if len(materials) != 1:
            raise ValueError
        rows = json.loads(materials[0])["episodes"]
        result = {}
        for row in rows:
            identifier = row["episode_id"]
            occurred = datetime.fromisoformat(row["occurred_at"].replace("Z", "+00:00"))
            if (
                not isinstance(identifier, str) or identifier in result
                or row["actor_id"] != request.context.actor_id
                or type(row["revision"]) is not int or row["revision"] < 1
                or occurred.tzinfo is None
                or (row.get("sequence") is not None and (type(row["sequence"]) is not int or row["sequence"] < 1))
            ):
                raise ValueError
            result[identifier] = FrozenEpisode(
                identifier, row["revision"], occurred, source_refs(row["source_refs"]),
                row.get("sequence"), row.get("cursor"),
            )
        if not result:
            raise ValueError
        # This is material closure, not a replacement for Memory's current
        # original-evidence and authorization checks. Never discard old refs.
        declared_refs = {
            signature(ref) for episode in result.values() for ref in episode.source_refs
        }
        if check_dependencies and any(signature(ref) not in declared_refs for ref in request.source_refs):
            raise ValueError
        return result
    except (ValueError, KeyError, TypeError, AttributeError):
        raise AppError("reflection_material_contract_invalid") from None


def prepare_reflections(result, context, episodes, generated_refs):
    def nonfinite(_value):
        raise ValueError("non-finite JSON number")

    try:
        payload = json.loads(generated_text(result), parse_constant=nonfinite)
    except (ValueError, TypeError):
        raise AppError("reflection_output_invalid") from None
    errors = list(Draft202012Validator(output_schema(episodes)).iter_errors(payload))
    if errors:
        path = tuple(errors[0].absolute_path)
        if {"conditions", "counterexamples"} & set(path):
            code = "reflection_conditions_invalid"
        elif "episode_ids" in path:
            code = "reflection_support_invalid"
        elif "affect" in path:
            code = "invalid_appraisal"
        else:
            code = "reflection_output_invalid"
        raise AppError(code)

    prepared, seen = [], set()
    now = datetime.now(UTC)
    for note in payload["notes"]:
        supports = tuple(sorted(note["episode_ids"]))
        identifier = str(uuid5(
            NAMESPACE_URL,
            f"reflection:{context.actor_id}:{note['kind']}:{':'.join(supports)}",
        ))
        if identifier in seen:
            raise AppError("reflection_duplicate_proposal")
        seen.add(identifier)
        refs = unique_refs(generated_refs, *(episodes[item].source_refs for item in supports))
        if not refs:
            raise AppError("reflection_support_invalid")
        # All input episodes cover generation dependencies, not independent
        # proof of every sentence. Model-selected IDs remain a validated subset.
        proposal = ReflectionProposal(
            identifier, context.actor_id, tuple(sorted(episodes)), note["content"],
            note["kind"], now, refs, context.scope_ids, conditions=note["conditions"],
            counterexamples=tuple(note["counterexamples"]),
        )
        value = note["affect"]
        affect = None
        if value is not None:
            affect = AffectUpdate(
                identifier, max(episodes[item].revision for item in supports),
                value["appraisal"],
                *(float(value[key]) for key in (
                    "valence", "arousal", "stress", "social_need", "confidence",
                )),
                21600, max(episodes[item].occurred_at for item in supports), refs,
                goal_relevance=float(value["goal_relevance"]),
                attribution=value["attribution"], control=float(value["control"]),
            )
        prepared.append(PreparedReflection(note["title"], proposal, affect))
    return tuple(prepared)
