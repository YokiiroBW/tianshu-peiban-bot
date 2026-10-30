import asyncio
import json
import os
import threading
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

import pytest

from deploy.single_container import host as host_module
from deploy.single_container.applications import NativeModule
from deploy.single_container.host import BusinessHost
from deploy.single_container.host_settings import MODULES
from test_business_host import FixtureModule, configuration


class DiagnosticModule(FixtureModule):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ready = True
        self.diagnostics = SimpleNamespace(
            instance_id=str(uuid5(NAMESPACE_URL, self.name))
        )

    async def health(self, app):
        assert self.owner == threading.get_ident()
        return {"ok": self.ready, "code": "fixture_health"}

    def diagnostic_instance(self, app):
        assert self.owner == threading.get_ident()
        return NativeModule.diagnostic_instance(self, app)


def snapshot(tmp_path):
    return json.loads((tmp_path / "host.json").read_bytes())


def test_only_complete_healthy_running_host_publishes_native_instances(tmp_path):
    async def scenario():
        host = BusinessHost(configuration(tmp_path), module_factory=DiagnosticModule)
        host.publish("starting")
        assert snapshot(tmp_path)["diagnostics"] == {"complete": False, "instances": {}}
        try:
            await host.open()
            expected = {
                name: module.diagnostics.instance_id
                for name, module in host.modules.items()
            }
            state = snapshot(tmp_path)
            assert state["version"] == 1 and state["pid"] == os.getpid()
            assert state["status"] == "ready"
            assert state["diagnostics"] == {"complete": True, "instances": expected}
            assert set(expected) == set(MODULES)
            assert all(
                set(row["health"]) == {"ok", "code"}
                for row in state["modules"].values()
            )

            host.modules["memory"].ready = False
            await host.probe_once()
            assert snapshot(tmp_path)["status"] == "degraded"
            assert snapshot(tmp_path)["diagnostics"] == {
                "complete": False,
                "instances": {},
            }
            host.modules["memory"].ready = True
            await host.probe_once()
            assert snapshot(tmp_path)["diagnostics"] == {
                "complete": True,
                "instances": expected,
            }
            await host.close(timeout=0)
            assert snapshot(tmp_path)["diagnostics"] == {
                "complete": False,
                "instances": {},
            }
        finally:
            assert await host.close(timeout=3)
        assert snapshot(tmp_path)["status"] == "stopped"
        assert snapshot(tmp_path)["diagnostics"] == {"complete": False, "instances": {}}

    asyncio.run(scenario())


@pytest.mark.parametrize("identity", [None, "", "not-a-uuid", "A" * 32, object()])
def test_missing_or_invalid_identity_never_forges_proof_or_blocks_business(
    tmp_path, identity
):
    class IncompleteModule(DiagnosticModule):
        async def __call__(self, ports):
            app = await super().__call__(ports)
            if self.name == "knowledge":
                self.diagnostics.instance_id = identity
            return app

    async def scenario():
        host = BusinessHost(configuration(tmp_path), module_factory=IncompleteModule)
        try:
            await host.open()
            assert snapshot(tmp_path)["status"] == "ready"
            assert snapshot(tmp_path)["diagnostics"] == {
                "complete": False,
                "instances": {},
            }
        finally:
            assert await host.close(timeout=3)

    asyncio.run(scenario())


def test_partial_startup_never_publishes_complete_registry(tmp_path, monkeypatch):
    snapshots = []
    write_state = host_module.write_state

    def record(path, state):
        snapshots.append(state["diagnostics"])
        write_state(path, state)

    monkeypatch.setattr(host_module, "write_state", record)

    class FailedModule(DiagnosticModule):
        async def __call__(self, ports):
            if self.name == "memory":
                raise ValueError("synthetic partial startup")
            return await super().__call__(ports)

    async def scenario():
        host = BusinessHost(configuration(tmp_path), module_factory=FailedModule)
        with pytest.raises(ValueError, match="synthetic partial startup"):
            await asyncio.wait_for(host.open(), 2)
        assert snapshot(tmp_path)["status"] == "failed"
        assert host.exchange.started_modules() == ()
        assert snapshots and all(
            value == {"complete": False, "instances": {}} for value in snapshots
        )

    asyncio.run(scenario())


def test_worker_metrics_use_owner_loop_and_do_not_reuse_failed_or_timed_out_samples(
    tmp_path,
):
    release = threading.Event()

    class MetricsModule(DiagnosticModule):
        broken = False

        async def __call__(self, ports):
            app = await super().__call__(ports)
            if self.name == "companion":

                def metrics():
                    assert self.owner == threading.get_ident()
                    if self.broken:
                        raise RuntimeError("private failure must not affect readiness")
                    return {"schema_version": 1, "workers": {"life.work": {"idle": 2}}}

                app.state.worker_metrics = metrics
            return app

        def runtime_metrics(self, app):
            return NativeModule.runtime_metrics(self, app)

        async def health(self, app):
            if self.block.is_set():
                self.entered.set()
                release.wait()
            return await super().health(app)

    async def scenario():
        host = BusinessHost(configuration(tmp_path), module_factory=MetricsModule)
        try:
            await host.open()
            state = snapshot(tmp_path)
            instance = state["diagnostics"]["instances"]["companion"]
            assert state["modules"]["companion"]["metrics"] == {
                "diagnostic_instance": instance,
                "values": {"schema_version": 1, "workers": {"life.work": {"idle": 2}}},
            }
            assert all(
                "metrics" not in row
                for name, row in state["modules"].items()
                if name != "companion"
            )
            host.modules["companion"].broken = True
            await host.probe_once()
            state = snapshot(tmp_path)
            assert state["status"] == "ready" and state["diagnostics"]["complete"]
            assert "metrics" not in state["modules"]["companion"]
            host.modules["companion"].broken = False
            host.modules["companion"].block.set()
            await host.probe_once(timeout=0.02)
            assert "metrics" not in snapshot(tmp_path)["modules"]["companion"]
        finally:
            release.set()
            host.modules["companion"].block.clear()
            assert await host.close(timeout=3)

    asyncio.run(scenario())
