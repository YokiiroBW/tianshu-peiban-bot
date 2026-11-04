"""Durable acceptance, original-attempt recovery and transactional completion wakes."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from uuid import uuid4

import pytest
import pytest_asyncio

from tianshu.models.async_jobs import completion_record, enqueue_completion, pending_completions
from tianshu.models.catalog import RoutePlan
from tianshu.models.configuration_reload import event_id, mark_applied, pending, record_pending_on, status
from tianshu.models.ledger import AttemptLedger, receipt
from tianshu.models.types import ModelMessage, ModelPart, ModelRequest, ModelUsage
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.types import OriginKind, RequestBudget, ServiceClass, TrustedContext


@pytest_asyncio.fixture
async def rb_database(database):
    async with database.transaction() as conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS runtime.installation_generation "
            "(singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),id text NOT NULL,"
            "created_at timestamptz NOT NULL DEFAULT now())")
        await conn.execute("INSERT INTO runtime.installation_generation(singleton,id) VALUES(true,%s) "
                           "ON CONFLICT(singleton) DO NOTHING", (str(uuid4()),))
        found = await (await conn.execute("SELECT to_regclass('models.submissions') AS table_name")).fetchone()
        if found["table_name"] is None:
            await conn.execute(files("tianshu.models").joinpath("async_execution.sql").read_text())
        column = await (await conn.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='models' "
            "AND table_name='attempts' AND column_name='data_generation'")).fetchone()
        if column is None:
            await conn.execute(files("tianshu.models").joinpath("generation_fence.sql").read_text())
    return database


def request(identity):
    return ModelRequest("work:" + identity, identity, "reflect", ServiceClass.ELASTIC,
        TrustedContext("fixture", "actor", None, None, (), (), 1, OriginKind.ACTOR),
        RequestBudget(datetime.now(UTC) + timedelta(minutes=2)),
        (ModelMessage("user", (ModelPart("text", text="synthetic"),)),),
        execution_timeout_seconds=30)


def plan():
    return RoutePlan("route", 1, "provider", "model", "openai.chat", "http://localhost",
                     "KEY", "account", frozenset({"text"}), {})


async def test_acceptance_identity_late_subscription_and_transactional_outbox(rb_database):
    ledger = AttemptLedger(rb_database, "test")
    original = request("same")
    async with rb_database.transaction(read_only=True) as conn:
        generation = (await (await conn.execute("SELECT id FROM runtime.installation_generation WHERE singleton")).fetchone())["id"]
    wake = JobSpec("memory.index_retry", "model-result:same", {"index_id": "fixture"},
                   ServiceClass.ELASTIC, subject_id="actor", data_generation=str(generation))
    row, created = await ledger.create(original, plan(), {"tokens": 3}, frozen=original)
    assert created and row["status"] == "queued"
    await ledger.finish("same", "failed", error_code="synthetic_failure")
    existing = await ledger.existing_submission(original, completion_record(wake))
    assert existing[0]["status"] == "failed"
    with pytest.raises(AppError, match="attempt_identity_conflict"):
        await ledger.existing_submission(replace(original, system="different"))
    with pytest.raises(AppError, match="model_completion_identity_conflict"):
        await ledger.existing_submission(original, completion_record(replace(wake, key="different")))

    class Queue:
        fail = True

        async def enqueue(self, connection, spec):
            await connection.execute("INSERT INTO runtime.job_keys(key,task) VALUES(%s,%s)",
                                     (spec.key, spec.task))
            if self.fail:
                raise RuntimeError("synthetic crash before commit")

    queue = Queue()
    with pytest.raises(RuntimeError):
        await enqueue_completion(rb_database, queue, "same")
    async with rb_database.transaction(read_only=True) as conn:
        assert await (await conn.execute("SELECT key FROM runtime.job_keys WHERE key=%s", (wake.key,))).fetchone() is None
        saved = await (await conn.execute("SELECT completion_enqueued_at FROM models.submissions WHERE attempt_id='same'")).fetchone()
        assert saved["completion_enqueued_at"] is None
    queue.fail = False
    assert await enqueue_completion(rb_database, queue, "same")
    assert not await enqueue_completion(rb_database, queue, "same")


async def test_restart_resumes_unsubmitted_attempt_and_retains_unknown_original(rb_database):
    ledger = AttemptLedger(rb_database, "old")
    queued, started = request("queued"), request("started")
    await ledger.create(queued, plan(), {}, frozen=queued)
    await ledger.create(started, plan(), {}, frozen=started)
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    await ledger.started("started", 300, deadline_at=deadline)
    await ledger.recover()
    pending = await ledger.pending_submissions()
    assert [item["request"]["attempt_id"] for item in pending] == ["queued"]
    original = await ledger.get("started")
    assert original["status"] == original["dispatch"] == "unknown"
    assert original["deadline_at"] == deadline
    assert (await ledger.existing_submission(started))[0]["status"] == "unknown"


async def test_late_usage_updates_only_original_receipt_and_is_idempotent(rb_database):
    ledger = AttemptLedger(rb_database, "old")
    original = request("late")
    await ledger.create(original, plan(), {"tokens": 10}, frozen=original)
    await ledger.started("late", None, deadline_at=datetime.now(UTC) + timedelta(seconds=30))
    await ledger.checkpoint("late", provider_handle="provider-handle")
    await ledger.finish("late", "unknown", error_code="transport_unknown")

    async def valid(_connection):
        return True

    partial = ModelUsage(input_tokens=12, metering="reported")
    first = await ledger.observe_usage("late", "provider-handle", partial, validate_observation=valid)
    same = await ledger.observe_usage("late", "provider-handle", partial, validate_observation=valid)
    assert first["revision"] == same["revision"]
    second = await ledger.observe_usage("late", "provider-handle",
        ModelUsage(output_tokens=4, metering="reported"), validate_observation=valid)
    assert second["status"] == second["dispatch"] == "unknown"
    assert second["usage"]["input_tokens"] == 12 and second["usage"]["output_tokens"] == 4
    assert second["charged_tokens"] == 16 and second["revision"] > first["revision"]
    with pytest.raises(AppError, match="model_usage_handle_mismatch"):
        await ledger.observe_usage("late", "different", partial, validate_observation=valid)


async def test_statistics_follow_attempt_revision_and_failed_projection_is_rebuilt(rb_database):
    class Statistics:
        failing = False

        def __init__(self):
            self.events, self.coverage = [], []

        async def record_safely_on(self, _conn, event):
            if self.failing:
                return False
            self.events.append(event)
            return True

        async def mark_producer_on(self, _conn, producer, *, watermark, complete, detail):
            self.coverage.append((producer, watermark, complete, detail))

    statistics = Statistics()
    ledger = AttemptLedger(rb_database, "stats", statistics=statistics)
    original = request("metered")
    await ledger.create(original, plan(), {"tokens": 30}, frozen=original, input_estimate=20)
    assert statistics.events[-1]["metrics"]["physical_attempts"] == 0
    statistics.failing = True
    await ledger.started("metered", None, deadline_at=datetime.now(UTC) + timedelta(seconds=30))
    assert (await ledger.get("metered"))["status"] == "running"
    assert statistics.coverage[-1][2] is False
    statistics.failing = False
    await ledger.checkpoint("metered", provider_handle="provider-handle", output_observed=False)
    await ledger.finish("metered", "unknown", error_code="transport_unknown")

    async def valid(_connection):
        return True

    await ledger.observe_usage("metered", "provider-handle",
        ModelUsage(input_tokens=12, output_tokens=4, metering="reported"), validate_observation=valid)
    latest = statistics.events[-1]
    assert latest["event_id"] == "metered" and latest["dimensions"]["state"] == "unknown"
    assert latest["metrics"]["physical_attempts"] == 1
    assert latest["metrics"]["input_tokens"] == 12 and latest["metrics"]["output_tokens"] == 4
    assert latest["metrics"]["estimated_input_tokens"] == 20
    assert latest["metrics"]["business_success"] is None
    assert latest["revision"] > statistics.events[0]["revision"]
    assert (await ledger.backfill_statistics())["complete"] is True
    assert statistics.coverage[-1][0:3] == ("models", "metered", True)


async def test_configuration_reload_fact_shares_command_transaction_and_revision(rb_database):
    context = request("config").context
    identity = event_id(context, "models.routes", "request-1")
    with pytest.raises(RuntimeError):
        async with rb_database.transaction() as conn:
            await record_pending_on(conn, context, "models.routes", "request-1", 4)
            raise RuntimeError("rollback")
    assert await status(rb_database, identity) is None
    async with rb_database.transaction() as conn:
        await record_pending_on(conn, context, "models.routes", "request-1", 4)
    assert (await status(rb_database, identity))["error_code"] == "broker_reload_pending"
    assert [item["event_id"] for item in await pending(rb_database)] == [identity]
    with pytest.raises(AppError, match="model_configuration_reload_revision_conflict"):
        await mark_applied(rb_database, identity, 3)
    assert (await mark_applied(rb_database, identity, 4))["state"] == "applied"
    assert await pending(rb_database) == []


async def test_completion_and_explicit_request_generation_are_checked_on_acceptance(rb_database):
    ledger = AttemptLedger(rb_database, "test")
    async with rb_database.transaction(read_only=True) as conn:
        current = (await (await conn.execute(
            "SELECT id FROM runtime.installation_generation WHERE singleton")).fetchone())["id"]
    previous = str(uuid4())
    original = replace(request("stale"), data_generation=previous)
    wake = JobSpec("memory.index_retry", "wake:stale", {}, data_generation=previous)
    with pytest.raises(AppError, match="data_generation_expired"):
        await ledger.create(original, plan(), {}, frozen=original,
                            completion=completion_record(wake), data_generation=current)
    assert await ledger.get("stale") is None

    original = replace(request("mismatch"), data_generation=current)
    wake = replace(wake, key="wake:mismatch")
    with pytest.raises(AppError, match="data_generation_expired"):
        await ledger.create(original, plan(), {}, frozen=original,
                            completion=completion_record(wake), data_generation=current)
    assert await ledger.get("mismatch") is None
    with pytest.raises(AppError, match="model_data_generation_required"):
        completion_record(replace(wake, data_generation=None))


async def test_generation_cutover_expires_outbox_and_does_not_dispatch_queued_work(rb_database):
    ledger = AttemptLedger(rb_database, "test")
    async with rb_database.transaction(read_only=True) as conn:
        current = (await (await conn.execute(
            "SELECT id FROM runtime.installation_generation WHERE singleton")).fetchone())["id"]
    outgoing = replace(request("wake"), data_generation=current)
    pending = replace(request("pending"), data_generation=current)
    wake = JobSpec("memory.index_retry", "wake:result", {}, data_generation=current)
    await ledger.create(outgoing, plan(), {}, frozen=outgoing, completion=completion_record(wake),
                        data_generation=current)
    await ledger.create(pending, plan(), {}, frozen=pending, data_generation=current)
    await ledger.finish("wake", "unknown", error_code="transport_unknown")
    async with rb_database.transaction() as conn:
        await conn.execute("UPDATE runtime.installation_generation SET id=%s WHERE singleton", (str(uuid4()),))

    class Queue:
        async def enqueue(self, _connection, _spec):
            raise AssertionError("stale completion reached queue")

    assert await enqueue_completion(rb_database, Queue(), "wake") is False
    assert "wake" not in await pending_completions(rb_database)
    result = receipt(await ledger.get("wake"))
    assert result.status == result.dispatch == "unknown"
    assert result.completion_state == "expired"
    assert result.completion_error_code == "data_generation_expired"
    with pytest.raises(AppError, match="data_generation_expired"):
        await ledger.started("pending", None, deadline_at=datetime.now(UTC) + timedelta(seconds=30))
    assert (await ledger.get("pending"))["dispatch"] == "not_started"
    await ledger.recover()
    expired = await ledger.get("pending")
    assert expired["status"] == "cancelled"
    assert expired["error_code"] == "data_generation_expired"
