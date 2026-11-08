"""Real subprocess plugin lifecycle over an isolated PostgreSQL database.

The plugin manager, its ledger, package staging and every child process are real;
nothing about install, reload, version switch or uninstall is mocked.

Windows note: psycopg's async connections need a Selector loop, while child
processes need the Proactor loop. The schema is therefore migrated once in a
Selector-loop thread, and the lifecycle itself runs on a Proactor loop using the
product's own synchronous database pool.
"""

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import ClassVar

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())

from tianshu.capabilities.plugins.manager import PluginManager
from tianshu.capabilities.plugins.process import RemoteError
from tianshu.runtime.credentials import CredentialStore
from tianshu.runtime.database import Database, DatabaseConfig
from tianshu.runtime.migrations import migrate, product_migrations

CAPABILITIES = Path(__file__).parents[2] / "src" / "tianshu" / "capabilities"
MASTER_KEY = "c2VjcmV0LW1hc3teci1rZXktZm9yLXRlc3RzLW9ubHktMzJi"


@pytest.fixture
async def lifecycle_database():
    """Disposable PostgreSQL + pgvector database, created and reclaimed here."""
    admin_dsn = os.environ.get("TIANSHU_TEST_POSTGRES_ADMIN_DSN")
    if not admin_dsn:
        pytest.skip("explicit disposable PostgreSQL+pgvector admin DSN required")
    name = "tianshu_lifecycle_" + uuid.uuid4().hex
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute("CREATE DATABASE " + name)
    dsn = make_conninfo(admin_dsn, dbname=name)
    await asyncio.to_thread(_schema, dsn)
    try:
        yield dsn
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute("DROP DATABASE " + name + " WITH (FORCE)")


def _schema(dsn):
    """Migrate on its own Selector loop; the product runs every role that way."""

    async def work():
        database = Database(DatabaseConfig(dsn))
        await database.open()
        # The product baseline already carries the capabilities and plugin
        # tables; the fresh-install foundation migrations are not applied with it.
        await migrate(database, product_migrations())
        await database.close()

    import selectors

    asyncio.run(work(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))


class Secrets:
    def resolve(self, reference):
        return MASTER_KEY


class Jobs:
    tasks: ClassVar[dict] = {}

    def register(self, name, function, **options):
        self.tasks[name] = function

    def enqueue_sync(self, connection, spec):
        return None

    def current_generation_on(self, connection):
        return "generation"

    def assert_generation_sync_on(self, connection, generation):
        return None


class Runtime:
    def __init__(self, database):
        self.database = database
        self.credentials = CredentialStore(database, Secrets())
        self.jobs = Jobs()


def module(text, slow=0.0):
    return f'''
import asyncio
from tianshu_plugin import Plugin, action, Invocation

class Fixture(Plugin):
    @action(id="value", title="value",
        input_schema={{"type": "object", "properties": {{}}}},
        output_schema={{"type": "object", "properties": {{"text": {{"type": "string"}}}}}})
    async def value(self, call: Invocation, args):
        return {{"text": "{text}"}}

    @action(id="slow", title="slow", timeout_seconds=30,
        input_schema={{"type": "object", "properties": {{}}}},
        output_schema={{"type": "object", "properties": {{"text": {{"type": "string"}}}}}})
    async def slow(self, call: Invocation, args):
        await asyncio.sleep({slow})
        return {{"text": "slow:{text}"}}
'''


def package(directory, identifier, version, text, *, slow=0.0, source=None):
    directory.mkdir()
    (directory / "plugin.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": identifier,
                "name": identifier,
                "version": version,
                "description": "real lifecycle fixture",
                "author": "test",
                "entrypoint": "main:Fixture",
                "sdk": {"min": "2.0.0", "max_exclusive": "3.0.0"},
                "capabilities": {"required": [], "optional": []},
            }
        ),
        encoding="utf-8",
    )
    (directory / "main.py").write_text(module(text, slow) if source is None else source, encoding="utf-8")
    return {"type": "directory", "path": str(directory)}


def generation(manager, identifier):
    """The actual running child generation for this instance, from its process."""
    process, revision = manager.workers[identifier]
    return {"generation": process.runtime_id, "pid": process.process.pid, "revision": revision}


class Host:
    def __init__(self, dsn, tmp_path):
        self.dsn = dsn
        self.tmp_path = tmp_path
        self.database = None
        self.manager = None

    async def open(self):
        self.database = Database(DatabaseConfig(self.dsn))
        await asyncio.to_thread(self.database.sync_pool.open, wait=True)
        self.manager = PluginManager(
            {"database_path": str(self.tmp_path / "library")}, runtime=Runtime(self.database)
        )
        self.manager.worker_enabled = True
        await self.manager.start()
        # The real catalog handler set, so uninstall withdrawal is observable.

        from tianshu.capabilities.catalog import Catalog
        from tianshu.capabilities.plugins.service import Plugins

        self.catalog = Catalog()
        self.plugins = object.__new__(Plugins)
        self.plugins.catalog = self.catalog
        self.manager.domain_ports = self.plugins
        return self.manager

    async def install(self, source, client):
        operation = await self.manager.install(source, client, wait=True)
        assert operation["execution"] == "succeeded", operation.get("error")
        return operation["package_id"]

    async def enable(self, package_id, name, client):
        instance = await self.manager.create(package_id, name, client)
        return await self.manager.state(instance["id"], True, instance["revision"])

    async def call(self, instance_id, action, intent):
        """A real action admission over the same execution path the API uses."""
        manager = self.manager
        from tianshu.capabilities.execution_context import CURRENT_ACTION
        from tianshu.types import Action, OriginKind, TrustedContext

        instance = await manager.instance(instance_id)
        context = TrustedContext(
            "owner", None, None, None, ("system",), ("system",), 1,
            OriginKind.HUMAN, frozenset({"config.publish"}),
        )
        key = instance["id"] + ":" + action + ":" + intent
        trusted = Action(key, intent, "plugin:" + instance["id"] + "::" + action, None, context, {}, OriginKind.HUMAN)
        token = CURRENT_ACTION.set(trusted)
        try:
            document = await manager.executions.invoke(instance_id, action, {}, intent, "owner")
        finally:
            CURRENT_ACTION.reset(token)
        task = manager.executions.running.get(document["id"])
        if task:
            await task
        return await manager.local(manager.repository.call, document["id"])


async def scenario(host, tmp_path):
    manager = await host.open()

    # 1. Install a plugin that did not exist, then enable and call it.
    version_a = await host.install(package(tmp_path / "a", "fixture.lifecycle", "1.0.0", "A"), "install-a")
    instance = await host.enable(version_a, "Lifecycle", "instance-a")
    assert instance["state"] == "ready", instance["state"]
    first = generation(manager, instance["id"])
    assert (await host.call(instance["id"], "value", "call-a"))["result"] == {"text": "A"}

    # 2. Reload keeps identity/configuration/bindings and replaces the process.
    stored = await manager.instance(instance["id"])
    reloaded = await manager.restart(instance["id"], stored["revision"])
    assert reloaded["state"] == "ready"
    runtime = generation(manager, instance["id"])
    assert runtime["generation"] != first["generation"] and runtime["pid"] != first["pid"]
    assert reloaded["config"] == instance["config"]
    assert (await host.call(instance["id"], "value", "call-reload"))["result"] == {"text": "A"}

    # 3. A second plugin keeps its own process across this instance's switch.
    other_package = await host.install(
        package(tmp_path / "other", "fixture.other", "1.0.0", "other"), "install-other"
    )
    other = await host.enable(other_package, "Other", "instance-other")
    other_pid = manager.workers[other["id"]][0].process.pid
    other_generation = manager.workers[other["id"]][0].runtime_id

    await host.install(package(tmp_path / "b", "fixture.lifecycle", "2.0.0", "B"), "install-b")
    current = await manager.instance(instance["id"])
    upgraded = await manager.upgrade(instance["id"], "2.0.0", current["revision"])
    assert upgraded["version"] == "2.0.0" and upgraded["state"] == "ready"
    assert (await host.call(instance["id"], "value", "call-b"))["result"] == {"text": "B"}
    assert manager.workers[other["id"]][0].process.pid == other_pid
    assert manager.workers[other["id"]][0].runtime_id == other_generation
    assert (await host.call(other["id"], "value", "call-other"))["result"] == {"text": "other"}

    # 4. Compatible rollback to the still installed previous package.
    current = await manager.instance(instance["id"])
    rolled = await manager.upgrade(instance["id"], "1.0.0", current["revision"])
    assert rolled["version"] == "1.0.0" and rolled["state"] == "ready"
    assert (await host.call(instance["id"], "value", "call-rollback"))["result"] == {"text": "A"}

    # 5. Uninstall stops the process, withdraws entries, keeps instance data.
    assert instance["id"] in manager.workers
    manager.domain_ports.catalog.handlers[
        "plugin:" + instance["id"] + ":binding:value"
    ] = object()
    result = await manager.uninstall("fixture.lifecycle")
    assert result["uninstalled"] and result["instance_data"] == "retained"
    assert instance["id"] not in manager.workers
    assert not [
        key
        for key in manager.domain_ports.catalog.handlers
        if key.startswith("plugin:" + instance["id"] + ":")
    ]
    stored = await manager.instance(instance["id"])
    assert stored["desired_state"] == "disabled"
    package_document = await manager.local(
        manager.repository.get, "packages", "fixture.lifecycle@1.0.0"
    )
    assert package_document is not None and package_document["installed"] is False
    assert other["id"] in manager.workers
    assert (await host.call(other["id"], "value", "call-after-uninstall"))["result"] == {
        "text": "other"
    }
    await manager.close()


async def broken_scenario(host, tmp_path):
    manager = await host.open()
    good = await host.install(package(tmp_path / "good", "fixture.broken", "1.0.0", "ok"), "install-good")
    instance = await host.enable(good, "Broken", "instance-broken")
    assert (await host.call(instance["id"], "value", "call-good"))["result"] == {"text": "ok"}

    # The package itself installs and describes fine; it fails the switch's
    # start/health confirmation, which is the case rollback must cover.
    broken = '''
import asyncio
from tianshu_plugin import Plugin, PluginError, action, Invocation

class Fixture(Plugin):
    async def start(self):
        raise PluginError("plugin_start_failed", "fixture refuses to start", effect="none")

    @action(id="value", title="value",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {"text": {"type": "string"}}})
    async def value(self, call: Invocation, args):
        return {"text": "never"}
'''
    await host.install(
        package(tmp_path / "bad", "fixture.broken", "2.0.0", "never", source=broken),
        "install-broken",
    )

    current = await manager.instance(instance["id"])
    with pytest.raises(RemoteError):
        await manager.upgrade(instance["id"], "2.0.0", current["revision"])
    restored = await manager.instance(instance["id"])
    assert restored["version"] == "1.0.0"
    # The snapshot is restored exactly; the instance was never switched.
    assert restored["revision"] == current["revision"]
    operations = [
        item
        for item in await manager.local(manager.repository.list, "operations")
        if item.get("kind") == "upgrade"
    ]
    assert operations and operations[-1]["execution"] == "failed"
    assert operations[-1]["error"]["rolled_back"] is True
    assert operations[-1]["error"]["restored_version"] == "1.0.0"
    assert (await host.call(instance["id"], "value", "call-restored"))["result"] == {"text": "ok"}
    await manager.close()


async def concurrency_scenario(host, tmp_path):
    manager = await host.open()
    slow_package = await host.install(
        package(tmp_path / "slow", "fixture.slow", "1.0.0", "slow", slow=2.5), "install-slow"
    )
    slow = await host.enable(slow_package, "Slow", "instance-slow")
    fast_package = await host.install(
        package(tmp_path / "fast", "fixture.fast", "1.0.0", "fast"), "install-fast"
    )
    fast = await host.enable(fast_package, "Fast", "instance-fast")

    working = asyncio.create_task(host.call(slow["id"], "slow", "slow-work"))
    await asyncio.sleep(0.4)
    started = time.monotonic()
    assert (await host.call(fast["id"], "value", "fast-work"))["result"] == {"text": "fast"}
    assert time.monotonic() - started < 2.0
    # Reloading the busy instance refuses only its own new admission.
    stored = await manager.instance(slow["id"])
    reloaded = await manager.restart(slow["id"], stored["revision"])
    assert reloaded["state"] == "ready"
    await working
    assert (await host.call(fast["id"], "value", "fast-after"))["result"] == {"text": "fast"}
    await manager.close()


@pytest.mark.asyncio
async def test_online_install_reload_upgrade_rollback_and_uninstall(lifecycle_database, tmp_path):
    await scenario(Host(lifecycle_database, tmp_path), tmp_path)


@pytest.mark.asyncio
async def test_failed_upgrade_restores_the_previous_version(lifecycle_database, tmp_path):
    await broken_scenario(Host(lifecycle_database, tmp_path), tmp_path)


@pytest.mark.asyncio
async def test_long_work_and_reload_in_one_plugin_keep_another_serving(lifecycle_database, tmp_path):
    await concurrency_scenario(Host(lifecycle_database, tmp_path), tmp_path)


async def failed_install_scenario(host, tmp_path):
    manager = await host.open()
    directory = tmp_path / "incompatible"
    directory.mkdir()
    (directory / "plugin.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "fixture.incompatible",
                "name": "incompatible",
                "version": "1.0.0",
                "description": "fixture",
                "author": "test",
                "entrypoint": "main:Fixture",
                # Outside the host's supported SDK window: the package never installs.
                "sdk": {"min": "3.0.0", "max_exclusive": "4.0.0"},
                "capabilities": {"required": [], "optional": []},
            }
        ),
        encoding="utf-8",
    )
    (directory / "main.py").write_text(module("never"), encoding="utf-8")
    operation = await manager.install({"type": "directory", "path": str(directory)}, "install-bad", wait=True)
    assert operation["execution"] == "failed"
    assert operation["error"]["code"] == "plugin_incompatible"
    # A failed installation cannot become an enableable version.
    with pytest.raises(Exception) as error:
        await manager.package("fixture.incompatible@1.0.0")
    assert getattr(error.value, "code", "") == "not_found"
    with pytest.raises(Exception) as error:
        await manager.create("fixture.incompatible@1.0.0", "Bad", "instance-bad")
    assert getattr(error.value, "code", "") == "not_found"
    # Repeating the same request returns the original failed operation, not a second one.
    again = await manager.install({"type": "directory", "path": str(directory)}, "install-bad", wait=True)
    assert again["id"] == operation["id"] and again["execution"] == "failed"
    await manager.close()


async def reinstall_scenario(host, tmp_path):
    manager = await host.open()
    source = package(tmp_path / "same", "fixture.reinstall", "1.0.0", "keep")
    first = await host.install(source, "install-reinstall")
    instance = await host.enable(first, "Kept", "instance-kept")
    revision = (await manager.instance(instance["id"]))["revision"]
    await manager.uninstall("fixture.reinstall")
    assert (await manager.instance(instance["id"]))["desired_state"] == "disabled"
    # Reinstall of the same package keeps the retained instance identity and data.
    second = await host.install(source, "install-reinstall-2")
    assert second == first
    retained = await manager.instance(instance["id"])
    assert retained["id"] == instance["id"] and retained["name"] == "Kept"
    assert retained["config"] == instance["config"]
    # Disabling for uninstall is the only revision change; identity and data stay.
    assert retained["revision"] == revision + 1
    restored = await manager.create(second, "Kept", "instance-kept")
    assert restored["id"] == instance["id"]
    enabled = await manager.state(instance["id"], True, retained["revision"])
    assert enabled["state"] == "ready"
    assert (await host.call(instance["id"], "value", "call-reinstall"))["result"] == {
        "text": "keep"
    }
    await manager.close()


@pytest.mark.asyncio
async def test_failed_install_is_never_enableable_and_repeats_idempotently(lifecycle_database, tmp_path):
    await failed_install_scenario(Host(lifecycle_database, tmp_path), tmp_path)


@pytest.mark.asyncio
async def test_reinstall_restores_the_retained_instance(lifecycle_database, tmp_path):
    await reinstall_scenario(Host(lifecycle_database, tmp_path), tmp_path)


@pytest.mark.asyncio
async def test_disabled_instance_keeps_its_config_in_the_ledger(lifecycle_database, tmp_path):
    host = Host(lifecycle_database, tmp_path)
    manager = await host.open()
    package_id = await host.install(
        package(tmp_path / "cfg", "fixture.config", "1.0.0", "cfg"), "install-cfg"
    )
    instance = await host.enable(package_id, "Config", "instance-cfg")
    stored = await manager.instance(instance["id"])
    assert stored["config"] == {} and stored["bindings"] == []
    disabled = await manager.state(instance["id"], False, stored["revision"])
    assert disabled["desired_state"] == "disabled"
    assert instance["id"] not in manager.workers
    # The ledger keeps identity, configuration and bindings for a later enable.
    after = await manager.instance(instance["id"])
    assert after["config"] == stored["config"]
    assert after["package_id"] == stored["package_id"]
    assert after["version"] == stored["version"]
    await manager.close()
