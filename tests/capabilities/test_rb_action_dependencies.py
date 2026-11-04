"""Queued actions retain trusted behavior dependencies and original recovery identities."""

from importlib.resources import files
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio

from tianshu.capabilities.actions import Actions
from tianshu.capabilities.catalog import Availability, CapabilityDefinition, Catalog
from tianshu.capabilities.contracts import Fault
from tianshu.capabilities.execution_context import CURRENT_ACTION, CURRENT_EXECUTION
from tianshu.capabilities.tracking import TrackedBroker, TrackedPermit
from tianshu.capabilities.withdrawal import redacted
from tianshu.runtime.jobs import JobQueue
from tianshu.runtime.migrations import Migration, migrate
from tianshu.types import Action, Attempt, OriginKind, Receipt, TrustedContext


@pytest_asyncio.fixture
async def owner(database):
    await migrate(database, (Migration("0007_rb_runtime",
        files("tianshu.runtime").joinpath("rb_runtime.sql").read_text()),
        Migration("capabilities-0001", files("tianshu.capabilities").joinpath("schema.sql").read_text())))
    jobs = JobQueue(database.config.dsn)
    catalog = Catalog()
    async def execute(action, attempt):
        return Receipt(str(uuid4()), action.action_id, attempt.attempt_id,
                       "succeeded", "confirmed", "not_requested")
    execute = AsyncMock(side_effect=execute)
    catalog.register(SimpleNamespace(definition=CapabilityDefinition(
        "example.write", "example", "", {"type": "object"}, {"type": "object"}, "write"),
        availability=AsyncMock(return_value=Availability(True, True)), execute=execute))
    service = Actions(database, jobs, catalog, SimpleNamespace(check=AsyncMock()))
    service.sources = SimpleNamespace(validate_persistence=AsyncMock(return_value=True))
    service.register_tasks()
    try:
        yield service, execute
    finally:
        await service.close()
        await catalog.close()


def action():
    context = TrustedContext("author", "actor", None, "scene", ("scope",), ("scope",),
                             1, OriginKind.ACTOR, frozenset({"*"}))
    return Action("original-action", "original-intent", "example.write", "actor", context, {}, OriginKind.ACTOR)


@pytest.mark.asyncio
async def test_action_freezes_generation_and_child_cannot_replace_it(owner):
    service, _ = owner
    original = action()
    await service.invoke(original, background=True)
    async with service.database.transaction(read_only=True) as conn:
        generation = await service.jobs.current_generation_on(conn)
    assert await service.saved_generation(original) == generation
    token = CURRENT_EXECUTION.set({"data_generation": generation})
    try:
        with pytest.raises(Fault) as rejected:
            await service.invoke(original, generation="different-generation")
    finally:
        CURRENT_EXECUTION.reset(token)
    assert rejected.value.code == "job_generation_expired"


@pytest.mark.asyncio
async def test_withdrawal_after_queue_acceptance_stops_original_action_before_dispatch(owner):
    service, execute = owner
    original = action()
    refs = ({"rule_id": "rule", "semantic_revision": 3},)
    state = {"valid": True}

    async def validate(context, saved_refs, *, purpose):
        assert context == original.context and saved_refs == refs and purpose == "conversation"
        if not state["valid"]:
            raise Fault("behavior_invalidated", 409)

    service.bind_behavior_validator(validate)
    log = SimpleNamespace(emit=AsyncMock())
    service.jobs.events = log
    pending = await service.invoke(original, background=True, behavior_refs=refs)
    async with service.database.transaction(read_only=True) as conn:
        row = await (await conn.execute("SELECT document FROM capabilities.actions WHERE id=%s", (original.action_id,))).fetchone()
    assert '"rule_id":"rule"' in row["document"]
    assert "behavior_refs" not in original.arguments
    state["valid"] = False
    result = await service.run(original.action_id)
    assert (result.action_id, result.attempt_id) == (original.action_id, pending.attempt_id)
    assert (result.execution, result.effect, result.error_code) == ("failed", "none", "behavior_invalidated")
    execute.assert_not_awaited()
    log.emit.assert_awaited_once_with(
        "capability.failed", "failed", material_id=original.action_id, failure_id=pending.attempt_id,
        attempt_id=pending.attempt_id, error_code="behavior_invalidated", dependency_ref="example.write")


@pytest.mark.asyncio
async def test_pending_recovery_uses_expected_revision_attempt_and_original_job(owner):
    service, execute = owner
    original = action()
    pending = await service.invoke(original, background=True)
    snapshot = await service.inspect_recovery(original.action_id, original.context)
    job_id = snapshot["execution"]["job_id"]
    args = dict(operation="reroute_pending", client_request_id="recovery",
                expected_revision=snapshot["result_revision"], expected_attempt_id=pending.attempt_id,
                expected_job_id=job_id, guard_on=AsyncMock(), guard_attempt_id="ops-attempt",
                guard_owner_token="lease-token")
    assert await service.context_for_action(original.action_id) == original.context
    with pytest.raises(Fault) as changed:
        await service.invoke_registered_recovery(original.action_id, original.context,
                                                   **{**args, "expected_revision": args["expected_revision"] + 1})
    assert changed.value.code == "revision_conflict"
    rejected_guard = AsyncMock(return_value=False)
    with pytest.raises(Fault) as rejected:
        await service.invoke_registered_recovery(original.action_id, original.context,
                                                 **{**args, "guard_on": rejected_guard})
    assert rejected.value.code == "recovery_guard_rejected"
    assert (await service.inspect_recovery(original.action_id, original.context))["execution"]["job_id"] == job_id
    result = await service.invoke_registered_recovery(original.action_id, original.context, **args)
    args["guard_on"].assert_awaited_once()
    assert args["guard_on"].await_args.args[1:] == ("ops-attempt", "lease-token")
    observed = await service.observe_recovery(original.action_id, original.context,
                                               expected_attempt_id=pending.attempt_id)
    assert result.action_id == original.action_id and result.attempt_id == pending.attempt_id
    assert observed["execution"]["job_id"] == job_id
    execute.assert_not_awaited()
    with pytest.raises(Fault):
        await service.invoke_registered_recovery(original.action_id, original.context,
                                                  **{**args, "operation": "replay_unknown"})


@pytest.mark.asyncio
async def test_executor_lease_records_the_actual_independent_role(owner):
    service, _ = owner
    service.process_role = "independent"
    await service.start()
    async with service.database.transaction(read_only=True) as conn:
        row = await (await conn.execute("SELECT role FROM capabilities.execution_owners WHERE id=%s",
                                        (service.owners.id,))).fetchone()
    assert row["role"] == "independent"


@pytest.mark.asyncio
async def test_long_preparation_rechecks_behavior_at_actual_resource_submission():
    original = action()
    validator = AsyncMock(side_effect=Fault("behavior_invalidated", 409))
    owner = SimpleNamespace(owners=SimpleNamespace(check_attempt=AsyncMock()),
                            authority=SimpleNamespace(check=AsyncMock()),
                            handler=AsyncMock(return_value=object()),
                            catalog=SimpleNamespace(availability=AsyncMock(return_value=Availability(True, True))),
                            check_behaviors=validator)
    permit = SimpleNamespace(submitted=AsyncMock(), rejected=AsyncMock())
    current = {"owner": owner, "attempt": Attempt("attempt", original.action_id),
               "behavior_refs": ({"rule_id": "rule"},), "behavior_purpose": "conversation"}
    action_token = CURRENT_ACTION.set(original)
    execution_token = CURRENT_EXECUTION.set(current)
    try:
        with pytest.raises(Fault):
            await TrackedPermit(permit, current).submitted()
    finally:
        CURRENT_EXECUTION.reset(execution_token)
        CURRENT_ACTION.reset(action_token)
    permit.submitted.assert_not_awaited()
    permit.rejected.assert_awaited_once_with("behavior_invalidated")
    validator.assert_awaited_once()


@pytest.mark.asyncio
async def test_revoked_source_before_resource_submission_rejects_unused_permit():
    original = action()
    owner = SimpleNamespace(owners=SimpleNamespace(check_attempt=AsyncMock()),
                            authority=SimpleNamespace(check=AsyncMock(side_effect=Fault("source_revoked", 410))))
    permit = SimpleNamespace(submitted=AsyncMock(), rejected=AsyncMock())
    current = {"owner": owner, "attempt": Attempt("attempt", original.action_id)}
    action_token = CURRENT_ACTION.set(original)
    execution_token = CURRENT_EXECUTION.set(current)
    try:
        with pytest.raises(Fault, match="source_revoked"):
            await TrackedPermit(permit, current).submitted()
    finally:
        CURRENT_EXECUTION.reset(execution_token)
        CURRENT_ACTION.reset(action_token)
    permit.submitted.assert_not_awaited()
    permit.rejected.assert_awaited_once_with("source_revoked")


@pytest.mark.asyncio
async def test_model_request_carries_original_behavior_dependencies_to_broker():
    @dataclass(frozen=True)
    class Request:
        attempt_id: str
        behavior_refs: tuple = ()
        behavior_purpose: str = "conversation"
        data_generation: str | None = None

    original = action()
    refs = ({"rule_id": "rule", "semantic_revision": 3},)
    broker = SimpleNamespace(generate=AsyncMock(return_value=SimpleNamespace(
        receipt=SimpleNamespace(dispatch="not_started"))))
    owner = SimpleNamespace(owners=SimpleNamespace(check_attempt=AsyncMock()), check_behaviors=AsyncMock())
    current = {"owner": owner, "attempt": Attempt("attempt", original.action_id),
               "behavior_refs": refs, "behavior_purpose": "tool", "data_generation": "generation"}
    action_token = CURRENT_ACTION.set(original)
    execution_token = CURRENT_EXECUTION.set(current)
    try:
        await TrackedBroker(broker).generate(Request("model-attempt"))
    finally:
        CURRENT_EXECUTION.reset(execution_token)
        CURRENT_ACTION.reset(action_token)
    sent = broker.generate.await_args.args[0]
    assert sent.behavior_refs == refs and sent.behavior_purpose == "tool" and sent.data_generation == "generation"
    owner.check_behaviors.assert_awaited_once_with(original.context, refs, purpose="tool")


def test_revoked_partial_delivery_keeps_only_original_progress_and_identifiers():
    original = Receipt("receipt", "action", "attempt", "unknown", "unknown", "unknown", result={
        "message_ids": ["external-1"], "expression_id": "expression",
        "calls": [{"call_id": "call-1", "api_name": "send", "state": "confirmed",
                   "message_id": "external-1", "text": "private"}],
        "delivery_progress": {"state": "partial", "parts": [
            {"part_index": 0, "state": "confirmed", "message_id": "external-1",
             "submitted_at": "2026-11-05T00:00:00Z", "text": "private", "media": {"uri": "secret"}},
            {"part_index": 1, "state": "unknown", "dispatch": "submitted", "body": "private"}]},
        "content": "private", "attachments": [{"uri": "secret"}],
    })
    cleared = redacted(original)
    assert (cleared.action_id, cleared.attempt_id, cleared.execution, cleared.effect, cleared.delivery) == (
        "action", "attempt", "unknown", "unknown", "unknown")
    assert cleared.result["message_ids"] == ["external-1"]
    assert cleared.result["calls"][0] == {"call_id": "call-1", "api_name": "send",
                                          "state": "confirmed", "message_id": "external-1"}
    assert cleared.result["delivery_progress"]["parts"][1]["dispatch"] == "submitted"
    assert "private" not in str(cleared.result) and "secret" not in str(cleared.result)
