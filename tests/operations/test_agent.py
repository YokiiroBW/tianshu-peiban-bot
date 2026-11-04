from types import SimpleNamespace
from unittest.mock import AsyncMock

from tianshu.operations.agent import OperationsAgent


async def test_model_reload_pages_resume_after_failure_and_restart_a_sweep(operations_db):
    cursor = {"after": {"created_at": "2026-11-05T00:00:00+00:00", "event_id": "model-config:2"},
              "through": {"created_at": "2026-11-05T00:01:00+00:00", "event_id": "model-config:3"}}
    first = {"event_id": "model-config:1", "revision": 1}
    second = {"event_id": "model-config:2", "revision": 1}
    third = {"event_id": "model-config:3", "revision": 2}
    models = SimpleNamespace(pending_configuration_reloads=AsyncMock(side_effect=[
        {"items": [first, second], "next_cursor": cursor},
        {"items": [third], "next_cursor": None},
        {"items": [first], "next_cursor": None},
    ]))
    calls = []
    async def ingest(event):
        calls.append(event["material_id"])
        if len(calls) == 1:
            raise RuntimeError("temporary incident failure")
        return {"incident_id": event["material_id"]}
    incidents = SimpleNamespace(ingest=AsyncMock(side_effect=ingest))
    outbox = SimpleNamespace(enqueue=AsyncMock())
    diagnosis = SimpleNamespace(models=models)
    agent = OperationsAgent(operations_db, None, incidents, None, diagnosis, outbox)
    await agent.poll_model_reloads()
    # A new agent instance must continue the stored cursor, not rescan page one.
    agent = OperationsAgent(operations_db, None, incidents, None, diagnosis, outbox)
    await agent.poll_model_reloads()
    await agent.poll_model_reloads()
    assert calls == ["model-config:1", "model-config:2", "model-config:3", "model-config:1"]
    assert models.pending_configuration_reloads.await_args_list[0].kwargs == {"limit": 100, "cursor": None}
    assert models.pending_configuration_reloads.await_args_list[1].kwargs == {"limit": 100, "cursor": cursor}
    assert models.pending_configuration_reloads.await_args_list[2].kwargs == {"limit": 100, "cursor": None}
    assert outbox.enqueue.await_count == 3
