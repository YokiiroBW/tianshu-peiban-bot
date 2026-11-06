"""Source withdrawal redacts original and recovery result jobs by exact dependency."""
from datetime import UTC, datetime
from importlib.resources import files
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.conversation.dependencies import ConversationDependencies
from tianshu.conversation.results import ActionResultEvents, ResultConsumer
from tianshu.conversation.rbg_result_recovery import ResultRecovery
from tianshu.runtime.jobs import JobQueue, JobSpec
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind, Receipt, ServiceClass, SourceRef, TrustedContext


@pytest.mark.asyncio
async def test_fragment_withdrawal_scrubs_each_native_copy_and_blocks_recovery(database):
    await migrate(database, (
        Migration("rbi_execution_budget", files("tianshu.runtime").joinpath("rbi_execution_budget.sql").read_text()),
        Migration("0032_conversation", files("tianshu.conversation").joinpath("schema.sql").read_text()),
        Migration("rbg_results", files("tianshu.conversation").joinpath("rbg_result_consumption.sql").read_text()),
        Migration("rbi_result_reconcile", files("tianshu.conversation").joinpath("rbi_result_reconcile.sql").read_text()),
    ))
    jobs = JobQueue(database.config.dsn, database=database)
    jobs.register_static_routes()
    context = TrustedContext("principal", "actor", None, "chat", ("scope",), ("scope",), 1, OriginKind.ACTOR)
    selected = SourceRef("page", 3, {"unit": "character", "start": 0, "end": 20})
    untouched = SourceRef("page", 3, {"unit": "character", "start": 30, "end": 40})
    removed = SourceRef("page", 3, {"unit": "character", "start": 5, "end": 10})
    now = datetime.now(UTC)

    def event(action_id, receipt_id, ref, text):
        actual = Receipt(receipt_id, action_id, "attempt", "succeeded", "confirmed", "not_requested",
                         result={"consumption": {"source": wire(ref)}, "body": text})
        return {"type": "capability.result", "version": 1, "event_id": receipt_id, "action_id": action_id,
                "result_revision": 1, "result_observed_at": now.isoformat(), "context": wire(context),
                "source_refs": [], "receipt": wire(actual)}

    sensitive = event("action", "receipt", selected, "private withdrawn text")
    other = event("other-action", "other-receipt", untouched, "separate fragment")
    orphan = event("legacy-action", "legacy-receipt", selected, "legacy private text")
    collision = {**sensitive, "result_revision": 2, "receipt": {**sensitive["receipt"],
        "result": {**sensitive["receipt"]["result"], "body": "collided private text"}}}
    async with database.transaction() as connection:
        await ActionResultEvents(jobs).publish(connection, sensitive)
        copy_id = await jobs.enqueue(connection, JobSpec("conversation.result", "result-copy:receipt",
            {"event": sensitive}, ServiceClass.INTERACTIVE, subject_id="actor"))
        await ActionResultEvents(jobs).publish(connection, other)
        orphan_id = await jobs.enqueue(connection, JobSpec("conversation.result", "legacy-unbound",
            {"event": orphan}, ServiceClass.INTERACTIVE, subject_id="actor"))
        await connection.execute("DELETE FROM runtime.job_keys WHERE job_id=%s", (orphan_id,))
        collision_id = await jobs.enqueue(connection, JobSpec("conversation.result", "legacy-collision",
            {"event": collision}, ServiceClass.INTERACTIVE, subject_id="actor"))
    dependants = ConversationDependencies(jobs)
    async with database.transaction() as connection:
        assert await dependants.actors_for_source(connection, (removed,), "fragment_forgotten") == ("actor",)
        await dependants.invalidate_actor(connection, (removed,), "fragment_forgotten", actor_id="actor")
    async with database.transaction(read_only=True) as connection:
        rows = await (await connection.execute("SELECT j.id,j.args FROM procrastinate_jobs j WHERE j.task_name='conversation.result' ORDER BY j.id")).fetchall()
        delivery = await (await connection.execute("SELECT state FROM conversation.result_deliveries WHERE receipt_id='receipt'")).fetchone()
        other_delivery = await (await connection.execute("SELECT state FROM conversation.result_deliveries WHERE receipt_id='other-receipt'")).fetchone()
    copies = [row for row in rows if row["args"]["event"]["event_id"] == "receipt" and row["id"] != collision_id]
    assert len(copies) == 2 and any(row["id"] == copy_id for row in copies)
    for row in copies:
        saved = row["args"]["event"]
        assert saved["source_invalidated"] is True
        assert saved["receipt"]["receipt_id"] == "receipt" and saved["receipt"]["attempt_id"] == "attempt"
        assert saved["receipt"]["execution"] == "succeeded" and saved["receipt"]["effect"] == "confirmed"
        assert "private withdrawn text" not in json.dumps(saved, ensure_ascii=False)
    assert delivery["state"] == "invalidated" and other_delivery["state"] == "pending"
    orphan_saved = next(row["args"]["event"] for row in rows if row["id"] == orphan_id)
    assert orphan_saved["source_invalidated"] and "legacy private text" not in json.dumps(orphan_saved)
    collision_saved = next(row["args"]["event"] for row in rows if row["id"] == collision_id)
    assert collision_saved["source_invalidated"] and collision_saved["result_revision"] == 2
    assert "collided private text" not in json.dumps(collision_saved)
    unaffected = next(row["args"]["event"] for row in rows if row["args"]["event"]["event_id"] == "other-receipt")
    assert unaffected["receipt"]["result"]["body"] == "separate fragment"
    identity = SimpleNamespace(check=AsyncMock(), sources=SimpleNamespace(validate_persistence=AsyncMock()))
    actions = SimpleNamespace(get=AsyncMock(), invoke=AsyncMock())
    consumer = ResultConsumer(database, identity, actions, None, None, SimpleNamespace(jobs=jobs), None, None)
    await consumer.consume(copies[0]["args"]["event"])
    identity.check.assert_not_awaited()
    actions.get.assert_not_awaited()
    actions.invoke.assert_not_awaited()
    recovery = ResultRecovery(database, jobs, identity)
    assert (await recovery.inspect(None, {"id": str(copies[0]["id"])}))["reason"] == "source_withdrawn"
