"""Cross-role plugin health regression cases; no provider or external channel."""

import asyncio
import unittest
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tianshu.capabilities.plugins.manager import PluginManager
from tianshu.capabilities.plugins.runtime_state import RuntimeState


async def local(operation, *args, **kwargs):
    return operation(*args, **kwargs)


class PluginRuntime(unittest.IsolatedAsyncioTestCase):
    def projection_host(self, runtime=None):
        host = object.__new__(PluginManager)
        host.workers = {}
        host.owner_busy = False
        host.content_discovery = None
        host.comic_files = None
        package = {
            "installed": True,
            "manifest": {"capabilities": {"required": []}},
            "schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "description": {"actions": [{"id": "status", "required_capabilities": []}]},
        }
        host.repository = SimpleNamespace(
            get=Mock(return_value=package), runtime=Mock(return_value=runtime)
        )
        host.vault = SimpleNamespace(names=Mock(return_value={}))
        host.local = local
        instance = {
            "id": "same-instance",
            "package_id": "package",
            "version": "1.0.0",
            "desired_state": "enabled",
            "revision": 2,
            "config": {},
        }
        return host, instance

    async def test_api_role_uses_worker_observation_without_starting_another_host(self):
        proof = {"owner_id": "real-worker", "generation": "actual-child", "instance_revision": 2}
        host, instance = self.projection_host(proof)
        host.start = AsyncMock()
        result = await host.projection(instance)
        self.assertEqual(result["state"], "ready")
        self.assertTrue(result["contributions"]["actions"][0]["availability"]["available"])
        self.assertEqual(result["plugin_runtime"], proof)
        host.repository.runtime.assert_called_once_with("same-instance", 2)
        host.start.assert_not_called()

    async def test_file_helpers_cannot_replace_a_missing_or_expired_plugin_observation(self):
        host, instance = self.projection_host()
        host.comic_files = SimpleNamespace(
            status=AsyncMock(
                return_value={
                    "state": "ready",
                    "roles": {"inputs": "running", "publisher": "running"},
                }
            )
        )
        host.comic_instance = AsyncMock(return_value=True)
        result = await host.projection(instance)
        self.assertEqual(result["file_runtime"]["state"], "ready")
        self.assertEqual(result["state"], "error")
        self.assertFalse(result["contributions"]["actions"][0]["availability"]["available"])

    async def test_publication_uses_actual_child_rpc_health_and_existing_owner(self):
        repository = SimpleNamespace(observe_runtime=Mock())
        alive = SimpleNamespace(
            alive=True, runtime_id="live-child", process=SimpleNamespace(pid=10)
        )
        broken = SimpleNamespace(
            alive=False, runtime_id="broken-rpc", process=SimpleNamespace(pid=11)
        )
        host = SimpleNamespace(
            domain_ports=SimpleNamespace(
                actions=SimpleNamespace(
                    status=lambda: {
                        "owner_id": "existing-action-owner",
                        "ready": True,
                    }
                )
            ),
            started=True,
            closing=False,
            workers={"live": (alive, 2), "broken": (broken, 3)},
            repository=repository,
            local=local,
        )
        await RuntimeState(host).publish()
        repository.observe_runtime.assert_called_once_with(
            "existing-action-owner",
            [
                {"id": "live", "revision": 2, "generation": "live-child", "pid": 10},
            ],
        )

    async def test_reader_exit_clears_original_generation_and_closes_actual_child(self):
        host = object.__new__(PluginManager)
        reader = asyncio.get_running_loop().create_future()
        exited = asyncio.Event()

        async def wait():
            await exited.wait()
            return 0

        process = SimpleNamespace(
            runtime_id="original-generation",
            reader=reader,
            process=SimpleNamespace(wait=wait),
            close=AsyncMock(),
        )
        host.workers = {"plugin": (process, 2)}
        host.locks = defaultdict(asyncio.Lock)
        host.runtime_state = SimpleNamespace(forget=AsyncMock())
        host.executions = SimpleNamespace(revoke=Mock())
        host.instance = AsyncMock(return_value={"id": "plugin"})
        host.comic_instance = AsyncMock(return_value=False)
        reader.set_result(None)  # OS child has not exited, but its RPC is gone.
        await host.watch_worker("plugin", process)
        host.runtime_state.forget.assert_awaited_once_with("plugin", process)
        process.close.assert_awaited_once_with("worker_exited")
        self.assertNotIn("plugin", host.workers)

    async def test_late_exit_of_old_generation_does_not_stop_replacement(self):
        host = object.__new__(PluginManager)
        reader = asyncio.get_running_loop().create_future()
        reader.set_result(None)
        old = SimpleNamespace(
            reader=reader, process=SimpleNamespace(wait=AsyncMock(return_value=0))
        )
        replacement = object()
        host.workers = {"plugin": (replacement, 3)}
        host.locks = defaultdict(asyncio.Lock)
        host.stop_worker = AsyncMock()
        await host.watch_worker("plugin", old)
        host.stop_worker.assert_not_awaited()
        self.assertIs(host.workers["plugin"][0], replacement)
