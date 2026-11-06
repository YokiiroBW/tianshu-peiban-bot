"""A bounded original-result repair commits real Sources, Life, Goal and Memory."""

from datetime import UTC, datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from psycopg.errors import QueryCanceled

from tianshu.capabilities.actions import Actions, encoded
from tianshu.capabilities.catalog import Catalog
from tianshu.capabilities.contracts import canonical
from tianshu.conversation import rbg_result_ledger as ledger
from tianshu.conversation.codec import document, signature, source_refs
from tianshu.conversation.rbg_result_recovery import ResultRecovery
from tianshu.conversation.rbi_result_wake import EXECUTION_OWNER, MAX_FAILURES, execution_identity
from tianshu.conversation.results import ActionResultEvents, ResultConsumer
from tianshu.identity.service import IdentityService
from tianshu.life.service import LifeService
from tianshu.life.worker import LifeWorker
from tianshu.memory.claims import ClaimService
from tianshu.memory.service import MemoryService
from tianshu.runtime import jobs as runtime_jobs
from tianshu.runtime.commands import CommandStore
from tianshu.runtime.files import FileStore
from tianshu.runtime.jobs import JobQueue
from tianshu.runtime.migrations import migrate, product_migrations
from tianshu.runtime.serialization import wire
from tianshu.sources.service import SourceService
from tianshu.types import Action, Attempt, Coverage, OriginKind, Receipt


_spec = spec_from_file_location(
    "rbj_domain_source_graph", Path(__file__).parents[1] / "sources" / "test_rbj_graph_proofs.py"
)
_graph = module_from_spec(_spec)
_spec.loader.exec_module(_graph)

REPAIR_REF = "rbj-real-domain-repair"
REPAIR_REVISION = "rbj-verified-source-proof"
DEFECT_KEY = "source-proof-depth"
CONSUMER = "life_activity"
OTHER_REASONS = {
    "renderer": "no_render_plan", "dialogue": "no_tool_link",
    "life_commitment": "not_life_owned", "detached": "result_owned_elsewhere",
    "memory": "result_owned_elsewhere_or_nonterminal",
}


async def prepare(database, tmp_path, record_property):
    # Use the same fresh product migration path as deployed roles, with no network clients.
    await migrate(database, product_migrations())
    commands = CommandStore(database)
    jobs = JobQueue(database.config.dsn, database=database)
    jobs.register_static_routes()
    identity = IdentityService(database, commands)
    sources = SourceService(database, identity, FileStore(tmp_path / "files"), commands, jobs)
    identity.bind_sources(sources)
    models = SimpleNamespace(submit=AsyncMock(side_effect=AssertionError("no model invocation")))
    memory = MemoryService(database, identity, sources, commands, jobs, models)
    memory.bind_claims(ClaimService(memory))
    life = LifeService(database, commands, jobs, identity, None, None, sources=sources, memory=memory)
    actions = Actions(database, jobs, Catalog(), identity, ActionResultEvents(jobs))
    actions.sources = sources
    for operation in ("invoke", "resume", "run"):
        setattr(actions, operation, AsyncMock(side_effect=AssertionError("original Action must not execute again")))
    worker = LifeWorker(life, models, None, actions.catalog, actions, None)
    jobs.register("life.plan", worker.plan)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO identity.people(person_id,display_name) VALUES ('owner-person','Owner')")
        await connection.execute("INSERT INTO identity.principals(principal_id,username,display_name,person_id,permissions) "
                                 "VALUES ('owner','owner','Owner','owner-person',ARRAY['*'])")
    owner = await identity.context("owner")
    async with database.transaction() as connection:
        actor_id = await identity.register_actor(owner, connection=connection)
    context = await identity.context("owner", actor_id=actor_id, origin_kind=OriginKind.ACTOR)
    refs = await _graph._seed_field_graph(SimpleNamespace(database=database, identity=identity), context)
    started = perf_counter()
    async with database.transaction() as connection:
        assert await sources.check_many_on(connection, context, (refs,)) == (True,)
    record_property("source_graph_64_depth_seconds", perf_counter() - started)
    action = Action("original-action", "original-intent", "content.read", actor_id, context, {},
                    OriginKind.ACTOR, activity_id="reading", source_refs=refs)
    attempt = Attempt("original-attempt", action.action_id, dispatch="submitted", provider_handle="original-handle")
    result = Receipt("original-receipt", action.action_id, attempt.attempt_id, "succeeded", "none", "not_requested",
                     coverage=Coverage(True), result={"read_basis": "actual_source_body", "source_refs": wire(refs),
                         "summary": "The selected source body was read.", "content": {"text": "abcdefgh"}})
    observed_at = datetime.now(UTC)
    event = {"type": "capability.result", "version": 1, "event_id": result.receipt_id,
             "action_id": action.action_id, "result_revision": 2, "result_observed_at": observed_at.isoformat(),
             "context": wire(context), "source_refs": wire(refs), "receipt": wire(result)}
    async with database.transaction() as connection:
        generation = await jobs.current_generation_on(connection)
        await connection.execute("INSERT INTO life.actor_state(actor_id) VALUES (%s)", (actor_id,))
        await connection.execute("INSERT INTO life.goals(id,actor_id,title,motivation,state,scope_ids,criteria) "
            "VALUES ('goal',%s,'Read the source','Answer the actual question','active',%s,%s)",
            (actor_id, list(context.scope_ids), document({"kind": "completed_activity", "capability_id": "content.read", "target_count": 1})))
        await connection.execute("INSERT INTO life.activities(id,actor_id,goal_id,title,kind,state,action_id,"
            "last_result_revision,scope_ids,checkpoint_data,context,source_refs) "
            "VALUES ('reading',%s,'goal','Read the source','reading','dormant',%s,1,%s,%s,%s,%s)",
            (actor_id, action.action_id, list(context.scope_ids), document({"capability_id": "content.read", "planning_context": wire(context)}),
             document(context), document(refs)))
        await connection.execute("INSERT INTO capabilities.actions(id,intent,semantics,principal_id,kind,created_at,"
            "document,action,attempt,receipt,result_revision,result_observed_at) VALUES(%s,%s,'fixture',%s,%s,0,%s,%s::jsonb,%s::jsonb,%s::jsonb,2,%s)",
            (action.action_id, action.intent_key, context.principal_id, action.capability_id, canonical({"data_generation": generation}),
             encoded(action), encoded(attempt), encoded(result), observed_at))
        await connection.execute("INSERT INTO capabilities.attempts(id,action_id,attempt,receipt) VALUES(%s,%s,%s::jsonb,%s::jsonb)",
                                (attempt.attempt_id, action.action_id, encoded(attempt), encoded(result)))
    # The public Action handoff creates the original delivery without invoking its capability.
    wake = await actions.ensure_result_consumption(context, action.action_id, data_generation=generation,
                                                  expected_result_revision=2, expected_receipt_id=result.receipt_id)
    assert wake["state"] == "accepted"
    original_job_id = wake["job_id"]
    async with database.transaction() as connection:
        await ledger.initialize_on(connection, generation, event, wire(action), actor_id)
        # These five consumers are already durably not applicable, as in the affected live window.
        for name, reason in OTHER_REASONS.items():
            token, _ = await ledger.claim_on(connection, generation, result.receipt_id, name)
            await ledger.complete_on(connection, generation, result.receipt_id, name, token, "not_applicable",
                                     reason=reason, proof={"rendered": False} if name == "renderer" else {})
        previous_progress = await ledger.progress_on(connection, generation, result.receipt_id)
    job_id = original_job_id
    for index in range(4):
        async with database.transaction() as connection:
            # Native started/failed transitions supply authoritative original execution history.
            await connection.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
            await connection.execute("UPDATE procrastinate_jobs SET status='failed',attempts=attempts+1 WHERE id=%s", (job_id,))
        if index < 3:
            async with database.transaction() as connection:
                next_wake = await ActionResultEvents(jobs).ensure_on(connection, event)
                assert next_wake["state"] == "accepted"
                job_id = next_wake["job_id"]
    conversation = SimpleNamespace(jobs=jobs, sources=sources, on_result=AsyncMock())
    renderer = SimpleNamespace(on_action_result=AsyncMock())
    consumer = ResultConsumer(database, identity, actions, memory, None, conversation, worker, renderer)
    recovery = ResultRecovery(database, jobs, identity, actions=actions, life_worker=worker)
    expected = dict(data_generation=generation, action_id=action.action_id, receipt_id=result.receipt_id, result_revision=2)
    async with database.transaction() as connection:
        await jobs.register_execution_repair_on(connection, owner=EXECUTION_OWNER, repair_ref=REPAIR_REF,
            repair_revision=REPAIR_REVISION, defect_key=DEFECT_KEY, consumer=CONSUMER)
    authorized = await recovery.authorize_local_repair(original_job_id, **expected, repair_ref=REPAIR_REF,
        repair_revision=REPAIR_REVISION, defect_key=DEFECT_KEY, repair_consumer=CONSUMER,
        defect_evidence_ref="verified-native-failure-history", material_digest=signature(event))
    assert authorized["state"] == "accepted" and authorized["owner_id"] == "reading"
    repair = await recovery.recover_result(original_job_id, **expected, repair_ref=REPAIR_REF, repair_consumer=CONSUMER)
    assert repair["state"] == "accepted"
    return SimpleNamespace(database=database, jobs=jobs, sources=sources, identity=identity, memory=memory,
        life=life, actions=actions, worker=worker, models=models, consumer=consumer, renderer=renderer,
        conversation=conversation, recovery=recovery, context=context, action=action, attempt=attempt, result=result,
        event=event, generation=generation, original_job_id=original_job_id, repair_job_id=repair["job_id"],
        expected=expected, previous_progress=previous_progress)


@pytest.mark.parametrize("cancel_after_memory_write", [False, True])
async def test_real_source_life_goal_memory_and_proof_share_the_original_repair(empty_database, tmp_path, record_property,
                                                                              cancel_after_memory_write):
    value = await prepare(empty_database, tmp_path, record_property)
    original_observe = value.memory.observe_episode
    memory_calls = 0

    async def observe(context, episode, *, connection):
        nonlocal memory_calls
        memory_calls += 1
        saved = await original_observe(context, episode, connection=connection)
        goal = await (await connection.execute("SELECT progress,state FROM life.goals WHERE id='goal'")).fetchone()
        assert goal == {"progress": 1, "state": "completed"}
        proof_started = perf_counter()
        assert await value.sources.check_many_on(connection, context, (source_refs(episode["source_refs"]),)) == (True,)
        record_property("source_graph_65_depth_seconds", perf_counter() - proof_started)
        # Another connection cannot see the new receipt original or Memory before this transaction commits.
        assert not (await value.identity.check_many(context, (source_refs(episode["source_refs"]),)))[0]
        if cancel_after_memory_write:
            raise QueryCanceled("fixture cancellation after real Memory persisted inside Life transaction")
        return saved

    value.memory.observe_episode = observe
    async with empty_database.transaction() as connection:
        await connection.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (value.repair_job_id,))
        native = await (await connection.execute("SELECT attempts FROM procrastinate_jobs WHERE id=%s", (value.repair_job_id,))).fetchone()
    generation_token = runtime_jobs._execution_generation.set(value.generation)
    job_token = runtime_jobs._execution_identity.set((value.repair_job_id, native["attempts"], value.generation))
    started = perf_counter()
    try:
        if cancel_after_memory_write:
            with pytest.raises(QueryCanceled):
                await value.consumer.consume(value.event, repair_ref=REPAIR_REF, repair_consumer=CONSUMER)
        else:
            await value.consumer.consume(value.event, repair_ref=REPAIR_REF, repair_consumer=CONSUMER)
    finally:
        record_property("real_repair_apply_seconds", perf_counter() - started)
        runtime_jobs._execution_identity.reset(job_token)
        runtime_jobs._execution_generation.reset(generation_token)
    async with empty_database.transaction() as connection:
        await connection.execute("UPDATE procrastinate_jobs SET status=%s,attempts=attempts+1 WHERE id=%s",
                                 ("failed" if cancel_after_memory_write else "succeeded", value.repair_job_id))
    async with empty_database.transaction(read_only=True) as connection:
        activity = await (await connection.execute("SELECT state,last_result_revision,last_receipt_id FROM life.activities WHERE id='reading'")).fetchone()
        goal = await (await connection.execute("SELECT progress,state FROM life.goals WHERE id='goal'")).fetchone()
        goal_proofs = await (await connection.execute("SELECT action_id,result_revision,source_refs FROM life.goal_progress_receipts WHERE goal_id='goal'")).fetchall()
        originals = await (await connection.execute("SELECT revision FROM sources.versions WHERE evidence_unit='life:action:result:original-action'")).fetchall()
        episodes = await (await connection.execute("SELECT event_id,event_revision,action_id,receipt_id,source_refs FROM memory.episodes WHERE event_id='action:original-action'")).fetchall()
        progress = await ledger.progress_on(connection, value.generation, value.result.receipt_id)
        delivery = await (await connection.execute("SELECT state,original_job_id FROM conversation.result_deliveries WHERE receipt_id=%s", (value.result.receipt_id,))).fetchone()
        aggregate = await (await connection.execute("SELECT action_id FROM conversation.result_consumption WHERE receipt_id=%s", (value.result.receipt_id,))).fetchone()
        budget = await value.jobs.inspect_execution_on(connection, owner=EXECUTION_OWNER,
            identity=execution_identity(value.generation, value.event), data_generation=value.generation,
            max_failures=MAX_FAILURES, repair_ref=REPAIR_REF, consumer=CONSUMER)
        head = await (await connection.execute("SELECT action,attempt,receipt,result_revision FROM capabilities.actions WHERE id=%s", (value.action.action_id,))).fetchone()
        attempts = await (await connection.execute("SELECT attempt FROM capabilities.attempts WHERE action_id=%s", (value.action.action_id,))).fetchall()
    assert memory_calls == 1 and budget["failed_executions"] == 4
    assert all(progress[name] == value.previous_progress[name] for name in OTHER_REASONS)
    assert delivery["original_job_id"] == value.original_job_id
    assert head == {"action": wire(value.action), "attempt": wire(value.attempt), "receipt": wire(value.result), "result_revision": 2}
    assert attempts == [{"attempt": wire(value.attempt)}]
    if cancel_after_memory_write:
        assert activity == {"state": "dormant", "last_result_revision": 1, "last_receipt_id": None}
        assert goal == {"progress": 0, "state": "active"}
        assert not goal_proofs and not originals and not episodes and not aggregate
        assert not progress[CONSUMER]["proof"] and progress[CONSUMER]["disposition"] != "done"
        assert delivery["state"] == "pending" and budget["repair"]["remaining"] == 0
        assert not budget["repair"]["eligible"]
    else:
        assert activity == {"state": "completed", "last_result_revision": 2, "last_receipt_id": value.result.receipt_id}
        assert goal == {"progress": 1, "state": "completed"}
        assert len(goal_proofs) == len(originals) == len(episodes) == 1
        assert goal_proofs[0]["action_id"] == value.action.action_id and goal_proofs[0]["result_revision"] == 2
        assert originals == [{"revision": 2}]
        assert episodes[0]["receipt_id"] == value.result.receipt_id and episodes[0]["event_revision"] == 2
        assert episodes[0]["source_refs"] == goal_proofs[0]["source_refs"]
        assert progress[CONSUMER]["disposition"] == "done"
        assert progress[CONSUMER]["proof"] == {"activity_id": "reading", "last_result_revision": 2, "last_receipt_id": value.result.receipt_id}
        assert aggregate == {"action_id": value.action.action_id} and delivery["state"] == "completed"
        assert all(item["disposition"] in ledger.TERMINAL for item in progress.values()) and len(progress) == 6
        # Loss of an acknowledgement must reconcile the committed proof, never apply it again.
        reconciled = await value.recovery.recover_result(value.original_job_id, **value.expected,
            repair_ref=REPAIR_REF, repair_consumer=CONSUMER)
        assert reconciled["state"] == "verified" and memory_calls == 1
    value.actions.invoke.assert_not_awaited()
    value.actions.resume.assert_not_awaited()
    value.actions.run.assert_not_awaited()
    value.models.submit.assert_not_awaited()
    value.renderer.on_action_result.assert_not_awaited()
    value.conversation.on_result.assert_not_awaited()
