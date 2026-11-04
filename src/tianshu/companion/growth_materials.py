"""Budget-aware complete experience batches; never truncate persona or evidence."""

import json

from tianshu.models.budgets import BACKGROUND_INPUT_TOKENS
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.sources.dependencies import unique_refs
from tianshu.types import ContextContribution
from .growth_observations import episode_material
from .growth_output_repair import repair_reserve_tokens
from .reflection_output import (
    CONTENT_MAX_LENGTH, MAX_NOTES, OUTPUT_TOKEN_BUDGET,
    TITLE_MAX_LENGTH, output_example, output_schema, short_material,
)


def material(episodes, rules=(), examples=(), *, rules_complete=True):
    groups = {}
    for episode in episodes:
        actual = episode.actual_result or {}
        if actual.get('imagined') is True and actual.get('learning_value') == 'low' and actual.get('experience_eligible') is not True:
            title = str(actual.get('title') or '')
            groups.setdefault(title, []).append(episode.episode_id)
    return short_material({
        "episodes": [episode_material(item) for item in episodes],
        "low_imagined_groups": [{'title': title, 'episode_ids': ids, 'count': len(ids)}
                                for title, ids in groups.items()],
        "rules": [{key: row[key] for key in ('id', 'semantic_revision', 'kind', 'title', 'content',
                    'conditions', 'counterexamples', 'state', 'preference_kind', 'conversation_id',
                    'purposes', 'valid_until', 'source_refs')} for row in rules],
        "candidate_examples": examples,
        "rules_complete": rules_complete,
        "instruction": (
            "Reflect on the supplied new experiences. Return only JSON matching output_schema; every note field is required. "
            f"Select at most {MAX_NOTES} distinct useful notes for the whole batch, not one per episode. "
            f"Keep titles within {TITLE_MAX_LENGTH} and content within {CONTENT_MAX_LENGTH} characters. "
            "Do not repeat episodes, instructions or examples. conditions is an object with when and unless arrays, never a list. "
            "counterexamples is a separate array of actual counterexamples, empty when none are known. "
            "affect is null or the complete bounded numeric object. Do not invent affect. "
            "Use at most one note per kind and episode_ids set. Examples demonstrate format, not evidence. "
            "No change is valid. Separate subjective interpretation from observed fact. Cite only supplied episode IDs. "
            "Actor imagined/fictional_world experiences prove that the character generated or completed an imagined activity; "
            "they never prove physical activity or human facts. An action succeeds only when its actual receipt confirms it. "
            "Interests belong to the actor, not copied user preferences. Habits remain conditional and revisable; "
            "never rewrite authored identity or infer permissions. Prior reflections and diaries are not independent evidence. "
            "A semantic_action_observation is a bounded view of an original receipt, not its complete document contents. "
            "Only included whole units were supplied for reflection; omitted units and reflection_coverage.missing remain unassessed. "
            "Discovery candidates are metadata only and never prove a source body was read. "
            "Reflect only on the actor's own observed operation or supplied content; do not copy human facts or preferences from receipts."
            " For a style or interest, choose create, support, revise, retract, unresolved or example. "
            "Only refer to a supplied rule alias; use unresolved if identity is uncertain. "
            "A revoked rule cannot be re-created as a new rule. Never infer explicit preference from a quote or actor output. "
            "Use validity turn or today for temporary requests, and persistent only when the evidence supports it. "
            "Existing rules are context for resolving identity, not new independent evidence. "
            "Choose example only for a supplied actor expression episode whose actual delivery is delivered, "
            "and only if its exact original reply usefully illustrates the referenced rule. "
            "Examples are not evidence of user approval and never increase support."
        ),
        "output_schema": output_schema(item.episode_id for item in episodes),
        "example": output_example(episodes[0].episode_id),
        "no_change_example": {"notes": []},
    })


def reflectable(episode):
    result = episode.actual_result or {}
    low_rest = (result.get('derived_kind') == 'routine_rest' and result.get('learning_value') == 'low'
                and result.get('experience_eligible') is False)
    return not result.get("internal") and result.get("derived_kind") not in {"reflection", "diary"} and not low_rest


async def select_batch(assembler, context, page, persona_text, budget, revision, *, rules=(), examples=()):
    # Consume a complete prefix of actual pending versions. The cursor is only
    # diagnostic; the version ledger also covers late commits below that cursor.
    # Growth records confirmed source-less versions as explicit unavailable
    # coverage before selection. Keep this guard for any unclassified caller;
    # budget failures remain blocking and must never be treated as unavailable.
    eligible = []
    candidates = tuple(item for item in page.items if reflectable(item))
    for episode in candidates:
        if not episode.source_refs:
            if not eligible:
                raise AppError("reflection_material_source_required", details={
                    "episode_id": episode.episode_id, "revision": episode.revision,
                    "cursor": episode.cursor,
                })
            break
        eligible.append(episode)
    count = len(eligible)
    selected_rules = tuple(rules)
    include_examples = True
    while count:
        selected = tuple(eligible[:count])
        selected_ids = {item.episode_id for item in selected}
        selected_examples = tuple(item for item in examples if item['episode_id'] in selected_ids) if include_examples else ()
        refs = unique_refs(*(item.source_refs for item in selected),
                           *(row['source_refs'] for row in selected_rules),
                           *(row['source_refs'] for row in selected_examples))
        try:
            pack = await assembler.assemble(
                context, "reflect", "Review new experiences without forcing a change.", budget,
                persona_text=persona_text,
                max_input_tokens=BACKGROUND_INPUT_TOKENS + OUTPUT_TOKEN_BUDGET,
                reserved_output_tokens=OUTPUT_TOKEN_BUDGET + repair_reserve_tokens(assembler.counter),
                contributions=(ContextContribution("new_experiences", json.dumps(wire(material(selected, selected_rules, selected_examples,
                    rules_complete=len(selected_rules) == len(rules))), ensure_ascii=False),
                    refs, context.scope_ids, 80, True, str(revision)),),
            )
        except AppError as error:
            if error.code != "context_mandatory_budget_exceeded":
                raise
            if count == 1:
                if selected_examples:
                    include_examples = False
                    continue
                if selected_rules:
                    selected_rules = selected_rules[:len(selected_rules)//2]
                    continue
                raise AppError("reflection_material_budget_exceeded", status=413, details={
                    **(error.details or {}), "episode_id": selected[0].episode_id,
                    "revision": selected[0].revision, "cursor": selected[0].cursor,
                }) from None
            count = max(1, count // 2)
            continue
        more = len(selected) < len(candidates) or bool(page.next_cursor)
        end_cursor = selected[-1].cursor if len(selected) < len(candidates) else page.watermark
        return selected, pack, end_cursor, more
    raise AppError("reflection_material_contract_invalid")
