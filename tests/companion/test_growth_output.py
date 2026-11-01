"""Malformed terminal generations never partially write or restart themselves."""
from dataclasses import replace
from datetime import UTC, datetime
from importlib.resources import files
import json

import pytest

from tianshu.companion.growth import GrowthService
from tianshu.companion.reflection_output import output_example
from tianshu.companion.service import CompanionService
from tianshu.conversation.codec import document, signature
from tianshu.conversation.context.assembly import ContextAssembler
from tianshu.memory.types import Episode, EpisodePage
from tianshu.models.types import ModelPart, ModelReceipt, ModelResult
from tianshu.runtime.commands import CommandStore
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import Migration, migrate
from tianshu.types import ContextContribution, Coverage, OriginKind, SourceRef, TrustedContext


class Authority:
    async def check(self, *_args):
        pass

    async def check_many(self, _context, groups):
        return tuple(True for _ in groups)

    async def validate_persistence(self, *_args):
        return True


class Jobs:
    def __init__(self):
        self.items = []

    async def enqueue(self, _connection, spec):
        self.items.append(spec)


class Memory:
    def __init__(self):
        self.proposals = []
        self.episode = Episode("episode", "input", 1, "actor", OriginKind.HUMAN, datetime.now(UTC),
            "明确要求限定本轮回复范围", (SourceRef("original", 1),), ("private",), None, Coverage(True), 1, "e:1")
        self.episodes = (self.episode,)

    async def episodes_since(self, _context, _cursor, _limit):
        after = int((_cursor or "e:0").split(":")[1])
        pending = tuple(sorted((item for item in self.episodes if item.sequence > after), key=lambda item: item.sequence))
        items = pending[:_limit]
        end = items[-1].cursor if items else _cursor or "e:0"
        return EpisodePage(items, end if len(pending)>_limit else None, Coverage(len(pending)<=_limit), end)

    async def episodes_by_refs(self, _context, refs, *, connection=None):
        selected = tuple(item for identifier, version in refs for item in self.episodes
                         if item.episode_id == identifier and item.revision == version)
        return EpisodePage(selected, None, Coverage(len(selected) == len(refs)), selected[-1].cursor if selected else "e:0")

    async def relationship(self, *_args):
        return {"frozen": False}

    async def record_reflection(self, _context, proposal, *, connection=None, episode_revisions=None):
        self.proposals.append(proposal)

    async def projection(self, *_args):
        return {"reflections": []}

    async def style_view(self, *_args):
        return {"evidence": [], "counterexamples": [], "source_refs": [], "coverage": Coverage(True)}


class Models:
    status = "succeeded"
    native_response = None

    def __init__(self, *payloads):
        self.payloads, self.calls, self.reads, self.results = payloads, [], [], {}
        self.read_contexts = []

    async def generate(self, request):
        payload = self.payloads[len(self.calls)]
        self.calls.append(request)
        output = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        result = ModelResult((ModelPart("text", text=output),) if self.status == "succeeded" else (),
            ModelReceipt(request.attempt_id, request.intent_id, self.status, "submitted" if self.status == "succeeded" else "unknown", "route", 1,
                         "provider", "model", "openai.chat" if self.native_response and "choices" in self.native_response else "openai.responses", "quota"), self.native_response)
        self.results[request.attempt_id] = result
        return result

    async def result(self, attempt_id, *, context):
        self.reads.append(attempt_id)
        self.read_contexts.append(context)
        return self.results.get(attempt_id)


async def prepare(database, models):
    await migrate(database, (
        Migration("0030_companion", files("tianshu.companion").joinpath("schema.sql").read_text()),
        Migration("0032_conversation", files("tianshu.conversation").joinpath("schema.sql").read_text()),
        Migration("0034_companion_growth_batches", files("tianshu.companion").joinpath("growth_batches.sql").read_text()),
    ))
    context = TrustedContext("author", "actor", "person", "conversation", ("private",), ("private",),
        1, OriginKind.ACTOR, permissions=frozenset({"companion.manage"}))
    authority, memory, jobs = Authority(), Memory(), Jobs()
    companion = CompanionService(database, CommandStore(database), authority, memory,
        actor_registry=None, actor_changed=None, sources=authority, actor_activated=None)
    notifications = []

    async def changed(*args, **kwargs):
        notifications.append(args)

    growth = GrowthService(companion, jobs, models, ContextAssembler(authority, None, None), on_projection_changed=changed)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO companion.actors(actor_id,name,enabled) VALUES ('actor','角色',true)")
    await growth.changed(context, "new-experience", episode=memory.episode)
    return growth, memory, jobs, context, notifications


async def state(database):
    async with database.transaction(read_only=True) as connection:
        return await (await connection.execute("SELECT * FROM companion.organize_state WHERE actor_id='actor'")).fetchone()


def note(kind="reflection"):
    value = output_example("episode")["notes"][0]
    value["kind"] = kind
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["observed_list", "late_affect"])
async def test_whole_batch_rejects_before_memory_style_or_affect_writes(database, invalid):
    first, second = note("style"), note("reflection")
    first["affect"] = {"appraisal": "这次沟通很明确", "valence": 0.2, "arousal": 0.2, "stress": 0,
        "social_need": 0.1, "confidence": 0.6, "goal_relevance": 0.1, "attribution": "situation", "control": 0.5}
    if invalid == "observed_list":
        # Actual failing model shape: conditions=list[str], counterexamples=list[str].
        second["conditions"] = ["当多模态请求涉及内容判断，而材料只提供上传状态或元数据时"]
        expected = "reflection_conditions_invalid"
    else:
        second["affect"] = {**first["affect"], "control": 2}
        expected = "invalid_appraisal"
    models = Models({"notes": [first, second]})
    growth, memory, jobs, context, notifications = await prepare(database, models)
    scope = signature(sorted(context.scope_ids))
    rejected = await growth.organize("actor", scope, 1)
    assert rejected["state"] == "rejected"
    assert rejected["error_code"] == expected
    original = await state(database)
    assert original["last_error"] == expected
    assert original["cursor"] is None
    assert original["generation_revision"] == 0
    assert memory.proposals == notifications == []
    async with database.transaction(read_only=True) as connection:
        for table in ("companion.style_rules", "companion.affect_events"):
            assert (await (await connection.execute("SELECT count(*) AS n FROM "+table)).fetchone())["n"] == 0
    before = len(jobs.items)
    await growth.recover()
    await growth.organize("actor", scope, 1)
    assert len(jobs.items) == before
    # New ticks retain the refused generation instead of paying for or consuming it again.
    await growth.changed(context, "another-tick", episode=replace(memory.episode, episode_id="another", sequence=2, cursor="e:2"))
    await growth.organize("actor", scope, 2)
    before = len(jobs.items)
    await growth.recover()
    assert len(jobs.items) == before
    assert len(models.calls) == 1
    assert models.reads == []
    assert (await state(database))["last_error"] == expected


@pytest.mark.asyncio
async def test_explicit_review_creates_one_new_generation_using_the_canonical_contract(database):
    invalid = note()
    invalid["conditions"] = ["仅限该隔离验收消息及其回复上下文"]
    valid = note("style")
    models = Models({"notes": [invalid]}, {"notes": [valid]})
    growth, memory, _jobs, context, notifications = await prepare(database, models)
    scope = signature(sorted(context.scope_ids))
    await growth.organize("actor", scope, 1)
    original_attempt = models.calls[0].attempt_id
    assert (await growth.view(context))["review"]["can_request_review"] is True
    request = {"client_request_id": "explicit-review", "expected_revision": 1}
    result = await growth.request_review(context, request)
    assert result.revision == 2
    assert (await growth.request_review(context, request)).revision == 2
    await growth.organize("actor", scope, 2)
    assert len(models.calls) == 2
    assert models.calls[1].attempt_id != original_attempt
    assert "conditions is an object" in "".join(part.text or "" for message in models.calls[1].messages for part in message.parts)
    assert len(memory.proposals) == 1
    assert memory.proposals[0].conditions == {"when": valid["conditions"]["when"], "unless": valid["conditions"]["unless"]}
    assert memory.proposals[0].counterexamples == ()
    assert memory.proposals[0].interpretation is True
    assert len(notifications) == 1
    reviewed = await state(database)
    assert reviewed["generation_revision"] == 1
    assert reviewed["last_error"] is None
    assert reviewed["cursor"] == "e:1"
    assert (await growth.view(context))["review"]["can_request_review"] is False
    await growth.organize("actor", scope, 2)
    await growth.recover()
    assert len(models.calls) == 2
    async with database.transaction(read_only=True) as connection:
        rows = await (await connection.execute("SELECT attempt_id,state FROM conversation.generation_requests ORDER BY claimed_at")).fetchall()
    assert {row["attempt_id"] for row in rows} == {call.attempt_id for call in models.calls}
    assert all(row["state"] == "claimed" for row in rows)


@pytest.mark.asyncio
async def test_no_change_is_a_valid_complete_batch(database):
    models = Models({"notes": []})
    growth, memory, _jobs, context, notifications = await prepare(database, models)
    await growth.organize("actor", signature(sorted(context.scope_ids)), 1)
    assert memory.proposals == notifications == []
    assert (await state(database))["cursor"] == "e:1"
    assert (await state(database))["last_error"] is None


@pytest.mark.asyncio
async def test_reflection_keeps_the_full_frozen_input_batch_as_dependencies(database):
    models = Models({"notes": [note()]})
    growth, memory, _jobs, context, _notifications = await prepare(database, models)
    memory.episodes += (replace(memory.episode, episode_id="second", event_id="second-input",
        source_refs=(SourceRef("second-original", 1),), sequence=2, cursor="e:2"),)
    await growth.changed(context, "second-input", episode=memory.episodes[-1])
    await growth.organize("actor", signature(sorted(context.scope_ids)), 2)
    proposal = memory.proposals[0]
    assert proposal.episode_ids == ("episode", "second")
    assert {ref.source_id for ref in proposal.source_refs} == {"original", "second-original"}
    assert proposal.interpretation is True
    assert growth.assembler.retrieval is None


@pytest.mark.asyncio
async def test_completed_legacy_request_with_unrelated_memory_is_not_applied(database):
    models = Models({"notes": []})
    growth, memory, _jobs, context, notifications = await prepare(database, models)

    class LegacyAssembler(ContextAssembler):
        async def assemble(self, *args, contributions, **kwargs):
            unrelated = ContextContribution("old_memory", "unrelated old memory",
                (SourceRef("unrelated", 1),), context.scope_ids)
            return await super().assemble(*args, contributions=(*contributions, unrelated), **kwargs)

    growth.assembler = LegacyAssembler(growth.companion.identity, memory, None)
    scope = signature(sorted(context.scope_ids))
    rejected = await growth.organize("actor", scope, 1)
    assert rejected["error_code"] == "reflection_material_contract_invalid"
    assert memory.proposals == notifications == []
    assert (await state(database))["cursor"] is None
    await growth.recover()
    await growth.organize("actor", scope, 1)
    assert len(models.calls) == 1
    assert models.reads == []


@pytest.mark.asyncio
async def test_unknown_attempt_cannot_be_replaced_by_explicit_review(database):
    models = Models({"notes": []})
    models.status = "unknown"
    growth, _memory, _jobs, context, _notifications = await prepare(database, models)
    scope = signature(sorted(context.scope_ids))
    for _ in range(2):
        unknown = await growth.organize("actor", scope, 1)
        assert unknown["state"] == "waiting"
        assert unknown["error_code"] == "reflection_generation_unknown"
    with pytest.raises(AppError) as unresolved:
        await growth.request_review(context, {"client_request_id": "no-new-unknown-attempt", "expected_revision": 1})
    assert unresolved.value.code == "reflection_original_attempt_unresolved"
    assert len(models.calls) == 1
    assert models.reads == [models.calls[0].attempt_id]


@pytest.mark.asyncio
async def test_reasoning_and_complete_small_batch_share_the_reserved_output_budget(database):
    models = Models({"notes": [note("reflection"), note("style"), note("interest")]})
    growth, memory, _jobs, context, _notifications = await prepare(database, models)

    class ObservedAssembler(ContextAssembler):
        async def assemble(self, *args, **kwargs):
            self.pack = await super().assemble(*args, **kwargs)
            return self.pack

    assembler = ObservedAssembler(growth.companion.identity, memory, None)
    growth.assembler = assembler
    await growth.organize("actor", signature(sorted(context.scope_ids)), 1)
    request = models.calls[0]
    usage = assembler.pack.token_usage
    assert request.max_output_tokens == usage.reserved_output_tokens
    # The observed 2565 reasoning tokens must no longer leave only 435 for JSON.
    assert request.max_output_tokens - 2565 >= 3000
    assert usage.limit_tokens - usage.reserved_output_tokens >= 10843
    assert usage.estimated_tokens + request.max_output_tokens <= usage.limit_tokens
    assert len(memory.proposals) == 3


@pytest.mark.asyncio
async def test_truncated_success_is_rejected_without_partial_writes_or_automatic_new_attempt(database):
    # The provider finished by length while starting a second note's support IDs.
    first_note = json.dumps({"notes": [note("style")]}, ensure_ascii=False)[:-2]
    models = Models(first_note + ',{"kind":"reflection","episode_ids":["epis')
    models.native_response = {
        "choices": [{"finish_reason": "length"}],
        "usage": {"completion_tokens": 3000, "completion_tokens_details": {"reasoning_tokens": 2565}},
    }
    growth, memory, jobs, context, notifications = await prepare(database, models)
    scope = signature(sorted(context.scope_ids))
    rejected = await growth.organize("actor", scope, 1)
    assert rejected["error_code"] == "reflection_content_incomplete"
    assert memory.proposals == notifications == []
    async with database.transaction(read_only=True) as connection:
        assert (await (await connection.execute("SELECT count(*) AS n FROM companion.style_rules")).fetchone())["n"] == 0
    original = await state(database)
    assert original["cursor"] is None
    assert original["generation_revision"] == 0
    before = len(jobs.items)
    await growth.recover()
    await growth.organize("actor", scope, 1)
    assert len(jobs.items) == before
    assert len(models.calls) == 1
    assert models.reads == []
    assert models.results[rejected["attempt_id"]].receipt.status == "succeeded"


@pytest.mark.asyncio
async def test_new_event_keeps_unresolved_original_batch_and_attempt(database):
    models = Models({"notes": []})
    models.status = "unknown"
    growth, memory, _jobs, context, _notifications = await prepare(database, models)
    scope = signature(sorted(context.scope_ids))
    await growth.organize("actor", scope, 1)
    original = models.calls[0]
    active = (await state(database))["active_batch"]
    memory.episodes += (replace(memory.episode, episode_id="later", sequence=2, cursor="e:2"),)
    await growth.changed(context, "later-event", episode=memory.episodes[-1])
    await growth.organize("actor", scope, 2)
    assert len(models.calls) == 1
    assert models.reads == [original.attempt_id]
    current = await state(database)
    assert current["active_batch"] == active
    assert current["cursor"] is None
    assert current["watermark"] == "later-event"


@pytest.mark.asyncio
async def test_late_commit_with_lower_sequence_is_consumed_as_actual_version(database):
    models = Models({"notes": []}, {"notes": []})
    growth, memory, _jobs, context, _notifications = await prepare(database, models)
    scope = signature(sorted(context.scope_ids))
    memory.episodes = (replace(memory.episode, sequence=10, cursor="e:10"),)
    await growth.organize("actor", scope, 1)
    late = replace(memory.episode, episode_id="late-commit", event_id="late", sequence=5, cursor="e:5")
    memory.episodes += (late,)
    await growth.changed(context, "late-commit", episode=late)
    current = await state(database)
    await growth.organize("actor", scope, current["revision"])
    assert len(models.calls) == 2
    assert 'late-commit' in ''.join(part.text or '' for message in models.calls[1].messages for part in message.parts)
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT revision,reviewed_revision FROM companion.growth_inputs WHERE episode_id='late-commit'")).fetchone()
    assert row["reviewed_revision"] == row["revision"] == 1


@pytest.mark.asyncio
async def test_source_less_head_is_a_coverage_gap_tail_continues_and_repaired_version_reenters(database):
    models = Models({"notes": []}, {"notes": []})
    growth, memory, _jobs, context, _notifications = await prepare(database, models)
    missing = replace(memory.episode, source_refs=())
    tail = replace(memory.episode, episode_id="source-backed-tail", event_id="tail", sequence=2, cursor="e:2",
        source_refs=(SourceRef("tail-original", 1),))
    memory.episode, memory.episodes = missing, (missing, tail)
    await growth.changed(context, "tail", episode=tail)
    async with database.transaction() as connection:
        await connection.execute(
            "UPDATE companion.organize_state SET last_error='reflection_material_source_required',blocked_material=%s",
            (document({"episode_id": missing.episode_id, "revision": missing.revision}),))
    view = (await growth.view(context))["review"]
    assert view["can_request_review"] and view["recovery"] == "continue_available_material"
    resumed = await growth.request_review(context,
        {"client_request_id": "continue-source-backed-tail", "expected_revision": view["revision"]})
    assert resumed.data["recovery"] == "continue_available_material"
    assert (await state(database))["generation_revision"] == 0
    await growth.organize("actor", signature(sorted(context.scope_ids)), resumed.revision)
    async with database.transaction(read_only=True) as connection:
        inputs = await (await connection.execute(
            "SELECT episode_id,revision,reviewed_revision,state,missing FROM companion.growth_inputs ORDER BY sequence")).fetchall()
    assert inputs[0] == {"episode_id": "episode", "revision": 1, "reviewed_revision": 1,
                         "state": "unavailable", "missing": ["original_source_missing"]}
    assert inputs[1]["state"] == "reviewed" and inputs[1]["reviewed_revision"] == 1
    assert len(models.calls) == 1 and models.calls[0].source_refs == tail.source_refs
    assert memory.episodes[0] == missing and missing.occurred_at == memory.episode.occurred_at
    view = (await growth.view(context))["review"]
    assert view["unavailable_inputs"] == 1 and "original_source_missing" in view["legacy_missing"]
    # A legitimate native repair supplies a new Episode version, not a fabricated
    # dependency for the old version. Its original occurrence time is retained.
    repaired = replace(missing, revision=2, source_refs=(SourceRef("repaired-original", 1),))
    memory.episode, memory.episodes = repaired, (repaired, tail)
    await growth.changed(context, "native-repaired-version", episode=repaired)
    current = await state(database)
    await growth.organize("actor", signature(sorted(context.scope_ids)), current["revision"])
    assert len(models.calls) == 2 and repaired.occurred_at == missing.occurred_at
    async with database.transaction(read_only=True) as connection:
        recovered = await (await connection.execute(
            "SELECT revision,reviewed_revision,state,missing FROM companion.growth_inputs WHERE episode_id='episode'")).fetchone()
    assert recovered == {"revision": 2, "reviewed_revision": 2, "state": "reviewed", "missing": []}
    view = (await growth.view(context))["review"]
    assert view["unavailable_inputs"] == 0 and "original_source_missing" not in view["legacy_missing"]


@pytest.mark.asyncio
async def test_source_backed_budget_failure_is_not_converted_to_unavailable_or_skipped(database):
    models = Models({"notes": []})
    growth, memory, _jobs, context, _notifications = await prepare(database, models)
    huge = replace(memory.episode, summary="完整但过大材料" * 24000)
    tail = replace(memory.episode, episode_id="tail", event_id="tail", sequence=2, cursor="e:2")
    memory.episodes = (huge, tail)
    await growth.changed(context, "tail", episode=tail)
    current = await state(database)
    result = await growth.organize("actor", signature(sorted(context.scope_ids)), current["revision"])
    assert result["error_code"] == "reflection_material_budget_exceeded"
    assert models.calls == []
    async with database.transaction(read_only=True) as connection:
        inputs = await (await connection.execute("SELECT state,reviewed_revision,missing FROM companion.growth_inputs")).fetchall()
    assert len(inputs) == 2 and all(row == {"state": "pending", "reviewed_revision": 0, "missing": []} for row in inputs)
    assert (await state(database))["cursor"] is None
