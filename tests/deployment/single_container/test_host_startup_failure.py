import asyncio
import os
import sqlite3
import threading

import pytest
from aiohttp import web

from deploy.single_container.host import BusinessHost


def test_first_factory_failure_skips_unopened_lifecycle_targets_and_joins_owner(
    tmp_path,
):
    phases = []

    class UnopenedModule:
        def __init__(self, name, *args, **kwargs):
            self.name = name

        async def __call__(self, ports):
            assert self.name == "platform"
            raise ValueError("synthetic first-module startup refusal")

        async def quiesce(self, app):
            phases.append(self.name)
            raise AssertionError("unopened app has no producer lifecycle")

    async def scenario():
        before = dict(os.environ)
        config = {
            "state_file": str(tmp_path / "host.json"),
            "shutdown_seconds": 0.02,
            "modules": {
                name: {
                    "origin": "https://" + name + ".internal:8443",
                    "env": {},
                    "env_file": [],
                }
                for name in ("companion", "gateway", "knowledge", "memory", "platform")
            },
        }
        host = BusinessHost(config, module_factory=UnopenedModule)
        with pytest.raises(ValueError, match="synthetic first-module startup refusal"):
            await asyncio.wait_for(host.open(), 1)
        assert not phases
        assert host.exchange.started_modules() == ()
        assert not any(
            owner.thread is not None and owner.thread.is_alive()
            for owner in host.exchange._owners.values()
        )
        assert dict(os.environ) == before

    asyncio.run(scenario())


def test_partial_startup_keeps_real_owner_work_and_callback_until_receipt(tmp_path):
    class PartialModule:
        def __init__(self, name, spec, environment, **kwargs):
            self.name = name
            self.release = threading.Event()
            self.draining = threading.Event()
            self.closed = threading.Event()
            self.worker = None
            self.callback_status = None

        async def __call__(self, ports):
            if self.name == "memory":
                raise ValueError("synthetic later-module startup refusal")
            database = sqlite3.connect(":memory:")
            database.execute("CREATE TABLE receipt(value TEXT)")
            app = web.Application()

            async def callback(request):
                return web.json_response({"alive": True})

            app.router.add_get("/callback", callback)

            async def lifecycle(app):
                if self.name == "platform":

                    async def work():
                        await asyncio.to_thread(self.release.wait)
                        async with (
                            ports.session_factory("gateway")() as client,
                            client.get(
                                "https://gateway.internal:8443/callback"
                            ) as response,
                        ):
                            await response.read()
                            self.callback_status = response.status
                        database.execute("INSERT INTO receipt VALUES ('committed')")
                        database.commit()

                    self.worker = asyncio.create_task(work())
                try:
                    yield
                finally:
                    if self.worker is not None:
                        assert self.worker.done()
                        assert database.execute(
                            "SELECT value FROM receipt"
                        ).fetchone() == ("committed",)
                    database.close()
                    self.closed.set()

            app.cleanup_ctx.append(lifecycle)
            return app

        async def stop_accepting(self, app):
            pass

        async def drain_ingress(self, app, *, timeout):
            return True

        async def quiesce(self, app):
            pass

        async def drain(self, app, *, timeout):
            if self.worker is not None:
                self.draining.set()
                await asyncio.shield(self.worker)
            return True

        async def close_public(self, app):
            pass

    async def scenario():
        before = dict(os.environ)
        config = {
            "state_file": str(tmp_path / "host.json"),
            "shutdown_seconds": 0.02,
            "modules": {
                name: {
                    "origin": "https://" + name + ".internal:8443",
                    "env": {"IDENTITY": name},
                    "env_file": [],
                }
                for name in ("platform", "gateway", "memory", "knowledge", "companion")
            },
        }
        host = BusinessHost(config, module_factory=PartialModule)
        opening = asyncio.create_task(host.open())
        platform = host.modules["platform"]
        try:
            assert await asyncio.to_thread(platform.draining.wait, 2)
            assert host.started and not opening.done()
            assert host.previous_environment is not None
            assert os.environ["IDENTITY"] == "platform"
            assert not platform.closed.is_set()
        finally:
            platform.release.set()
        with pytest.raises(ValueError, match="synthetic later-module startup refusal"):
            await asyncio.wait_for(opening, 3)
        if host.close_task is not None and not host.close_task.done():
            await host.close(timeout=3)
        assert platform.callback_status == 200
        assert platform.closed.is_set()
        assert not any(
            owner.thread is not None and owner.thread.is_alive()
            for owner in host.exchange._owners.values()
        )
        assert dict(os.environ) == before

    asyncio.run(scenario())
