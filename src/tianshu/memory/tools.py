"""The memory domain's actual use cases exposed through the one executable catalog."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from tianshu.capabilities.catalog import Availability, CapabilityDefinition
from tianshu.runtime.serialization import wire
from tianshu.types import Coverage, Receipt, RequestBudget
from .claims import command_fingerprint


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
        return Receipt(
            str(uuid4()),
            action.action_id,
            attempt.attempt_id,
            "succeeded",
            "confirmed" if self.write else "none",
            "not_requested",
            result=wire(result),
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
    async def search(action):
        budget = RequestBudget(action.deadline_at or datetime.now(UTC) + timedelta(seconds=20))
        material, missing = await retrieval.search(
            action.context,
            action.arguments["query"],
            budget,
            limit=action.arguments.get("limit", 12),
            source_refs=action.source_refs,
        )
        return {"items": material, "coverage": Coverage(not missing, missing=missing)}

    async def claims(action):
        return await memory.claims.list(
            action.context,
            subject_id=action.arguments.get("subject_id"),
            cursor=action.arguments.get("cursor"),
            limit=action.arguments.get("limit", 50),
        )

    async def correct(action):
        return await memory.claims.correct(
            action.context,
            {**action.arguments, "client_request_id": action.intent_key},
            evidence_refs=action.current_input_refs,
        )

    async def matters(action):
        return await memory.claims.matters(
            action.context,
            subject_id=action.arguments.get("subject_id"),
            cursor=action.arguments.get("cursor"),
            limit=action.arguments.get("limit", 50),
        )

    async def save_matter(action):
        return await memory.claims.save_matter(
            action.context,
            {**action.arguments, "client_request_id": action.intent_key},
            evidence_refs=action.current_input_refs,
        )

    page = {
        "type": "object",
        "properties": {
            "subject_id": {"type": "string"},
            "cursor": {"type": ["string", "null"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "additionalProperties": False,
    }
    definitions = (
        (
            "memory.search",
            "按授权来源检索记忆",
            {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "maxLength": 4000},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 32},
                },
                "additionalProperties": False,
            },
            search,
            False,
        ),
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
