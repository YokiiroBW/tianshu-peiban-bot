"""The Action port hands off only a fenced original result, never an operation."""

from dataclasses import replace
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg.errors import LockNotAvailable

from tianshu.capabilities.actions import Actions, encoded
from tianshu.capabilities.catalog import Catalog
from tianshu.capabilities.contracts import canonical
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobQueue
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.serialization import wire
from tianshu.types import Action, Attempt, OriginKind, Receipt, SourceRef, TrustedContext


class Sources:
    def __init__(self):
        self.revoked = False
        self.error = None
        self.validate_persistence = AsyncMock(side_effect=self.validate)

    async def validate(self, connection, context, refs):
        await connection.execute("INSERT INTO test_source_checks(context_id,refs) VALUES(%s,%s::jsonb)",
                                 (context.principal_id, canonical(wire(refs))))
        if self.error:
            raise self.error
        return not self.revoked


class ResultEvents:
    """Fixture for the frozen ensure_on port; rows share the caller transaction."""
    def __init__(self, database):
        self.database = database
        self.outcome = None
        self.error = None
        self.check_lock = False
        self.ensure_on = AsyncMock(side_effect=self.ensure)
        self.publish = AsyncMock()

    async def ensure(self, connection, event):
        head = await (await connection.execute(
            "SELECT receipt,result_revision FROM capabilities.actions WHERE id=%s", (event["action_id"],))).fetchone()
        assert head["receipt"] == event["receipt"] and head["result_revision"] == event["result_revision"]
        if self.check_lock:
            with pytest.raises(LockNotAvailable):
                async with self.database.transaction() as other:
                    await other.execute("SET LOCAL lock_timeout='100ms'")
                    await other.execute("UPDATE capabilities.actions SET result_revision=result_revision+1 WHERE id=%s",
                                        (event["action_id"],))
        if self.error:
            raise self.error
        if self.outcome is not None:
            return self.outcome
        row = await (await connection.execute(
            "INSERT INTO test_result_handoffs(receipt_id,event) VALUES(%s,%s::jsonb) "
            "ON CONFLICT(receipt_id) DO NOTHING RETURNING id", (event["event_id"], canonical(event)))).fetchone()
        if row:
            return {"state": "accepted", "reason": "scheduled", "job_id": row["id"], "consumer_progress": {}}
        row = await (await connection.execute(
            "SELECT id,event FROM test_result_handoffs WHERE receipt_id=%s", (event["event_id"],))).fetchone()
        if canonical(row["event"]) != canonical(event):
            raise AppError("result_receipt_revision_conflict", status=409)
        return {"state": "accepted", "reason": "already_queued", "job_id": row["id"], "consumer_progress": {}}


@pytest_asyncio.fixture
async def original_action(database):
    await migrate(database, (Migration("capabilities-0001",
        files("tianshu.capabilities").joinpath("schema.sql").read_text()),))
    jobs = JobQueue(database.config.dsn, database=database)
    sources, events = Sources(), ResultEvents(database)
    identity = SimpleNamespace(check=AsyncMock())
    actions = Actions(database, jobs, Catalog(), identity, events)
    actions.sources = sources
    actions.invoke = AsyncMock(side_effect=AssertionError("must not invoke an Action"))
    actions.resume = AsyncMock(side_effect=AssertionError("must not resume an Action"))
    actions.run = AsyncMock(side_effect=AssertionError("must not execute an Action"))
    context = TrustedContext("principal", "actor", None, "scene", ("scope",), ("scope",), 1,
                             OriginKind.ACTOR, frozenset({"*"}))
    source = SourceRef("source", 1, {"unit": "characters", "start": 0, "end": 5})
    action = Action("action", "intent", "content.read", "actor", context, {}, OriginKind.ACTOR,
                    source_refs=(source,))
    attempt = Attempt("attempt", action.action_id, dispatch="submitted", provider_handle="original-handle")
    pending = Receipt("pending", action.action_id, attempt.attempt_id, "pending", "none", "not_requested")
    async with database.transaction() as conn:
        generation = await jobs.current_generation_on(conn)
        await conn.execute("CREATE TABLE test_source_checks(id bigint GENERATED ALWAYS AS IDENTITY, context_id text, refs jsonb)")
        await conn.execute("CREATE TABLE test_result_handoffs(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "
                           "receipt_id text UNIQUE NOT NULL, event jsonb NOT NULL)")
        await conn.execute(
            "INSERT INTO capabilities.actions(id,intent,semantics,principal_id,kind,created_at,document,"
            "action,attempt,receipt,result_revision,result_observed_at) "
            "VALUES(%s,%s,%s,%s,%s,0,%s,%s::jsonb,%s::jsonb,%s::jsonb,1,now())",
            (action.action_id, action.intent_key, "fixture", context.principal_id, action.capability_id,
             canonical({"data_generation": generation}), encoded(action), encoded(attempt), encoded(pending)))
        await conn.execute("INSERT INTO capabilities.attempts(id,action_id,attempt,receipt) "
                           "VALUES(%s,%s,%s::jsonb,%s::jsonb)",
                           (attempt.attempt_id, action.action_id, encoded(attempt), encoded(pending)))
    yield SimpleNamespace(database=database, jobs=jobs, actions=actions, context=context, action=action,
                          attempt=attempt, pending=pending, source=source, sources=sources, events=events,
                          generation=generation, identity=identity)
    await actions.close()


async def set_head(value, receipt, revision, *, action=None):
    async with value.database.transaction() as conn:
        await conn.execute("UPDATE capabilities.actions SET action=%s::jsonb,receipt=%s::jsonb,"
                           "result_revision=%s,result_observed_at=now() WHERE id=%s",
                           (encoded(action or value.action), encoded(receipt), revision, value.action.action_id))


async def counts(value):
    async with value.database.transaction(read_only=True) as conn:
        checks = await (await conn.execute("SELECT count(*) AS n FROM test_source_checks")).fetchone()
        handoffs = await (await conn.execute("SELECT count(*) AS n FROM test_result_handoffs")).fetchone()
        attempts = await (await conn.execute("SELECT attempt FROM capabilities.attempts WHERE action_id=%s",
                                            (value.action.action_id,))).fetchall()
    return checks["n"], handoffs["n"], attempts


def terminal(value, receipt_id="terminal", *, execution="succeeded", effect="confirmed"):
    return Receipt(receipt_id, value.action.action_id, value.attempt.attempt_id, execution, effect,
                   "not_requested", result={"source_refs": [wire(value.source)], "phase": "original"})


@pytest.mark.asyncio
async def test_save_keeps_existing_publish_envelope_for_unknown_receipt(original_action):
    value = original_action
    unknown = terminal(value, "unknown", execution="unknown", effect="unknown")
    saved = await value.actions.save(unknown, value.attempt)
    async with value.database.transaction(read_only=True) as conn:
        row = await (await conn.execute("SELECT action,receipt,result_revision,result_observed_at "
                                        "FROM capabilities.actions WHERE id=%s", ("action",))).fetchone()
    event = value.events.publish.await_args.args[1]
    assert saved == unknown
    assert event == {"type": "capability.result", "version": 1, "event_id": "unknown", "action_id": "action",
                     "result_revision": row["result_revision"], "result_observed_at": row["result_observed_at"].isoformat(),
                     "context": row["action"]["context"], "source_refs": row["action"]["source_refs"],
                     "receipt": row["receipt"]}
    value.events.ensure_on.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_running_wait_but_unknown_is_handed_off_unchanged(original_action):
    value = original_action
    initial = await value.actions.ensure_result_consumption(value.context, "action", data_generation=value.generation)
    assert initial == {"state": "waiting", "reason": "action_pending", "action_id": "action",
                       "receipt_id": None, "result_revision": None, "consumer_progress": {}}
    running = replace(value.pending, receipt_id="running", execution="running")
    await set_head(value, running, 2)
    waiting = await value.actions.ensure_result_consumption(value.context, "action", data_generation=value.generation,
        expected_result_revision=1, expected_receipt_id="pending")
    assert waiting == {"state": "waiting", "reason": "action_running", "action_id": "action",
                       "receipt_id": None, "result_revision": None, "consumer_progress": {}}
    value.events.ensure_on.assert_not_awaited()
    unknown = terminal(value, "unknown", execution="unknown", effect="unknown")
    await set_head(value, unknown, 3)
    value.events.check_lock = True
    handed = await value.actions.ensure_result_consumption(value.context, "action", data_generation=value.generation,
        expected_result_revision=2, expected_receipt_id="running")
    assert handed["state"] == "accepted" and handed["receipt_id"] == "unknown" and handed["result_revision"] == 3
    event = value.events.ensure_on.await_args.args[1]
    assert event["receipt"] == wire(unknown) and event["receipt"]["effect"] == "unknown"
    assert event["event_id"] == "unknown" and event["context"] == wire(value.context)
    assert event["source_refs"] == [wire(value.source)]
    assert value.sources.validate_persistence.await_args.args[1:] == (value.context, (value.source,))
    assert (await counts(value))[2][0]["attempt"]["provider_handle"] == "original-handle"
    value.actions.invoke.assert_not_awaited()
    value.actions.resume.assert_not_awaited()
    value.actions.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_handoff_preserves_event_and_one_wake(original_action):
    value = original_action
    actual = terminal(value)
    await set_head(value, actual, 2)
    first = await value.actions.ensure_result_consumption(value.context, "action", data_generation=value.generation)
    second = await value.actions.ensure_result_consumption(value.context, "action", data_generation=value.generation,
        expected_result_revision=2, expected_receipt_id="terminal")
    assert first["reason"] == "scheduled" and second["reason"] == "already_queued"
    assert first["job_id"] == second["job_id"] and (await counts(value))[1] == 1
    assert value.events.ensure_on.await_count == 2
    assert value.events.ensure_on.await_args_list[0].args[1] == value.events.ensure_on.await_args_list[1].args[1]
    value.events.publish.assert_not_awaited()
    value.actions.invoke.assert_not_awaited()
    value.actions.resume.assert_not_awaited()


@pytest.mark.parametrize("execution,effect", [("failed", "none"), ("cancelled", "none")])
@pytest.mark.asyncio
async def test_failed_and_cancelled_terminal_receipts_remain_original(original_action, execution, effect):
    value = original_action
    actual = terminal(value, execution, execution=execution, effect=effect)
    await set_head(value, actual, 2)
    result = await value.actions.ensure_result_consumption(value.context, "action", data_generation=value.generation)
    event = value.events.ensure_on.await_args.args[1]
    assert result["receipt_id"] == actual.receipt_id and result["result_revision"] == 2
    assert event["receipt"] == wire(actual) and event["receipt"]["execution"] == execution
    value.actions.invoke.assert_not_awaited()
    value.actions.resume.assert_not_awaited()


@pytest.mark.parametrize("outcome", [
    {"state": "accepted", "reason": "already_queued", "job_id": 7, "consumer_progress": {}},
    {"state": "waiting", "reason": "execution_wait", "due_at": "2030-01-01T00:00:00+00:00", "consumer_progress": {}},
    {"state": "blocked", "reason": "execution_budget_exhausted", "budget": {"failures": 4}, "consumer_progress": {}},
    {"state": "verified", "reason": "legacy_aggregate", "consumer_progress": {}},
    {"state": "verified", "reason": "result_already_consumed", "consumer_progress": {
        "life_activity": {"data_generation": "gen", "action_id": "action", "receipt_id": "terminal",
                          "result_revision": 2, "disposition": "done", "reason": None,
                          "proof": {"activity_id": "activity", "last_result_revision": 2,
                                    "last_receipt_id": "terminal"}}}},
    {"state": "verified", "reason": "superseded", "consumer_progress": {
        "life_activity": {"data_generation": "gen", "action_id": "action", "receipt_id": "terminal",
                          "result_revision": 2, "disposition": "not_applicable", "reason": "superseded", "proof": {}}}},
])
@pytest.mark.asyncio
async def test_terminal_handoff_preserves_real_consumer_status(original_action, outcome):
    value = original_action
    actual = terminal(value)
    await set_head(value, actual, 2)
    value.events.outcome = outcome
    result = await value.actions.ensure_result_consumption(value.context, "action", data_generation=value.generation)
    assert result == {**outcome, "action_id": "action", "receipt_id": "terminal", "result_revision": 2}


@pytest.mark.asyncio
async def test_head_advances_between_read_and_fence_then_only_new_head_is_handed_off(original_action):
    value = original_action
    current_get = value.actions.get
    final = terminal(value)
    reads = 0
    async def racing_get(*args):
        nonlocal reads
        snapshot = await current_get(*args)
        reads += 1
        if reads == 1:
            await set_head(value, final, 2)
        return snapshot
    value.actions.get = racing_get
    result = await value.actions.ensure_result_consumption(value.context, "action", data_generation=value.generation,
        expected_result_revision=1, expected_receipt_id="pending")
    assert reads == 2 and result["state"] == "accepted" and result["receipt_id"] == final.receipt_id
    assert value.events.ensure_on.await_count == 1
    assert value.events.ensure_on.await_args.args[1]["receipt"] == wire(final)
    checks, handoffs, attempts = await counts(value)
    assert checks == 1 and handoffs == 1 and len(attempts) == 1  # first source-check transaction rolled back
    value.actions.invoke.assert_not_awaited()
    value.actions.resume.assert_not_awaited()


@pytest.mark.parametrize("changed", ["receipt", "source", "context", "attempt"])
@pytest.mark.asyncio
async def test_same_revision_change_never_enters_event_port(original_action, changed):
    value = original_action
    actual = terminal(value)
    await set_head(value, actual, 2)
    current_get = value.actions.get
    async def racing_get(*args):
        snapshot = await current_get(*args)
        if changed == "receipt":
            await set_head(value, replace(actual, result={"phase": "different"}), 2)
        elif changed == "source":
            newer_ref = SourceRef("different-source", 1)
            await set_head(value, actual, 2, action=replace(value.action, source_refs=(newer_ref,)))
        elif changed == "context":
            newer_context = replace(value.context, conversation_id="different-scene")
            await set_head(value, actual, 2, action=replace(value.action, context=newer_context))
        else:
            async with value.database.transaction() as conn:
                await conn.execute("UPDATE capabilities.actions SET attempt=%s::jsonb WHERE id=%s",
                                   (encoded(replace(value.attempt, attempt_id="different-attempt")), "action"))
        return snapshot
    value.actions.get = racing_get
    with pytest.raises(AppError) as failure:
        await value.actions.ensure_result_consumption(value.context, "action", data_generation=value.generation)
    assert failure.value.code == ("result_source_set_changed" if changed == "source" else "result_receipt_revision_conflict")
    value.events.ensure_on.assert_not_awaited()
    assert (await counts(value))[0:2] == (0, 0)


@pytest.mark.parametrize("changed", ["regressed", "same_revision_body", "same_receipt_newer"])
@pytest.mark.asyncio
async def test_locked_advanced_head_cannot_regress_or_mutate_before_reread(original_action, changed):
    value = original_action
    current_get = value.actions.get
    advanced = terminal(value, "advanced")
    reads = 0
    async def racing_get(*args):
        nonlocal reads
        reads += 1
        if reads == 1:
            snapshot = await current_get(*args)
            await set_head(value, advanced, 5)
            return snapshot
        next_receipt = replace(advanced, result={"phase": "different"}) if changed == "same_revision_body" else advanced
        next_revision = 4 if changed == "regressed" else 6 if changed == "same_receipt_newer" else 5
        await set_head(value, next_receipt, next_revision)
        return await current_get(*args)
    value.actions.get = racing_get
    with pytest.raises(AppError) as failure:
        await value.actions.ensure_result_consumption(value.context, "action", data_generation=value.generation)
    assert failure.value.code == ("result_head_regressed" if changed == "regressed" else "result_receipt_revision_conflict")
    value.events.ensure_on.assert_not_awaited()


@pytest.mark.asyncio
async def test_source_revoked_after_advance_is_not_retried_or_handed_off(original_action):
    value = original_action
    current_get = value.actions.get
    reads = 0
    async def racing_get(*args):
        nonlocal reads
        reads += 1
        snapshot = await current_get(*args)
        if reads == 1:
            await set_head(value, terminal(value), 2)
        else:
            value.sources.revoked = True
        return snapshot
    value.actions.get = racing_get
    with pytest.raises(AppError, match="source_unavailable") as failure:
        await value.actions.ensure_result_consumption(value.context, "action", data_generation=value.generation)
    assert failure.value.status == 403 and reads == 2
    value.events.ensure_on.assert_not_awaited()
    assert (await counts(value))[0:2] == (0, 0)


@pytest.mark.asyncio
async def test_continuous_head_advance_is_bounded_and_not_reported_as_accepted(original_action):
    value = original_action
    current_get = value.actions.get
    reads = 0
    async def racing_get(*args):
        nonlocal reads
        snapshot = await current_get(*args)
        reads += 1
        await set_head(value, terminal(value, "new-" + str(reads)), snapshot["result_revision"] + 1)
        return snapshot
    value.actions.get = racing_get
    with pytest.raises(AppError, match="result_head_advanced"):
        await value.actions.ensure_result_consumption(value.context, "action", data_generation=value.generation)
    assert reads == 3 and (await counts(value))[0:2] == (0, 0)
    value.events.ensure_on.assert_not_awaited()


@pytest.mark.asyncio
async def test_snapshot_expectations_authority_generation_and_source_fail_closed(original_action):
    value = original_action
    await set_head(value, terminal(value), 3)
    for options, code in [
        ({"expected_result_revision": 3}, "result_expectation_incomplete"),
        ({"expected_receipt_id": "terminal"}, "result_expectation_incomplete"),
        ({"expected_result_revision": True, "expected_receipt_id": "terminal"}, "result_expectation_invalid"),
        ({"expected_result_revision": -1, "expected_receipt_id": "terminal"}, "result_expectation_invalid"),
        ({"expected_result_revision": 3, "expected_receipt_id": "different"}, "result_receipt_revision_conflict"),
        ({"expected_result_revision": 4, "expected_receipt_id": "future"}, "result_head_regressed"),
        ({"expected_result_revision": 2, "expected_receipt_id": "terminal"}, "result_receipt_revision_conflict"),
    ]:
        with pytest.raises(AppError) as failure:
            await value.actions.ensure_result_consumption(value.context, "action", data_generation=value.generation, **options)
        assert failure.value.code == code
    for bad_context in (replace(value.context, principal_id="other"), replace(value.context, actor_id="other"),
                        replace(value.context, scope_ids=())):
        with pytest.raises(AppError) as failure:
            await value.actions.ensure_result_consumption(bad_context, "action", data_generation=value.generation)
        assert failure.value.code in {"not_found", "forbidden"}
    with pytest.raises(AppError, match="not_found"):
        await value.actions.ensure_result_consumption(value.context, "missing-action", data_generation=value.generation)
    value.identity.check.side_effect = AppError("access_changed", status=409)
    with pytest.raises(AppError, match="access_changed"):
        await value.actions.ensure_result_consumption(value.context, "action", data_generation=value.generation)
    value.identity.check.side_effect = None
    with pytest.raises(AppError, match="job_generation_expired"):
        await value.actions.ensure_result_consumption(value.context, "action", data_generation=str(uuid4()))
    value.sources.revoked = True
    with pytest.raises(AppError, match="source_unavailable"):
        await value.actions.ensure_result_consumption(value.context, "action", data_generation=value.generation)
    value.events.ensure_on.assert_not_awaited()


@pytest.mark.asyncio
async def test_generation_change_after_snapshot_is_not_swallowed(original_action):
    value = original_action
    await set_head(value, terminal(value), 2)
    current_get = value.actions.get
    async def changed_generation(*args):
        snapshot = await current_get(*args)
        async with value.database.transaction() as conn:
            await conn.execute("UPDATE runtime.installation_generation SET id=%s WHERE singleton=true", (str(uuid4()),))
        return snapshot
    value.actions.get = changed_generation
    with pytest.raises(AppError, match="job_generation_expired"):
        await value.actions.ensure_result_consumption(value.context, "action", data_generation=value.generation)
    value.events.ensure_on.assert_not_awaited()


@pytest.mark.parametrize("code,status", [("result_head_advanced", 409), ("forbidden", 403),
                                          ("result_head_unavailable", 404), ("result_receipt_revision_conflict", 409)])
@pytest.mark.asyncio
async def test_event_port_errors_are_not_confused_with_a_fence_advance(original_action, code, status):
    value = original_action
    await set_head(value, terminal(value), 2)
    value.events.error = AppError(code, status=status)
    with pytest.raises(AppError) as failure:
        await value.actions.ensure_result_consumption(value.context, "action", data_generation=value.generation)
    assert failure.value.code == code and failure.value.status == status
    assert value.events.ensure_on.await_count == 1
    assert (await counts(value))[0:2] == (0, 0)


@pytest.mark.asyncio
async def test_missing_or_invalid_result_port_never_claims_handoff(original_action):
    value = original_action
    await set_head(value, terminal(value), 2)
    value.actions.events = None
    blocked = await value.actions.ensure_result_consumption(value.context, "action", data_generation=value.generation)
    assert blocked == {"state": "blocked", "reason": "result_events_unavailable", "action_id": "action",
                       "receipt_id": "terminal", "result_revision": 2, "consumer_progress": {}}
    value.actions.events = value.events
    for invalid in ({"state": "accepted", "consumer_progress": {}},
                    {"state": "accepted", "reason": "scheduled"},
                    {"state": "verified", "reason": "completed", "consumer_progress": None}):
        value.events.outcome = invalid
        with pytest.raises(AppError, match="result_wake_contract_invalid"):
            await value.actions.ensure_result_consumption(value.context, "action", data_generation=value.generation)
    assert (await counts(value))[1] == 0
