"""Budget-aware complete experience batches; never truncate persona or evidence."""

import json

from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.sources.dependencies import unique_refs
from tianshu.types import ContextContribution
from .reflection_output import (
    CONTENT_MAX_LENGTH, INPUT_TOKEN_BUDGET, MAX_NOTES, OUTPUT_TOKEN_BUDGET,
    TITLE_MAX_LENGTH, output_example, output_schema,
)


def material(episodes):
    return {
        "episodes": wire(episodes),
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
            "never rewrite authored identity or infer permissions. Prior reflections and diaries are not independent evidence."
        ),
        "output_schema": output_schema(item.episode_id for item in episodes),
        "example": output_example(episodes[0].episode_id),
        "no_change_example": {"notes": []},
    }


def reflectable(episode):
    result = episode.actual_result or {}
    return not result.get("internal") and result.get("derived_kind") not in {"reflection", "diary"}


async def select_batch(assembler, context, page, persona_text, budget, revision):
    # Consume a complete prefix of actual pending versions. The cursor is only
    # diagnostic; the version ledger also covers late commits below that cursor.
    # A bad first item is visible and blocks once instead of being silently lost.
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
    while count:
        selected = tuple(eligible[:count])
        refs = unique_refs(*(item.source_refs for item in selected))
        try:
            pack = await assembler.assemble(
                context, "reflect", "Review new experiences without forcing a change.", budget,
                persona_text=persona_text,
                max_input_tokens=INPUT_TOKEN_BUDGET + OUTPUT_TOKEN_BUDGET,
                reserved_output_tokens=OUTPUT_TOKEN_BUDGET,
                contributions=(ContextContribution("new_experiences", json.dumps(material(selected), ensure_ascii=False),
                    refs, context.scope_ids, 80, True, str(revision)),),
            )
        except AppError as error:
            if error.code != "context_mandatory_budget_exceeded":
                raise
            if count == 1:
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
