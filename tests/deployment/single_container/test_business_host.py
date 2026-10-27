import asyncio
import json
import os
import sqlite3
import threading
from contextlib import asynccontextmanager

from aiohttp import web
from fastapi import FastAPI

from deploy.single_container.host import BusinessHost
from deploy.single_container.host_settings import MODULES


class FixtureModule:
    def __init__(self, name, spec, environment, **kwargs):
        self.name, self.spec, self.environment = name, spec, environment
        self.block = threading.Event()
        self.entered = threading.Event()
        self.closed = threading.Event()
        self.owner = None
        self.quiesced = False

    async def __call__(self, ports):
        self.owner = threading.get_ident()
        self.ports = ports
        database = sqlite3.connect(":memory:")
        database.execute("create table owned (value integer)")

        async def close():
            assert threading.get_ident() == self.owner
            database.close()
            self.closed.set()

        if self.name in {"platform", "gateway"}:
            app = web.Application()

            async def context(app):
                yield
                await close()

            async def route(request):
                return web.json_response({"alive": True})

            app.cleanup_ctx.append(context)
            app.router.add_get("/callback", route)
        else:

            @asynccontextmanager
            async def lifespan(app):
                yield
                await close()

            app = FastAPI(lifespan=lifespan)

            @app.get("/callback")
            async def callback():
                return {"alive": True}

        return app

    async def start_public(self, app):
        assert self.owner == threading.get_ident()

    async def stop_accepting(self, app):
        assert self.owner == threading.get_ident()

    async def drain_ingress(self, app, *, timeout):
        return True

    async def quiesce(self, app):
        # Real callback exchange remains open while all producer domains stop.
        target = "memory" if self.name == "platform" else "platform"
        async with (
            self.ports.session_factory(target)() as client,
            client.get(f"https://{target}.internal:8443/callback") as response,
        ):
            assert response.status == 200
        self.quiesced = True

    async def drain(self, app, *, timeout):
        assert self.quiesced
        return True

    async def close_public(self, app):
        assert not self.closed.is_set()

    async def health(self, app):
        if self.block.is_set():
            self.entered.set()
            self.block.wait()
        return {"ok": True, "code": "fixture_ready"}


def configuration(tmp_path):
    return {
        "state_file": str(tmp_path / "host.json"),
        "shutdown_seconds": 0.02,
        "modules": {
            name: {
                "origin": f"https://{name}.internal:8443",
                "env": {"IDENTITY": name},
                "env_file": [],
            }
            for name in MODULES
        },
    }


def test_five_module_owner_lifecycles_callbacks_and_environment_isolation(tmp_path):
    async def scenario():
        before = dict(os.environ)
        host = BusinessHost(configuration(tmp_path), module_factory=FixtureModule)
        await host.open()
        assert len({module.owner for module in host.modules.values()}) == 5
        assert threading.get_ident() not in {
            module.owner for module in host.modules.values()
        }
        assert os.environ["IDENTITY"] == "platform"
        for name, module in host.modules.items():
            assert module.environment["IDENTITY"] == name
        state = json.loads((tmp_path / "host.json").read_bytes())
        assert state["status"] == "ready"
        assert set(state["modules"]) == set(MODULES)
        assert await host.close(timeout=3)
        assert all(module.closed.is_set() for module in host.modules.values())
        assert dict(os.environ) == before
        assert json.loads((tmp_path / "host.json").read_bytes())["status"] == "stopped"

    asyncio.run(scenario())


def test_sorted_configuration_keeps_platform_before_native_gateway_renewal(tmp_path):
    import pytest

    renewal = pytest.importorskip("tianshu_gateway.origin_renewal")
    from datetime import datetime, timedelta, timezone

    from tianshu_gateway.config import EnvSecrets, RegisteredTargets

    opened = []
    renewed = []
    platform_url = "https://platform.internal:8443"
    credential = "isolated-renewal-credential"
    reference = "origin:" + "a" * 32

    class RenewingModule(FixtureModule):
        async def __call__(self, ports):
            app = await super().__call__(ports)

            async def started(app):
                opened.append(self.name)
                yield

            if self.name == "platform":

                async def renew(request):
                    assert request.headers["Authorization"] == "Bearer " + credential
                    body = await request.json()
                    assert body["assertion_ref"] == reference
                    renewed.append(True)
                    return web.json_response(
                        {
                            **body,
                            "expires_at": (
                                datetime.now(timezone.utc) + timedelta(seconds=60)
                            )
                            .isoformat()
                            .replace("+00:00", "Z"),
                        }
                    )

                app.router.add_post(renewal.PATH, renew)
            if self.name in {"platform", "gateway"}:
                app.cleanup_ctx.append(started)
            if self.name == "gateway":

                async def native_renewal(app):
                    async with ports.session_factory("platform")() as client:
                        environment = {"CREDENTIAL": credential, "ORIGIN": reference}
                        origin = renewal.OriginRenewal(
                            client,
                            RegisteredTargets(
                                [{"base_url": platform_url, "addresses": ["127.0.0.1"]}]
                            ),
                            EnvSecrets({"platform": "CREDENTIAL"}, environ=environment),
                            platform_url,
                            "platform",
                            "ORIGIN",
                            environ=environment,
                        )
                        await origin.start()
                        try:
                            yield
                        finally:
                            await origin.close()

                app.cleanup_ctx.append(native_renewal)
            return app

    async def scenario():
        # This is the real native capture→candidate JSON key order that failed.
        config = json.loads(json.dumps(configuration(tmp_path), sort_keys=True))
        host = BusinessHost(config, module_factory=RenewingModule)
        await asyncio.wait_for(host.open(), 3)
        try:
            assert tuple(host.modules) == MODULES
            assert opened == ["platform", "gateway"]
            assert renewed == [True]
            assert host.results["gateway"]["health"]["ok"]
        finally:
            assert await host.close(timeout=3)

    asyncio.run(scenario())


def test_blocked_owner_does_not_freeze_health_or_pretend_shutdown_completed(tmp_path):
    async def scenario():
        host = BusinessHost(configuration(tmp_path), module_factory=FixtureModule)
        await host.open()
        memory = host.modules["memory"]
        # A synchronous native operation can monopolize its own module loop.
        release = threading.Event()

        async def blocked(app):
            memory.entered.set()
            release.wait()
            return {"ok": True, "code": "fixture_ready"}

        memory.health = blocked
        await host.probe_once(timeout=0.1)
        assert memory.entered.is_set()
        assert host.results["memory"]["health"]["code"] == "probe_timeout"
        assert host.results["gateway"]["health"]["ok"]
        await host.probe_once(timeout=0.1)
        assert len(host.pending_probes) == 1
        assert not await host.close(timeout=0.01)
        assert not memory.closed.is_set()
        assert json.loads((tmp_path / "host.json").read_bytes())["status"] == "stopping"
        release.set()
        assert await host.close(timeout=3)
        assert memory.closed.is_set()

    asyncio.run(scenario())
