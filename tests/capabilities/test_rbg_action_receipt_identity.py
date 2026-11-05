"""The Action ledger, transactional result queue and consumer agree on receipt identity."""

import json
from dataclasses import replace
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from tianshu.capabilities.actions import Actions, encoded
from tianshu.capabilities.catalog import Catalog
from tianshu.capabilities.contracts import canonical, digest
from tianshu.conversation.results import ActionResultEvents, ResultConsumer
from tianshu.runtime.jobs import JobQueue
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.serialization import wire
from tianshu.types import Action, Attempt, OriginKind, Receipt, SourceRef, TrustedContext


@pytest_asyncio.fixture
async def receipts(database):
    await migrate(database, (
        Migration("capabilities-0001", files("tianshu.capabilities").joinpath("schema.sql").read_text()),
        Migration("0032_conversation", files("tianshu.conversation").joinpath("schema.sql").read_text()),
        Migration("0031_life", files("tianshu.life").joinpath("schema.sql").read_text()),
        Migration("rbg_results", files("tianshu.conversation").joinpath("rbg_result_consumption.sql").read_text()),
    ))
    jobs = JobQueue(database.config.dsn, database=database)
    jobs.register_route("conversation.result")
    identity = SimpleNamespace(check=AsyncMock())
    actions = Actions(database, jobs, Catalog(), identity, ActionResultEvents(jobs))
    actions.sources = SimpleNamespace(validate_persistence=AsyncMock(return_value=True))
    context = TrustedContext("owner", "actor", None, "scene", ("scope",), ("scope",), 1,
                             OriginKind.ACTOR, frozenset({"*"}))
    action = Action("action", "intent", "channels.read", "actor", context, {}, OriginKind.ACTOR)
    attempt = Attempt("attempt", action.action_id, dispatch="submitted", provider_handle="provider-handle")
    initial = Receipt("initial", action.action_id, attempt.attempt_id, "pending", "none", "not_requested",
                      result={"source_refs": []})
    async with database.transaction() as conn:
        generation = await jobs.current_generation_on(conn)
        await conn.execute(
            "INSERT INTO capabilities.actions(id,intent,semantics,principal_id,kind,created_at,document,"
            "action,attempt,receipt,result_revision,result_observed_at) "
            "VALUES(%s,%s,%s,%s,%s,0,%s,%s::jsonb,%s::jsonb,%s::jsonb,1,now())",
            (action.action_id, action.intent_key, "fixture", context.principal_id, action.capability_id,
             canonical({"data_generation": generation}), encoded(action), encoded(attempt), encoded(initial)))
        await conn.execute(
            "INSERT INTO capabilities.attempts(id,action_id,attempt,receipt) VALUES(%s,%s,%s::jsonb,%s::jsonb)",
            (attempt.attempt_id, action.action_id, encoded(attempt), encoded(initial)))
        await conn.execute(
            "INSERT INTO life.activities(id,actor_id,title,kind,state,action_id,scope_ids) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s)",
            ("activity", context.actor_id, "Read input", "reading", "running", action.action_id, list(context.scope_ids)))

    async def activity_result(_context, result, revision, _observed_at, _capability_id, *, on_complete):
        # Match the atomic Life branch contract: record this result and complete
        # its independent consumer claim in one transaction.
        async with database.transaction() as conn:
            await on_complete.begin(conn)
            await conn.execute("UPDATE life.activities SET last_receipt_id=%s,last_result_revision=%s WHERE id=%s",
                               (result.receipt_id, revision, "activity"))
            await on_complete(conn, proof={"activity_id": "activity", "receipt_id": result.receipt_id,
                                           "result_revision": revision})

    life = SimpleNamespace(_activity_result=AsyncMock(side_effect=activity_result),
                           _commitment_result=AsyncMock(), detached_result=AsyncMock())
    renderer = SimpleNamespace(on_action_result=AsyncMock(return_value=False))
    conversation = SimpleNamespace(jobs=jobs, sources=actions.sources, on_result=AsyncMock())
    consumer = ResultConsumer(database, identity, actions, SimpleNamespace(), SimpleNamespace(),
                              conversation, life, renderer)
    yield SimpleNamespace(database=database, actions=actions, action=action, attempt=attempt,
                          initial=initial, consumer=consumer, life=life)
    await actions.close()


async def snapshot(state):
    async with state.database.transaction(read_only=True) as conn:
        return await (await conn.execute(
            "SELECT document,attempt,receipt,result_revision,result_observed_at FROM capabilities.actions WHERE id=%s",
            (state.action.action_id,))).fetchone()


async def events(state):
    async with state.database.transaction(read_only=True) as conn:
        rows = await (await conn.execute(
            "SELECT j.args->'event' AS event FROM runtime.job_keys k "
            "JOIN procrastinate_jobs j ON j.id=k.job_id WHERE k.task='conversation.result' ORDER BY j.id" )).fetchall()
    return [row["event"] for row in rows]


@pytest.mark.asyncio
async def test_changed_same_id_publishes_distinct_event_and_consumes_once_per_revision(receipts):
    state = receipts
    first = replace(state.initial, receipt_id="provider-result", execution="unknown", effect="unknown",
                    result={"phase": "waiting", "source_refs": []})
    assert await state.actions.save(first, state.attempt) == first
    first_event = (await events(state))[0]
    assert first_event["event_id"] == first.receipt_id
    await state.consumer.consume(first_event)
    await state.consumer.consume(first_event)

    requested = replace(first, execution="succeeded", effect="confirmed",
                        result={"outcome": "resolved", "source_refs": []})
    second = await state.actions.save(requested, state.attempt)
    assert second.receipt_id != first.receipt_id
    assert second.action_id == first.action_id and second.attempt_id == first.attempt_id
    event_rows = await events(state)
    assert [event["event_id"] for event in event_rows] == [first.receipt_id, second.receipt_id]
    assert event_rows[1]["receipt"]["receipt_id"] == second.receipt_id
    await state.consumer.consume(event_rows[1])
    await state.consumer.consume(event_rows[1])
    async with state.database.transaction(read_only=True) as conn:
        consumed = await (await conn.execute(
            "SELECT receipt_id FROM conversation.result_consumption ORDER BY receipt_id")).fetchall()
        progress = await (await conn.execute(
            "SELECT receipt_id,consumer,disposition,attempts,proof FROM conversation.result_consumer_progress "
            "ORDER BY receipt_id,consumer")).fetchall()
        activity = await (await conn.execute(
            "SELECT last_receipt_id,last_result_revision FROM life.activities WHERE id='activity'")).fetchone()
    assert {row["receipt_id"] for row in consumed} == {first.receipt_id, second.receipt_id}
    assert state.life._activity_result.await_count == 2
    assert [call.args[1].receipt_id for call in state.life._activity_result.await_args_list] == [
        first.receipt_id, second.receipt_id]
    assert activity == {"last_receipt_id": second.receipt_id, "last_result_revision": event_rows[1]["result_revision"]}
    assert len(progress) == 12 and all(row["attempts"] == 1 for row in progress)
    life_progress = {row["receipt_id"]: row for row in progress if row["consumer"] == "life_activity"}
    assert set(life_progress) == {first.receipt_id, second.receipt_id}
    assert all(row["disposition"] == "done" and row["proof"]["receipt_id"] == receipt_id
               for receipt_id, row in life_progress.items())

    before = await snapshot(state)
    assert before["attempt"]["attempt_id"] == state.attempt.attempt_id
    assert before["attempt"]["provider_handle"] == "provider-handle"
    lineage = json.loads(before["document"])["receipt_lineage"]
    assert lineage[first.receipt_id]["successor_receipt_id"] == second.receipt_id
    assert lineage[first.receipt_id]["receipt_digest"] == digest(wire(first))
    assert lineage[first.receipt_id]["result_revision"] == event_rows[0]["result_revision"]

    # A retried old request and a fresh but equivalent receipt are both no-ops.
    assert await state.actions.save(requested, state.attempt) == second
    assert await state.actions.save(replace(requested, receipt_id="another-id"), state.attempt) == second
    after = await snapshot(state)
    assert after["result_revision"] == before["result_revision"]
    assert after["result_observed_at"] == before["result_observed_at"]
    assert len(await events(state)) == 2

    # A later producer reusing an older historical ID cannot collide either.
    third_request = replace(requested, result={"outcome": "further detail", "source_refs": []})
    third = await state.actions.save(third_request, state.attempt)
    assert third.receipt_id not in {first.receipt_id, second.receipt_id}
    assert (await events(state))[-1]["event_id"] == third.receipt_id
    assert await state.actions.save(third_request, state.attempt) == third
    assert len(await events(state)) == 3


@pytest.mark.asyncio
async def test_source_redaction_changes_identity_once_and_retains_no_source_body_in_lineage(receipts):
    state = receipts
    source = SourceRef("source", 1, {"start": 0, "end": 3})
    original = replace(state.initial, receipt_id="sensitive", execution="succeeded", effect="confirmed",
                       result={"text": "synthetic-private-body", "source_refs": [wire(source)]})
    saved = await state.actions.save(original, state.attempt)
    assert saved.receipt_id == original.receipt_id
    state.actions.sources.validate_persistence.return_value = False
    redacted = await state.actions.save(saved, state.attempt)
    assert redacted.receipt_id != saved.receipt_id
    assert redacted.result["source_revoked"] is True and "text" not in redacted.result
    before = await snapshot(state)
    history = json.loads(before["document"])["receipt_lineage"]
    assert history[saved.receipt_id]["successor_receipt_id"] == redacted.receipt_id
    assert "synthetic-private-body" not in canonical(history) and "source" not in history[saved.receipt_id]
    assert await state.actions.save(saved, state.attempt) == redacted
    after = await snapshot(state)
    assert after["result_revision"] == before["result_revision"]
    assert len(await events(state)) == 2


@pytest.mark.asyncio
async def test_cancel_marker_gets_new_id_once_without_changing_attempt_or_provider(receipts):
    state = receipts
    original = replace(state.initial, receipt_id="pending-result", execution="unknown", effect="unknown")
    saved = await state.actions.save(original, state.attempt)
    async with state.database.transaction() as conn:
        await conn.execute("UPDATE capabilities.actions SET document=(document::jsonb||%s::jsonb)::text WHERE id=%s",
                           (canonical({"cancel_requested": True}), state.action.action_id))
    cancelled = await state.actions.save(saved, state.attempt)
    assert cancelled.receipt_id != saved.receipt_id
    assert cancelled.result["cancel_requested"] is True
    assert cancelled.result["sharing"] == {"decision": "archive", "reason": "cancel_requested"}
    before = await snapshot(state)
    assert before["attempt"]["attempt_id"] == state.attempt.attempt_id
    assert before["attempt"]["provider_handle"] == "provider-handle"
    assert await state.actions.save(saved, state.attempt) == cancelled
    after = await snapshot(state)
    assert after["result_revision"] == before["result_revision"]
    assert [event["event_id"] for event in await events(state)] == [saved.receipt_id, cancelled.receipt_id]


@pytest.mark.asyncio
async def test_original_unknown_receipt_remains_under_existing_withdrawal_rule(receipts):
    state = receipts
    action = replace(state.action, capability_id="video.observe")
    async with state.database.transaction() as conn:
        await conn.execute("UPDATE capabilities.actions SET action=%s::jsonb,kind=%s WHERE id=%s",
                           (encoded(action), action.capability_id, action.action_id))
    original = replace(state.initial, receipt_id="unknown-original", execution="unknown", effect="unknown",
                       result={"text": "synthetic-source-body", "source_refs": []})
    await state.actions.save(original, state.attempt)
    requested = replace(original, execution="succeeded", effect="none",
                        result={"outcome": "settled", "source_refs": []})
    settled = await state.actions.save(requested, state.attempt)
    saved = json.loads((await snapshot(state))["document"])
    assert saved["prior_unknown_receipts"][state.attempt.attempt_id] == wire(original)
    assert saved["receipt_lineage"][original.receipt_id]["successor_receipt_id"] == settled.receipt_id
    state.actions.sources.validate_persistence.return_value = False
    await state.actions.save(settled, state.attempt)
    redacted = json.loads((await snapshot(state))["document"])
    assert "prior_unknown_receipts" not in redacted
    assert "synthetic-source-body" not in canonical(redacted)
