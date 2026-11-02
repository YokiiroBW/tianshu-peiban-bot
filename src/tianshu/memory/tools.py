"""The memory domain's actual use cases exposed through the one executable catalog."""

from dataclasses import replace
from uuid import uuid4

from tianshu.capabilities.catalog import Availability, CapabilityDefinition
from tianshu.sources.dependencies import unique_refs
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import Coverage, Receipt
from .claims import command_fingerprint
from .evidence import build_evidence_unit
from .query_plan import QUERY_PROPERTIES
from .source_trace import SourceTrace, TRACE_SCHEMA


class MemoryTool:
    def __init__(self, memory, retrieval, name, title, schema, callback, *, write=False):
        self.memory, self.retrieval, self.callback = memory, retrieval, callback
        self.write = write
        self.definition = CapabilityDefinition(
            name,
            title,
            title,
            schema,
            {"type": "object"},
            "write" if write else "read",
            permissions=frozenset({"memory.manage" if write else "memory.read"}),
            model_tool={"name": name.replace(".", "_")},
        )

    async def availability(self, context):
        return Availability(
            True,
            True,
            enabled=context.actor_id is not None,
            reason=None if context.actor_id else "actor_required",
        )

    async def execute(self, action, attempt):
        result = await self.callback(action)
        coverage = getattr(result, "coverage", None) or (
            result.get("coverage") if isinstance(result, dict) else None
        )
        public = wire(result)
        refs = list(result.get("source_refs", ())) if isinstance(result, dict) else []
        for item in (() if isinstance(result, dict) else getattr(result, "items", ())):
            refs.extend(item.source_refs)
        data = getattr(result, "data", None)
        if data is not None and hasattr(data, "source_refs"):
            refs.extend(build_evidence_unit(data).source_refs)
        # Conversation persists this top-level closure with the actual tool result.
        # Nested evidence references alone are not consumed by its receipt owner.
        public["source_refs"] = wire(unique_refs(refs))
        return Receipt(
            str(uuid4()),
            action.action_id,
            attempt.attempt_id,
            "succeeded",
            "confirmed" if self.write else "none",
            "not_requested",
            result=public,
            coverage=coverage,
        )

    async def reconcile(self, action, attempt):
        # The domain command log can prove a committed write; an absent log cannot prove execution.
        await self.memory.identity.check(action.context, action.source_refs)
        operation = {
            "memory.claim.correct": "memory.claim.correct",
            "memory.matter.save": "memory.matter.save",
        }.get(self.definition.id)
        result = None
        if operation:
            body = {**action.arguments, "client_request_id": action.intent_key}

            async def authorized(conn, original):
                await self.memory.identity.check_on(conn, action.context, action.source_refs)
                return original

            result = await self.memory.commands.replay(
                action.context,
                operation,
                action.intent_key,
                command_fingerprint(body, action.current_input_refs),
                on_replay=authorized,
            )
        return Receipt(
            str(uuid4()),
            action.action_id,
            attempt.attempt_id,
            "succeeded" if result else "unknown",
            "confirmed" if result else "unknown" if self.write else "none",
            "not_requested",
            result=wire(result) if result else None,
            error_code=None if result else "memory_command_outcome_unknown",
        )

    async def cancel(self, action, attempt):
        return await self.reconcile(action, attempt)


def register_tools(catalog, memory, retrieval):
    def bounded_plan(action, **fixed):
        plan = {key: value for key, value in action.arguments.items() if key not in {"limit", "cursor", "subject_id", "subject_aliases"}}
        if action.arguments.get("subject_id"):
            plan["subject_ids"] = [action.arguments["subject_id"]]
        plan.update(fixed)
        plan["budget"] = {"max_tokens": min(max(int(memory.settings.get("retrieval", {}).get("tool_max_tokens", 10000)), 1000), 16000)}
        return plan

    async def read(action, *, limit=12, **fixed):
        plan = bounded_plan(action, **fixed)
        request_budget = retrieval.foreground_budget(deadline_at=action.deadline_at)
        aliases = action.arguments.get("subject_aliases", ())
        if (not isinstance(aliases, (list, tuple)) or len(aliases) > 16
                or any(not isinstance(name, str) or not 1 <= len(name) <= 256 for name in aliases)):
            raise AppError("invalid_memory_subject_aliases")
        subjects, resolution_missing = (), []
        async with request_budget.timeout():
            if aliases:
                async with memory.database.transaction(retrieval.local_budget(request_budget), read_only=True) as conn:
                    subjects = await memory.identity.resolve_memory_subjects_on(conn, action.context,
                        aliases=tuple(aliases), include_group=False)
                if not subjects and not plan.get("subject_ids"):
                    return {"items": (), "next_cursor": None, "resolved_subjects": (), "source_refs": (),
                            "coverage": Coverage(False, missing=("subject_unresolved",), representation="memory_evidence")}
                for alias in aliases:
                    matching = [item for item in subjects if alias.casefold() in
                        {item["display_name"].casefold(), *(name.casefold() for name in item.get("aliases", ())) }]
                    if len(matching) > 1:
                        resolution_missing.append("subject_alias_ambiguous")
                    elif not matching:
                        resolution_missing.append("subject_unresolved")
                plan["subject_ids"] = tuple(dict.fromkeys((*plan.get("subject_ids", ()), *(item["subject_id"] for item in subjects))))
            page = await retrieval.query(action.context, plan, request_budget, limit=action.arguments.get("limit", limit),
                cursor=action.arguments.get("cursor"), source_refs=action.source_refs)
        coverage = replace(page.coverage, complete=False,
            missing=tuple(dict.fromkeys((*page.coverage.missing, *resolution_missing)))) if resolution_missing else page.coverage
        return {"items": page.items, "next_cursor": page.next_cursor, "coverage": coverage,
                "resolved_subjects": tuple({key: item[key] for key in ("subject_id", "display_name", "kind")} for item in subjects),
                "source_refs": unique_refs(*(item.source_refs for item in page.items))}

    async def search(action):
        return await read(action)

    async def claims(action):
        return await read(action, limit=50, coverage_mode="list", evidence_kinds=("claim", "hypothesis"))

    async def correct(action):
        return await memory.claims.correct(
            action.context,
            {**action.arguments, "client_request_id": action.intent_key},
            evidence_refs=action.current_input_refs,
        )

    async def matters(action):
        return await read(action, limit=50, coverage_mode="list", evidence_kinds=("matter",))

    async def save_matter(action):
        return await memory.claims.save_matter(
            action.context,
            {**action.arguments, "client_request_id": action.intent_key},
            evidence_refs=action.current_input_refs,
        )

    aliases_schema = {"type": "array", "maxItems": 16, "uniqueItems": True,
                      "items": {"type": "string", "minLength": 1, "maxLength": 256}}
    page = {
        "type": "object",
        "properties": {
            "subject_id": {"type": "string"},
            "subject_aliases": aliases_schema,
            **{key: value for key, value in QUERY_PROPERTIES.items()
               if key in {"subject_ids", "matter_refs", "cursor", "limit", "time_mode", "valid_at", "known_at"}},
        },
        "additionalProperties": False,
    }
    definitions = (
        (
            "memory.search",
            "查询完整记忆证据：current 当前；valid_at 当时状态；known_at 当时已知。全部清单用 list 并续 next_cursor，ranked 仅候选。",
            {
                "type": "object",
                "properties": {**QUERY_PROPERTIES, "subject_aliases": aliases_schema},
                "additionalProperties": False,
            },
            search,
            False,
        ),
        ("memory.source.trace", "确有缺口时补查已给 source_refs 的原文。每次前台请求最多一次；保留原 locator，position/max_characters 只能缩小范围。",
         TRACE_SCHEMA, SourceTrace(memory).read, False),
        ("memory.claims", "分页列出已知事实与条件，不把检索top-k当完整清单", page, claims, False),
        (
            "memory.claim.correct",
            "更正指定事实，保留原版本与真实用户来源",
            {
                "type": "object",
                "required": ["claim_id", "expected_revision", "value"],
                "properties": {
                    "claim_id": {"type": "string"},
                    "expected_revision": {"type": "integer", "minimum": 1},
                    "value": {},
                    "polarity": {"type": "boolean"},
                    "conditions": {"type": "object"},
                },
                "additionalProperties": False,
            },
            correct,
            True,
        ),
        ("memory.matters", "分页查看事项的当前计划、改期、取消和完成状态", page, matters, False),
        (
            "memory.matter.save",
            "按明确用户要求更新事项，未核实完成保持未知",
            {
                "type": "object",
                "required": ["matter_id", "expected_revision", "title", "state"],
                "properties": {
                    "matter_id": {"type": "string"},
                    "expected_revision": {"type": "integer", "minimum": 0},
                    "title": {"type": "string", "maxLength": 1000},
                    "state": {
                        "enum": [
                            "planned",
                            "active",
                            "rescheduled",
                            "cancelled",
                            "uncertain",
                            "completed",
                        ]
                    },
                    "due_at": {"type": ["string", "null"]},
                    "summary": {"type": "string", "maxLength": 4000},
                    "conditions": {"type": "object"},
                },
                "additionalProperties": False,
            },
            save_matter,
            True,
        ),
    )
    for name, title, schema, callback, write in definitions:
        catalog.register(MemoryTool(memory, retrieval, name, title, schema, callback, write=write))
