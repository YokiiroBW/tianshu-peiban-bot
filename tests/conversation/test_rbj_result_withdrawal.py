"""Withdrawal redacts each original wake without resolving old unknown effects."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from psycopg.types.json import Jsonb

from test_rbj_result_repair import (CONSUMER, REPAIR_REF, exhaust, register_and_authorize,
                                    run_native, schedule_repair, setup)
from tianshu.conversation.codec import receipt as decode_receipt
from tianshu.conversation.dependencies import ConversationDependencies
from tianshu.runtime.recovery_policy import RecoveryWait
from tianshu.runtime.serialization import wire
from tianshu.types import SourceRef


def with_source(value, ref, text):
    event = deepcopy(value.event)
    event["source_refs"] = [wire(ref)]
    event["receipt"]["result"]["body"] = text
    value.event = event
    value.actions.current = decode_receipt(event["receipt"])
    return event


@pytest.mark.asyncio
async def test_withdrawal_redacts_normal_and_repair_jobs_but_keeps_old_outcome_unknown(database):
    value = await setup(database)
    selected = SourceRef("page", 3, {"unit": "character", "start": 0, "end": 20})
    removed = SourceRef("page", 3, {"unit": "character", "start": 5, "end": 10})
    with_source(value, selected, "private-original-body")
    saved = await exhaust(database, value)
    assert (await register_and_authorize(database, value, saved))["state"] == "accepted"
    repair = await schedule_repair(value, saved)
    async with database.transaction() as connection:
        await ConversationDependencies(value.jobs).invalidate_actor(connection, (removed,), "fragment_forgotten", actor_id="actor")
    async with database.transaction(read_only=True) as connection:
        jobs = await (await connection.execute("SELECT id,args FROM procrastinate_jobs "
            "WHERE task_name='conversation.result' ORDER BY id")).fetchall()
        delivery = await (await connection.execute("SELECT state FROM conversation.result_deliveries "
            "WHERE data_generation=%s AND receipt_id='receipt'", (saved["data_generation"],))).fetchone()
        progress = await (await connection.execute("SELECT consumer,disposition FROM conversation.result_consumer_progress "
            "WHERE data_generation=%s AND receipt_id='receipt'", (saved["data_generation"],))).fetchall()
        aggregate = await (await connection.execute("SELECT 1 FROM conversation.result_consumption WHERE receipt_id='receipt'")).fetchone()
    assert any(row["id"] == repair["job_id"] for row in jobs)
    for row in jobs:
        event = row["args"]["event"]
        assert event["source_invalidated"] is True and event["event_id"] == "receipt"
        assert event["receipt"]["attempt_id"] == "attempt"
        assert "private-original-body" not in str(row["args"])
    assert delivery["state"] == "invalidated" and aggregate is None
    assert len(progress) == 6 and all(row["disposition"] == "blocked" for row in progress)
    facts = await value.recovery.inspect(None, {"id": str(saved["original_job_id"]),
        "repair_ref": REPAIR_REF, "repair_consumer": CONSUMER})
    assert facts["eligible"] is False and facts["outcome"] == "unknown"
    value.actions.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_with_old_snapshot_yields_persisted_redaction_without_apply(database):
    value = await setup(database)
    selected = SourceRef("page", 3, {"unit": "character", "start": 0, "end": 20})
    removed = SourceRef("page", 3, {"unit": "character", "start": 5, "end": 10})
    with_source(value, selected, "old-body")
    saved = await exhaust(database, value)
    assert (await register_and_authorize(database, value, saved))["state"] == "accepted"
    repair = await schedule_repair(value, saved)
    async with database.transaction() as connection:
        await ConversationDependencies(value.jobs).invalidate_actor(connection, (removed,), "fragment_forgotten", actor_id="actor")
    # The handler captured the original event before native args were redacted.
    result = await run_native(database, value, saved, repair["job_id"])
    assert result["state"] == "invalidated" and result["reason"] == "source_invalidated"
    assert value.life.activity_calls == 0
    value.actions.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_redaction_keeps_a_previously_committed_consumer_proof(database):
    value = await setup(database)
    selected = SourceRef("page", 3, {"unit": "character", "start": 0, "end": 20})
    removed = SourceRef("page", 3, {"unit": "character", "start": 5, "end": 10})
    with_source(value, selected, "original-body")
    from test_rbi_result_consumption import publish
    saved = await publish(value, database)
    proof = {"activity_id": "activity", "last_result_revision": 1, "last_receipt_id": "receipt"}
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.activities SET state='completed',last_result_revision=1,"
                                 "last_receipt_id='receipt' WHERE id='activity'")
        await connection.execute("INSERT INTO conversation.result_consumer_progress(data_generation,receipt_id,"
            "consumer,action_id,result_revision,disposition,proof) VALUES (%s,'receipt','life_activity','action',1,'done',%s)",
            (saved["data_generation"], Jsonb(proof)))
    async with database.transaction() as connection:
        await ConversationDependencies(value.jobs).invalidate_actor(connection, (removed,), "fragment_forgotten", actor_id="actor")
    facts = await value.recovery.inspect(None, {"id": str(saved["original_job_id"])})
    assert facts["outcome"] == "unknown"
    assert facts["consumer_progress"]["life_activity"]["disposition"] == "done"
    assert facts["consumer_progress"]["life_activity"]["proof"] == proof


@pytest.mark.asyncio
async def test_cas_change_rechecks_fragment_before_erasing_new_payload(database):
    value = await setup(database)
    selected = SourceRef("page", 3, {"unit": "character", "start": 0, "end": 20})
    removed = SourceRef("page", 3, {"unit": "character", "start": 5, "end": 10})
    separate = SourceRef("page", 3, {"unit": "character", "start": 30, "end": 40})
    with_source(value, selected, "old-body")
    from test_rbi_result_consumption import publish
    saved = await publish(value, database)
    replacement = deepcopy(value.event)
    replacement["source_refs"] = [wire(separate)]
    replacement["receipt"]["result"]["body"] = "separate-body"

    class RacingJobs:
        async def redact_wake_on(self, connection, job_id, **arguments):
            async with database.transaction() as concurrent:
                await concurrent.execute("UPDATE procrastinate_jobs SET args=%s WHERE id=%s",
                                         (Jsonb({"event": replacement}), job_id))
            return {"state": "changed", "reason": "wake_payload_changed", "job_id": job_id}

    async with database.transaction() as connection:
        await ConversationDependencies(RacingJobs()).invalidate_actor(
            connection, (removed,), "fragment_forgotten", actor_id="actor")
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT args FROM procrastinate_jobs WHERE id=%s",
                                              (saved["original_job_id"],))).fetchone()
    assert row["args"]["event"]["receipt"]["result"]["body"] == "separate-body"
    assert not row["args"]["event"].get("source_invalidated")


@pytest.mark.asyncio
async def test_real_redaction_lock_wait_retries_the_original_notification(database):
    value = await setup(database)
    selected = SourceRef("page", 3, {"unit": "character", "start": 0, "end": 20})
    removed = SourceRef("page", 3, {"unit": "character", "start": 5, "end": 10})
    with_source(value, selected, "still-pending")
    from test_rbi_result_consumption import publish
    saved = await publish(value, database)
    retry_at = datetime.now(UTC) + timedelta(seconds=5)

    class BusyJobs:
        async def redact_wake_on(self, connection, job_id, **arguments):
            return {"state": "waiting", "reason": "wake_lock_busy", "retry_at": retry_at}

    with pytest.raises(RecoveryWait, match="source_redaction_lock_busy") as failure:
        async with database.transaction() as connection:
            await ConversationDependencies(BusyJobs()).invalidate_actor(
                connection, (removed,), "fragment_forgotten", actor_id="actor")
    assert failure.value.retry_at == retry_at
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT args FROM procrastinate_jobs WHERE id=%s",
                                              (saved["original_job_id"],))).fetchone()
    assert row["args"]["event"]["receipt"]["result"]["body"] == "still-pending"
