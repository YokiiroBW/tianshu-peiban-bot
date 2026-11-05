"""Authored generation waits preserve their original identity and due time."""
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from test_rbf_planning_recovery import make_life
from tianshu.conversation.codec import document
from tianshu.life.generation_control import defer_generation, observe_original
from tianshu.runtime.recovery_policy import RecoveryWait, recovery_wait_from_wire


@pytest.mark.asyncio
async def test_lookup_phase_uses_only_positive_local_admission_evidence(database):
    value = await make_life(database)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.generation_recipes(generation_id,kind,item_id,context,recipe,source_refs,model_submission_confirmed) "
            "VALUES ('new','chapter','c',%s,'{}','[]',false),('legacy','chapter','c',%s,'{}','[]',NULL)",
            (document(value.context),document(value.context)))
    models = type("Models",(),{})()
    models.status = AsyncMock(return_value=None)
    assert await observe_original(value.worker.life,models,"new",value.data_generation) == (None,True)
    assert await observe_original(value.worker.life,models,"legacy",value.data_generation) == (None,False)
    assert models.status.await_args_list[0].kwargs == {"phase":"pre_submit"}
    assert models.status.await_args_list[1].kwargs == {}


@pytest.mark.asyncio
async def test_typed_wait_keeps_chapter_attempt_and_schedules_exact_retry(database):
    value = await make_life(database)
    life = value.worker.life
    async def generate(**kwargs):
        pass
    value.jobs.register("life.chapter_generate",generate)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.works(id,actor_id,title,scope_ids) VALUES ('work','actor','work',ARRAY['actor-scope'])")
        await connection.execute("INSERT INTO life.chapters(id,work_id,position,title,state,generation_id) "
            "VALUES ('chapter','work',1,'chapter','generating','original-attempt')")
    retry_at = datetime.now(UTC)+timedelta(minutes=3)
    wait = RecoveryWait("recovery_dependency_cooling",retry_at,dependency="models:quota:test")
    result = await defer_generation(life,"chapter","chapter","original-attempt","actor",value.data_generation,wait)
    assert result["attempt_id"] == "original-attempt" and result["retry_due_at"] == retry_at
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT generation_id,state,reason,generation_retry_due_at FROM life.chapters WHERE id='chapter'")).fetchone()
        queued = await (await connection.execute("SELECT args,scheduled_at FROM procrastinate_jobs WHERE task_name='life.chapter_generate' AND status='todo'")).fetchall()
    assert row == {"generation_id":"original-attempt","state":"generating","reason":wait.reason,"generation_retry_due_at":retry_at}
    assert len(queued) == 1 and queued[0]["args"]["generation_id"] == "original-attempt" and queued[0]["scheduled_at"] == retry_at
    lost = RecoveryWait("recovery_claim_lost",retry_at)
    with pytest.raises(RecoveryWait,match="recovery_claim_lost"):
        await defer_generation(life,"chapter","chapter","original-attempt","actor",value.data_generation,lost)
    unsafe = recovery_wait_from_wire({"kind":"recovery_wait","version":1,"reason":"source_lock_set_changed",
        "retry_at":(retry_at+timedelta(minutes=1)).isoformat(),"dependency":"sources:lock_set","rollback_confirmed":False})
    with pytest.raises(RecoveryWait):
        await defer_generation(life,"chapter","chapter","original-attempt","actor",value.data_generation,unsafe)
    async with database.transaction(read_only=True) as connection:
        unchanged = await (await connection.execute("SELECT generation_id,state,reason,generation_retry_due_at FROM life.chapters WHERE id='chapter'")).fetchone()
    assert unchanged == row
