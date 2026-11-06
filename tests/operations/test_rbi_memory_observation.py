from importlib.resources import files

import pytest
import pytest_asyncio
from psycopg.types.json import Jsonb

from tianshu.operations.rbi_memory_observation import (current_holds_query, current_query, history_query, page_result,
    target_attempts_query, target_batch_episode_query, target_episode_query, target_query, target_result)
from tianshu.runtime.migrations import Migration, migrate


@pytest_asyncio.fixture
async def memory_probe_db(database):
    names = ("schema.sql", "0021_memory_consolidation.sql", "0022_memory_runtime.sql", "0023_memory_index_retry.sql",
             "0024_memory_consolidation_retry.sql", "0025_memory_write.sql")
    await migrate(database, tuple(Migration("rbi_probe_" + name,
        files("tianshu.memory").joinpath(name).read_text(encoding="utf-8")) for name in names))
    async with database.transaction() as conn:
        for index in range(1, 4):
            partition, batch, step = f"partition-{index}", f"batch-{index}", f"step-{index}"
            await conn.execute("INSERT INTO memory.consolidation_state(partition_id,actor_id,principal_id,scope_key,authority_bound) "
                               "VALUES(%s,'actor','owner','scope','{}')", (partition,))
            await conn.execute("INSERT INTO memory.consolidation_batches(batch_id,partition_id,schema_version,status,manifest,context_fingerprint) "
                               "VALUES(%s,%s,'1','quarantined',%s,'fingerprint')",
                               (batch, partition, Jsonb({"data_generation": "generation-1",
                                "original_observation_only": index == 1,
                                "episodes": [{"episode_id": "episode-1", "revision": 1, "sequence": 101}]
                                    if index == 1 else [],
                                "remote_wait_unresolved": {"reason": "source_lock_set_changed",
                                    "remote_rollback_confirmed": False}})))
            if index != 2:
                await conn.execute("UPDATE memory.consolidation_state SET active_batch_id=%s WHERE partition_id=%s",
                                   (batch, partition))
            await conn.execute("INSERT INTO memory.model_steps(step_id,request) VALUES(%s,'{}')", (step,))
            await conn.execute("INSERT INTO memory.consolidation_attempts(batch_id,number,step_id,model_attempt_id,kind,due_at,status) "
                               "VALUES(%s,1,%s,%s,'original',now(),'queued')", (batch, step, f"attempt-{index}"))
        for index, status in ((1, "unknown"), (2, "succeeded")):
            await conn.execute("INSERT INTO models.attempts(attempt_id,intent_id,principal_id,function,service_class,"
                "route_id,route_revision,provider_id,model,protocol,quota_group,request_digest,context_fingerprint,"
                "status,dispatch,deadline_at) VALUES(%s,'intent','owner','extract','elastic','route',1,'provider',"
                "'model','openai.chat','quota','digest','context',%s,'unknown',now())", (f"attempt-{index}", status))
        await conn.execute("INSERT INTO memory.episodes(episode_id,event_id,actor_id,principal_id,revision,sequence,"
            "origin_kind,occurred_at,summary,source_refs,scope_ids,content_hash,event_revision,event_content_hash,"
            "extraction_state,extraction_missing,consolidation_partition_id) "
            "VALUES('episode-1','event-1','actor','owner',1,101,'human',now(),'fixture','[]','{}','digest',1,'digest',"
            "'completed_with_missing',ARRAY['media_unavailable'],'partition-1')")
    return database


async def test_pages_bound_candidates_before_filter_and_keep_model_authority(memory_probe_db):
    async with memory_probe_db.transaction(read_only=True) as conn:
        sql, params = current_query(limit=2)
        first = page_result("current", await (await conn.execute(sql, params)).fetchall(), after="", limit=2)
        assert [row["partition_id"] for row in first["items"]] == ["partition-1"]
        assert first["next_cursor"] == "partition-2"
        sql, params = current_query(after=first["next_cursor"], limit=2)
        second = page_result("current", await (await conn.execute(sql, params)).fetchall(),
                             after=first["next_cursor"], limit=2)
        assert [row["partition_id"] for row in second["items"]] == ["partition-3"]
        assert second["coverage"]["overall_complete"] is None
        sql, params = current_holds_query(limit=2)
        holds = page_result("current_holds", await (await conn.execute(sql, params)).fetchall(), after="", limit=2)
        assert [row["batch_id"] for row in holds["items"]] == ["batch-1", "batch-2"]
        assert holds["items"][1]["wait_reason"] == "source_lock_set_changed"
        assert holds["items"][1]["remote_rollback_confirmed"] is False
        assert holds["next_cursor"] == "batch-2"  # Includes the hold without an active pointer.

        after = None
        pages = []
        for _ in range(3):
            sql, params = history_query(after=after, limit=1)
            page = page_result("history", await (await conn.execute(sql, params)).fetchall(), after=after, limit=1)
            pages.append(page)
            after = page["next_cursor"]
        assert pages[0]["items"][0]["model_status"] == "unknown"
        assert pages[0]["items"][0]["local_status"] == "queued"
        assert pages[1]["items"] == [] and pages[1]["next_cursor"] == {"batch_id": "batch-2", "number": 1}
        assert pages[2]["items"][0]["model_status"] is None  # No Models receipt is not proof of success.
        assert pages[2]["coverage"]["page_complete"] is True
        assert pages[2]["coverage"]["overall_complete"] is None


async def test_target_lookup_and_cancelled_general_query_keep_independent_coverage(memory_probe_db):
    cancelled = page_result("history", None, after={"batch_id": "batch-1", "number": 1},
                            error_type="QueryCanceled", sqlstate="57014")
    assert cancelled["coverage"]["status"] == "unknown"
    assert cancelled["coverage"]["overall_complete"] is None
    assert cancelled["coverage"]["sqlstate"] == "57014"
    assert cancelled["next_cursor"] == {"batch_id": "batch-1", "number": 1}
    async with memory_probe_db.transaction(read_only=True) as conn:
        sql, params = target_query("batch-1")
        target = target_result(await (await conn.execute(sql, params)).fetchone())
        sql, params = target_attempts_query("batch-1", limit=2)
        attempts = page_result("target_attempts", await (await conn.execute(sql, params)).fetchall(), limit=2)
        sql, params = target_episode_query("episode-1")
        episode = target_result(await (await conn.execute(sql, params)).fetchone())
        sql, params = target_batch_episode_query("batch-1", "episode-1", revision=1, sequence=101)
        membership = target_result(await (await conn.execute(sql, params)).fetchone())
        sql, params = target_batch_episode_query("batch-1", "episode-1", revision=2, sequence=101)
        different_version = target_result(await (await conn.execute(sql, params)).fetchone())
    assert target["coverage"]["status"] == "found" and target["coverage"]["overall_complete"] is None
    assert target["item"]["batch_id"] == "batch-1"
    assert attempts["items"][0]["model_status"] == "unknown"
    assert episode["item"]["extraction_state"] == "completed_with_missing"
    assert episode["item"]["extraction_missing"] == ["media_unavailable"]
    assert membership["item"]["exact_version_in_batch"] is True
    assert different_version["item"]["exact_version_in_batch"] is False
    assert cancelled["coverage"]["status"] == "unknown"


def test_probe_rejects_unbounded_limits_and_malformed_cursors():
    with pytest.raises(ValueError):
        history_query(limit=10000)
    with pytest.raises(ValueError):
        history_query(after={"batch_id": "batch-1", "number": -1})
