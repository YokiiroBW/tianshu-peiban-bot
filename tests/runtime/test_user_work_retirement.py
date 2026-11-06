"""A user can retire failed work without rewriting business or execution evidence."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from psycopg.types.json import Jsonb

from tianshu.companion.growth_retry import GrowthRetries
from tianshu.operations.diagnosis import Diagnosis
from tianshu.operations.failed_work_cleanup import execute_plan
from tianshu.operations.incidents import IncidentStore
from tianshu.operations.notifications import NotificationOutbox
from tianshu.runtime.diagnostics import RuntimeDiagnostics
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import migrate, product_migrations


GENERATION = "cfe6da10-98e8-4b50-bc3e-a5b60177f190"


@pytest_asyncio.fixture
async def cleanup_db(empty_database):
    await migrate(empty_database, product_migrations())
    # The pg_dump baseline clears the migration connection's session search path.
    async with empty_database.control_pool.connection() as conn:
        await conn.execute("SET search_path TO public")
    async with empty_database.transaction() as conn:
        await conn.execute("INSERT INTO runtime.installation_generation(singleton,id) VALUES(true,%s) "
                           "ON CONFLICT(singleton) DO UPDATE SET id=EXCLUDED.id", (GENERATION,))
    return empty_database


def plan(owner, target):
    return {"generation": GENERATION, "request_ref": "user-authorized-cleanup", "requested_by": "fixture-user",
            "targets": [{"owner": owner, "target": target}]}


async def apply(db, owner, target, *, commit=True):
    return (await execute_plan(db, plan(owner, target), apply=commit))["results"][0]


async def model_on(conn, status):
    await conn.execute("INSERT INTO models.attempts(attempt_id,intent_id,principal_id,function,service_class,"
        "route_id,route_revision,provider_id,model,protocol,quota_group,request_digest,context_fingerprint,"
        "status,dispatch,deadline_at,data_generation) VALUES('model','intent','owner','fixture','elastic',"
        "'route',1,'provider','fixture','openai.chat','quota','digest','context',%s,'submitted',now(),%s)",
        (status, GENERATION))


async def native_job(db):
    async with db.transaction() as conn:
        return await (await conn.execute("INSERT INTO procrastinate_jobs(task_name,queue_name,args,status,attempts) "
            "VALUES('fixture','bot-background','{\"source\":\"original\"}','failed',4) "
            "RETURNING id,attempts,md5(args::text) AS args_digest")).fetchone()


async def memory_batch(db, status=None):
    async with db.transaction() as conn:
        await conn.execute("INSERT INTO memory.consolidation_state(partition_id,actor_id,principal_id,scope_key,authority_bound) "
            "VALUES('partition','actor','owner','scope','{}')")
        target = await (await conn.execute("INSERT INTO memory.consolidation_batches(batch_id,partition_id,"
            "schema_version,status,manifest,context_fingerprint) VALUES('batch','partition','v1','failed',%s,'context') "
            "RETURNING batch_id,md5(manifest::text) AS manifest_digest,updated_at", (Jsonb({"data_generation": GENERATION}),))).fetchone()
        if status:
            if status != "missing":
                await model_on(conn, status)
            await conn.execute("INSERT INTO memory.model_steps(step_id,request) VALUES('step','{}')")
            await conn.execute("INSERT INTO memory.consolidation_attempts(batch_id,number,step_id,model_attempt_id,kind,due_at) "
                "VALUES('batch',1,'step','model','original',now())")
    return {**target, "updated_at": str(target["updated_at"])}


async def growth_batch(db, status="failed"):
    async with db.transaction() as conn:
        await model_on(conn, status)
        await conn.execute("INSERT INTO companion.organize_state(actor_id,scope_key,runtime_generation) VALUES('actor','scope',%s)", (GENERATION,))
        await conn.execute("INSERT INTO companion.growth_retry_batches(actor_id,scope_key,retry_key,batch,current_material_key,"
            "state,attempt_number,history) VALUES('actor','scope','retry',%s,'material','blocked',4,'[{\"attempt_id\":\"model\"}]')",
            (Jsonb({"runtime_generation": GENERATION}),))
    return {"actor_id": "actor", "scope_key": "scope", "retry_key": "retry", "revision": 1}


async def incident(db, family="old_failure"):
    store = IncidentStore(db, None, None)
    event = {"event_id": "first", "dependency": "fixture", "error_family": family, "version": "v1"}
    row = await store.ingest(event)
    return store, event, {k: str(row[k]) if k == "last_seen" else row[k]
                         for k in ("incident_id", "revision", "episode", "occurrences", "last_seen")}


async def test_preview_and_idempotent_retirement_keep_native_history(cleanup_db):
    target = await native_job(cleanup_db)
    assert (await apply(cleanup_db, "runtime", target, commit=False))["state"] == "eligible"
    diagnostics = RuntimeDiagnostics(cleanup_db, None, None)
    assert len((await diagnostics.tasks())["jobs"]) == 1
    assert (await apply(cleanup_db, "runtime", target))["state"] == "retired"
    assert (await apply(cleanup_db, "runtime", target))["state"] == "already_retired"
    assert (await diagnostics.tasks())["jobs"] == []
    original = (await diagnostics.tasks(job_id=target["id"]))["jobs"][0]
    assert original["status"] == "failed" and original["attempts"] == 4 and original["retired"]
    async with cleanup_db.transaction(read_only=True) as conn:
        assert (await (await conn.execute("SELECT args FROM procrastinate_jobs")).fetchone())["args"] == {"source": "original"}
        assert (await (await conn.execute("SELECT count(*) AS n FROM runtime.user_work_retirements")).fetchone())["n"] == 1


async def test_changed_or_proof_backed_job_is_preserved(cleanup_db):
    target = await native_job(cleanup_db)
    assert (await apply(cleanup_db, "runtime", {**target, "attempts": 3}))["reason"] == "original_changed"
    async with cleanup_db.transaction() as conn:
        await conn.execute("INSERT INTO runtime.rbi_execution_facts(data_generation,identity_digest,owner,max_failures,"
            "job_id,native_attempt,outcome) VALUES(%s,'identity','fixture',4,%s,4,'failed')", (GENERATION, target["id"]))
    assert (await apply(cleanup_db, "runtime", target))["reason"] == "execution_proof_retained"


async def test_memory_retirement_preserves_original_episode_and_batch(cleanup_db):
    target = await memory_batch(cleanup_db)
    async with cleanup_db.transaction() as conn:
        await conn.execute("INSERT INTO memory.episodes(episode_id,event_id,actor_id,principal_id,origin_kind,occurred_at,"
            "summary,source_refs,scope_ids,coverage,content_hash,extraction_state,event_revision,event_content_hash) "
            "VALUES('episode','event','actor','owner','actor',now(),'original experience','[]','{}','{}','hash','consolidation_failed',1,'event-hash')")
    assert (await apply(cleanup_db, "memory", target))["state"] == "retired"
    async with cleanup_db.transaction(read_only=True) as conn:
        row = await (await conn.execute("SELECT summary,active,extraction_state FROM memory.episodes")).fetchone()
        assert row == {"summary": "original experience", "active": True, "extraction_state": "consolidation_failed"}
        batch = await (await conn.execute("SELECT status,md5(manifest::text) AS digest FROM memory.consolidation_batches")).fetchone()
        assert batch == {"status": "failed", "digest": target["manifest_digest"]}


@pytest.mark.parametrize("model_status,reason", [("unknown", "original_model_unsettled"), ("missing", "original_model_unavailable")])
async def test_memory_unknown_or_missing_receipt_cannot_be_retired(cleanup_db, model_status, reason):
    target = await memory_batch(cleanup_db, model_status)
    assert (await apply(cleanup_db, "memory", target))["reason"] == reason


async def test_active_memory_batch_is_preserved(cleanup_db):
    target = await memory_batch(cleanup_db)
    async with cleanup_db.transaction() as conn:
        await conn.execute("UPDATE memory.consolidation_state SET active_batch_id='batch'")
    assert (await apply(cleanup_db, "memory", target))["reason"] == "original_batch_active"


async def test_memory_local_unknown_marker_is_not_treated_as_no_attempt(cleanup_db):
    target = await memory_batch(cleanup_db)
    async with cleanup_db.transaction() as conn:
        target = await (await conn.execute("UPDATE memory.consolidation_batches SET manifest=manifest||"
            "'{\"remote_wait_unresolved\":true}' RETURNING batch_id,md5(manifest::text) AS manifest_digest,updated_at")).fetchone()
    target["updated_at"] = str(target["updated_at"])
    assert (await apply(cleanup_db, "memory", target))["reason"] == "original_model_unsettled"


async def test_growth_retirement_does_not_complete_or_renew_attempts(cleanup_db):
    target = await growth_batch(cleanup_db)
    assert (await apply(cleanup_db, "growth", target))["state"] == "retired"
    growth = SimpleNamespace(companion=SimpleNamespace(database=cleanup_db), jobs=SimpleNamespace(assert_generation_on=AsyncMock()))
    retry = GrowthRetries(growth)
    async with cleanup_db.transaction() as conn:
        row = await (await conn.execute("SELECT * FROM companion.growth_retry_batches")).fetchone()
        assert row["state"] == "blocked" and row["attempt_number"] == 4
        assert row["history"] == [{"attempt_id": "model"}] and row["revision"] == 1
        with pytest.raises(AppError, match="work_retired_by_user"):
            await retry.resume_on(conn, {**target, "runtime_generation": GENERATION}, None, "retry")


async def test_growth_unknown_remains_observable(cleanup_db):
    target = await growth_batch(cleanup_db, "unknown")
    assert (await apply(cleanup_db, "growth", target))["reason"] == "original_model_unsettled"


@pytest.mark.parametrize("mutation,reason", [
    ("UPDATE companion.growth_retry_batches SET state='active'", "original_not_settled_failure"),
    ("UPDATE companion.growth_retry_batches SET waiting_on_original=true", "original_not_settled_failure"),
    ("UPDATE companion.growth_retry_batches SET batch=batch||'{\"rbi_review\":{\"state\":\"requested\"}}'", "review_requested"),
    ("UPDATE companion.organize_state SET active_batch='{\"retry_key\":\"retry\"}'", "original_batch_active"),
])
async def test_growth_current_work_is_not_retired(cleanup_db, mutation, reason):
    target = await growth_batch(cleanup_db)
    async with cleanup_db.transaction() as conn:
        await conn.execute(mutation)
    assert (await apply(cleanup_db, "growth", target))["reason"] == reason


async def test_archived_incident_is_historical_until_a_new_event(cleanup_db):
    store, event, target = await incident(cleanup_db)
    async with cleanup_db.transaction() as conn:
        await conn.execute("INSERT INTO operations.notification_outbox(notification_id,incident_id,kind,episode,incident_revision,"
            "channel,target,payload) VALUES('old-notice',%s,'opened',1,1,'qq','recipient','{}')", (target["incident_id"],))
    assert (await apply(cleanup_db, "operations", target))["state"] == "retired"
    assert (await store.list({}))["items"] == []
    historical = await store.get(target["incident_id"])
    assert historical["state"] == "open" and historical["archive"]["request_ref"] == "user-authorized-cleanup"
    assert len((await store.list({"include_archived": True}))["items"]) == 1
    diagnosis = Diagnosis(cleanup_db, None, None, None, None)
    assert not await diagnosis.start(historical)
    outbox = NotificationOutbox(cleanup_db, None)
    await outbox.enqueue(historical, "opened")
    await outbox.deliver_due()
    async with cleanup_db.transaction(read_only=True) as conn:
        notice = await (await conn.execute("SELECT status,attempts FROM operations.notification_outbox")).fetchone()
        assert notice == {"status": "pending", "attempts": 0}
    assert (await store.ingest(event))["episode"] == 1  # Duplicate event is never a new failure.
    old = await store.ingest({**event, "event_id": "delayed-old", "timestamp": (datetime.now(UTC)-timedelta(days=1)).isoformat()})
    assert old["episode"] == 1 and old["occurrences"] == 1
    new = await store.ingest({**event, "event_id": "new", "timestamp": datetime.now(UTC).isoformat()})
    assert new["episode"] == 2 and new["state"] == "open" and new["diagnosis_count"] == 0
    assert new["diagnostic_state"] == "pending"
    assert len((await store.list({}))["items"]) == 1


async def test_incident_with_inflight_or_unknown_work_is_preserved(cleanup_db):
    _, _, target = await incident(cleanup_db)
    async with cleanup_db.transaction() as conn:
        await conn.execute("INSERT INTO operations.diagnostics(attempt_id,incident_id,incident_revision,settings_revision,route_id,status) "
            "VALUES('diag',%s,1,1,'route','submitting')", (target["incident_id"],))
    assert (await apply(cleanup_db, "operations", target))["reason"] == "incident_work_active"


@pytest.mark.parametrize("family", ["unknown", "outcome_unknown", "timeout_unknown"])
async def test_unknown_incident_is_retained(cleanup_db, family):
    _, _, target = await incident(cleanup_db, family)
    assert (await apply(cleanup_db, "operations", target))["reason"] == "current_or_unknown_problem_retained"


async def test_cleanup_cannot_cross_generation(cleanup_db):
    target = await native_job(cleanup_db)
    body = {**plan("runtime", target), "generation": "old-generation"}
    with pytest.raises(AppError, match="job_generation_expired"):
        await execute_plan(cleanup_db, body, apply=True)
    await apply(cleanup_db, "runtime", target)
    async with cleanup_db.transaction() as conn:
        await conn.execute("UPDATE runtime.installation_generation SET id='new-generation'")
    assert len((await RuntimeDiagnostics(cleanup_db, None, None).tasks())["jobs"]) == 1


async def test_entire_manifest_shape_is_checked_before_any_write(cleanup_db):
    target = await native_job(cleanup_db)
    body = plan("runtime", target)
    body["targets"].append({"owner": "memory", "target": {"batch_id": "missing-cas"}})
    with pytest.raises(AppError, match="invalid_cleanup_manifest"):
        await execute_plan(cleanup_db, body, apply=True)
    assert len((await RuntimeDiagnostics(cleanup_db, None, None).tasks())["jobs"]) == 1
