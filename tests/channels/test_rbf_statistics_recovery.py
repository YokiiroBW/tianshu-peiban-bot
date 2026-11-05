from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.channels import ingress
from tianshu.channels.forward import expand_forward
from tianshu.channels.media import Media
from tianshu.channels.service import Channels
from tianshu.conversation.types import InputPart
from tianshu.runtime.recovery_policy import RecoveryWait


def wait():
    return RecoveryWait("source_lock_set_changed", datetime.now(UTC) + timedelta(seconds=30),
                        dependency="sources:lock_set")


async def test_ingress_wait_retains_the_original_event_and_registers_resource_recovery(monkeypatch):
    error = wait()
    handler = AsyncMock(side_effect=error)
    monkeypatch.setattr(ingress, "_process_event", handler)
    service = object()
    with pytest.raises(RecoveryWait) as raised:
        await ingress.process_event(service, "event-original")
    assert raised.value is error
    handler.assert_awaited_once_with(service, "event-original")

    registrations = {}
    jobs = SimpleNamespace(register=lambda name, handler, **options: registrations.update({name: (handler, options)}))
    owner = SimpleNamespace(runtime=SimpleNamespace(jobs=jobs), resume_delivery=AsyncMock(),
                            reconcile_media_statistics=AsyncMock())
    Channels.register_tasks(owner)
    assert registrations["channels.ingest"][1]["resource_recovery"] is True
    assert "resource_recovery" not in registrations["channels.resume_delivery"][1]


async def test_original_media_and_forward_waits_are_not_converted_to_missing_content():
    error = wait()
    sources = SimpleNamespace(register_media=AsyncMock(side_effect=error), capture=AsyncMock(side_effect=error))
    media = Media(None, sources, None)
    media.download = AsyncMock(return_value=object())
    image = InputPart("image", metadata={"channel": "onebot.v11", "original_pending": True,
        "segment": {"type": "image", "data": {"url": "https://fixture.invalid/image"}}})
    connection = {"id": "connection-1", "namespace": "fixture"}
    with pytest.raises(RecoveryWait) as raised:
        await media.receive(object(), connection, image, AsyncMock(), event_id="event-1", part_index=0)
    assert raised.value is error

    node = InputPart("forward", metadata={"segment": {"type": "node", "data": {"content": "fixture"}}})
    with pytest.raises(RecoveryWait) as raised:
        await expand_forward(SimpleNamespace(sources=sources), object(), connection, node, AsyncMock(), "event-1")
    assert raised.value is error
