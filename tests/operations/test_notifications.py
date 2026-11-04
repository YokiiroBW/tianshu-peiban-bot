from types import SimpleNamespace
from unittest.mock import AsyncMock

from psycopg.types.json import Jsonb

from tianshu.operations.incidents import IncidentStore, default_settings
from tianshu.operations.notifications import NotificationOutbox
from tianshu.operations.qq_notification import QQOperationsNotifier


async def test_qq_uses_bound_scene_and_observes_original_receipt():
    bound = SimpleNamespace(principal_id="owner", actor_id="actor", conversation_id="conversation")
    context = SimpleNamespace(conversation_id="conversation", actor_id="actor", delivery_allowed=True)
    identity = SimpleNamespace(channel_context=AsyncMock(return_value=bound), context=AsyncMock(return_value=context),
                               channel_target=AsyncMock(return_value={"connection_id": "qq-1", "target_ref": "private:123"}))
    pending = {"execution": "pending", "effect": "none", "delivery": "pending"}
    delivered = {"execution": "succeeded", "effect": "confirmed", "delivery": "delivered"}
    channels = SimpleNamespace(notify_operations=AsyncMock(return_value=pending),
                               operations_notification_status=AsyncMock(return_value=delivered))
    notifier = QQOperationsNotifier(identity, channels)
    result = await notifier(connection_id="qq-1", recipient_id="123", notification_id="notice-1", payload={"kind": "opened"})
    assert result["state"] == "observing"
    result = await notifier.observe(connection_id="qq-1", recipient_id="123", notification_id="notice-1")
    assert result["state"] == "accepted"
    channels.notify_operations.assert_awaited_once()
    channels.operations_notification_status.assert_awaited_once()


async def test_unknown_qq_send_is_observed_without_resending(operations_db):
    settings = default_settings()
    settings["notifications"]["qq"] = {"connection_id": "qq-1", "recipient_id": "123"}
    async with operations_db.transaction() as conn:
        await conn.execute("INSERT INTO operations.settings(singleton,revision,value,author_context) VALUES(true,1,%s,%s)",
                           (Jsonb(settings), Jsonb({})))
    store = IncidentStore(operations_db, None, None)
    incident = await store.ingest({"event_id": "notice-event", "dependency": "fixture",
                                   "error_family": "failed", "version": "v1"})
    class Sender:
        def __init__(self):
            self.sends, self.reads = 0, 0
        async def __call__(self, **_kwargs):
            self.sends += 1
            return {"state": "unknown", "code": "interrupted"}
        async def observe(self, **_kwargs):
            self.reads += 1
            return {"state": "accepted", "meaning": "channel_delivered_not_read"}
    sender = Sender()
    outbox = NotificationOutbox(operations_db, None, qq_send=sender)
    await outbox.enqueue(incident, "opened")
    await outbox.deliver_due()
    async with operations_db.transaction() as conn:
        await conn.execute("UPDATE operations.notification_outbox SET due_at=now()")
    await outbox.deliver_due()
    async with operations_db.transaction(read_only=True) as conn:
        row = await (await conn.execute("SELECT status FROM operations.notification_outbox")).fetchone()
    assert row["status"] == "accepted"
    assert (sender.sends, sender.reads) == (1, 1)


async def test_only_explicit_dependent_alerts_are_suppressed_and_released(operations_db):
    settings = default_settings()
    settings["notifications"]["qq"] = {"connection_id": "qq-1", "recipient_id": "123"}
    async with operations_db.transaction() as conn:
        await conn.execute("INSERT INTO operations.settings(singleton,revision,value,author_context) VALUES(true,1,%s,%s)",
                           (Jsonb(settings), Jsonb({})))
    store = IncidentStore(operations_db, None, None)
    root = await store.ingest({"event_id": "root-event", "dependency": "database",
                               "error_family": "offline", "version": "v1"})
    child = await store.ingest({"event_id": "child-event", "dependency": "jobs",
                                "error_family": "unavailable", "version": "v1",
                                "root_incident_id": root["incident_id"], "affected": ["life"]})
    outbox = NotificationOutbox(operations_db, None)
    await outbox.enqueue(root, "opened")
    await outbox.enqueue(child, "opened")
    assert (await store.get(root["incident_id"]))["caused_incidents"][0]["incident_id"] == child["incident_id"]
    async with operations_db.transaction(read_only=True) as conn:
        count = await (await conn.execute("SELECT count(*) AS n FROM operations.notification_outbox")).fetchone()
    assert count["n"] == 1
    # A second occurrence without a causal reference must not be hidden by the first.
    await store.ingest({"event_id": "independent-event", "dependency": "jobs",
                        "error_family": "unavailable", "version": "v1"})
    await outbox.release_suppressed()
    async with operations_db.transaction(read_only=True) as conn:
        count = await (await conn.execute("SELECT count(*) AS n FROM operations.notification_outbox")).fetchone()
    assert count["n"] == 2
    other = await store.ingest({"event_id": "other-child", "dependency": "media",
                                "error_family": "unavailable", "version": "v1",
                                "root_incident_id": root["incident_id"]})
    await outbox.enqueue(other, "opened")
    async with operations_db.transaction() as conn:
        await conn.execute("UPDATE operations.incidents SET state='resolved' WHERE incident_id=%s", (root["incident_id"],))
    await outbox.release_suppressed()
    async with operations_db.transaction(read_only=True) as conn:
        count = await (await conn.execute("SELECT count(*) AS n FROM operations.notification_outbox")).fetchone()
    assert count["n"] == 3
