"""One bounded original-source follow-up per real foreground expression/activity."""

from tianshu.runtime.api import CommandResult
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.sources.dependencies import unique_refs
from tianshu.types import Coverage, RequestBudget, SourceRef


TRACE_SCHEMA = {
    "type": "object", "required": ["selections"], "additionalProperties": False,
    "properties": {"selections": {"type": "array", "minItems": 1, "maxItems": 8,
        "items": {"type": "object", "required": ["source_ref"], "additionalProperties": False,
            "properties": {"source_ref": {"type": "object", "required": ["source_id", "revision"],
                "additionalProperties": False, "properties": {"source_id": {"type": "string"},
                    "revision": {"type": "integer", "minimum": 1}, "locator": {"type": "object"}}},
                "position": {"type": "integer", "minimum": 0},
                "max_characters": {"type": "integer", "minimum": 1, "maximum": 12000}}}}},
}


class SourceTrace:
    def __init__(self, memory):
        self.memory = memory

    def selections(self, action):
        values = action.arguments.get("selections")
        if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 8:
            raise AppError("invalid_memory_source_selections")
        authorized = {body_hash(ref) for ref in action.source_refs}
        result, count = [], 0
        for item in values:
            if not isinstance(item, dict) or set(item) - {"source_ref", "position", "max_characters"}:
                raise AppError("invalid_memory_source_selection")
            raw = item.get("source_ref")
            if (not isinstance(raw, dict) or set(raw) - {"source_id", "revision", "locator"}
                    or not isinstance(raw.get("source_id"), str) or type(raw.get("revision")) is not int
                    or raw["revision"] < 1 or not isinstance(raw.get("locator", {}), dict)):
                raise AppError("invalid_source_reference")
            ref = SourceRef(raw["source_id"], raw["revision"], raw.get("locator", {}))
            # The model copies an already selected reference; position can narrow it.
            # A guessed ID, changed locator or enlarged original range grants nothing.
            if body_hash(ref) not in authorized:
                raise AppError("memory_source_reference_not_selected", status=403)
            position = item.get("position", ref.locator.get("start", 0))
            maximum = item.get("max_characters", 1000)
            lower, upper = ref.locator.get("start", 0), ref.locator.get("end")
            if (type(position) is not int or position < 0 or type(maximum) is not int
                    or not 1 <= maximum <= 12000 or type(lower) is not int
                    or upper is not None and type(upper) is not int):
                raise AppError("invalid_memory_source_range")
            if position < lower or upper is not None and position >= upper:
                raise AppError("source_position_outside_reference")
            if upper is not None:
                maximum = min(maximum, upper - position)
            count += maximum
            result.append((ref, {"position": position, "max_characters": maximum}))
        maximum = min(max(int(self.memory.settings.get("retrieval", {}).get("source_trace_characters", 4000)), 1), 12000)
        if count > maximum:
            raise AppError("memory_source_lookup_budget_exceeded", status=413,
                           details={"max_characters": maximum, "requested_characters": count})
        return tuple(result)

    async def read(self, action):
        if action.deadline_at is None:
            raise AppError("memory_source_lookup_deadline_required")
        budget = RequestBudget(action.deadline_at)
        budget.check()
        foreground = action.expression_id or action.activity_id
        if not foreground:
            raise AppError("memory_source_lookup_identity_required")
        selections = self.selections(action)
        refs = unique_refs(tuple(ref for ref, _ in selections))
        identity = {"principal": action.context.principal_id, "actor": action.context.actor_id,
                    "conversation": action.context.conversation_id, "foreground": foreground}
        key = "memory-source-trace:" + body_hash(identity)
        async with budget.timeout():
            await self.memory.identity.check(action.context, action.source_refs)

            async def reserve(conn):
                if not await self.memory.sources.validate_persistence(conn, action.context, refs):
                    raise AppError("source_unavailable", status=403)
                return CommandResult(key, action_id=action.action_id)

            async def replay(conn, original):
                if original.action_id != action.action_id:
                    raise AppError("memory_source_lookup_already_used", status=409)
                await self.memory.identity.check_on(conn, action.context, refs)
                return original

            await self.memory.commands.execute(action.context, "memory.source.trace", key,
                body_hash(identity), reserve, budget=budget, on_replay=replay)
            values = await self.memory.sources.read_many(action.context, selections)
            await self.memory.sources.check(action.context, refs)
        missing = tuple(dict.fromkeys(reason for item in values for reason in item["coverage"].missing))
        if any(not item["coverage"].complete for item in values):
            missing += ("original_range_partial",)
        return {"items": values, "source_refs": refs, "lookup_used": True,
                "coverage": Coverage(not missing, missing=missing, representation="original_source")}
