"""Optional component failure and the existing logs route remain explicit."""

from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from tianshu.capabilities.api import register_routes
from tianshu.capabilities.infrastructure.jobs import InfrastructureOperation
from tianshu.capabilities.service import Capabilities
from tianshu.types import Action, Attempt, OriginKind, TrustedContext


@pytest.mark.asyncio
async def test_media_and_plugin_start_failures_do_not_prevent_other_independent_owners():
    media = SimpleNamespace(config={}, start=AsyncMock(side_effect=RuntimeError("offline")),
                            background=SimpleNamespace(activate=AsyncMock()))
    plugins = SimpleNamespace(worker_enabled=False, owner_busy=False,
                              start=AsyncMock(side_effect=RuntimeError("broken plugin")))
    infrastructure = SimpleNamespace(config={}, start=AsyncMock())
    service = SimpleNamespace(initialized=False, prepared=True, worker=True, lifecycle={},
                              prepare=AsyncMock(), actions=SimpleNamespace(start=AsyncMock()),
                              media=media, plugin_host=plugins, infrastructure=infrastructure)
    service._start_component = MethodType(Capabilities._start_component, service)
    await Capabilities.initialize(service)
    assert service.initialized
    assert service.lifecycle["media"]["state"] == "failed"
    assert service.lifecycle["plugins"]["state"] == "failed"
    assert service.lifecycle["media_schedules"]["state"] == "ready"
    assert service.lifecycle["infrastructure"]["state"] == "ready"
    media.background.activate.assert_awaited_once()
    infrastructure.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_infrastructure_reconcile_only_reads_original_operation():
    from unittest.mock import MagicMock

    record = {"state": "unknown", "settled": False, "kind": "action", "acceptance": "unknown",
              "reason_codes": ["unknown"]}
    control = SimpleNamespace(lookup=MagicMock(return_value=record),
                              repo=SimpleNamespace(claim=MagicMock()), _work=AsyncMock())

    async def local(operation, *args, **kwargs):
        return operation(*args, **kwargs)

    control.local = local
    operation = InfrastructureOperation(SimpleNamespace(infrastructure=SimpleNamespace(control=control)))
    context = TrustedContext("author", None, None, None, ("system",), ("system",), 1,
                             OriginKind.HUMAN, frozenset({"external.manage"}))
    action = Action("original", "original", "infrastructure.execute", None, context,
                    {"operation_id": "original-handle"}, OriginKind.HUMAN)
    result = await operation.reconcile(action, Attempt("original-attempt", "original"))
    assert (result.action_id, result.attempt_id, result.execution, result.effect) == (
        "original", "original-attempt", "unknown", "unknown")
    control.lookup.assert_called_once_with(operation_id="original-handle")
    control.repo.claim.assert_not_called()
    control._work.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_system_logs_route_passes_full_filter_and_opaque_cursor():
    context = TrustedContext("author", None, None, None, ("system",), ("system",), 1,
                             OriginKind.HUMAN, frozenset({"config.publish"}))
    response = {"streams": [], "next_end_ns": None, "entries": [{"id": "log"}],
                "next_cursor": "next", "coverage": {"complete": False}, "freshness": {}}
    diagnostics = SimpleNamespace(logs=AsyncMock(return_value=response),
                                  tasks=AsyncMock(return_value={"jobs": []}))
    service = SimpleNamespace(ready=lambda: None, authority=SimpleNamespace(check=AsyncMock()),
                              runtime=SimpleNamespace(diagnostics=diagnostics))

    @web.middleware
    async def identity(request, handler):
        request["trusted_context"] = context
        return await handler(request)

    app = web.Application(middlewares=[identity])
    register_routes(app, service)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        query = {"start_ns": 1, "end_ns": 999, "role": "independent", "level": "error",
                 "module": "media", "correlation_id": "c", "trace_id": "t", "job_id": "j",
                 "attempt_id": "a", "keyword": "needle", "exclude_conversation": True,
                 "cursor": "original-cursor", "limit": 25}
        result = await client.post("/api/v1/system/logs", json=query)
        assert result.status == 200 and await result.json() == response
        diagnostics.logs.assert_awaited_once_with(**query)
        tasks = await client.post("/api/v1/system/tasks", json={"job_id": 123, "limit": 25})
        assert tasks.status == 200 and await tasks.json() == {"jobs": []}
        diagnostics.tasks.assert_awaited_once_with(job_id=123, limit=25)
    finally:
        await client.close()
