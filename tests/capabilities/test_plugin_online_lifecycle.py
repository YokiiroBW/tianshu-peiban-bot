"""Real subprocess plugin lifecycle over an isolated PostgreSQL database.

The plugin manager, its ledger, package staging and every child process are real;
nothing about install, reload, version switch or uninstall is mocked.

Windows note: psycopg's async connections need a Selector loop, while child
processes need the Proactor loop. The schema is therefore migrated once in a
Selector-loop thread, and the lifecycle itself runs on a Proactor loop using the
product's own synchronous database pool.
"""

import asyncio
import base64
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

from tianshu.capabilities.contracts import Fault
from tianshu.capabilities.plugins.manager import PluginManager
from tianshu.runtime.credentials import CredentialStore
from tianshu.runtime.database import Database, DatabaseConfig
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import migrate, product_migrations
from tianshu.types import TrustedContext

CAPABILITIES = Path(__file__).parents[2] / "src" / "tianshu" / "capabilities"
# 32-byte base64 key, so sealed plugin secrets really round-trip in these tests.
MASTER_KEY = base64.urlsafe_b64encode(b"tianshu-lifecycle-key-32-bytes!!").decode()


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


def package(directory, identifier, version, text, *, slow=0.0, source=None, capabilities=()):
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
                "capabilities": {
                    "required": [dict(item) for item in capabilities],
                    "optional": [],
                },
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
        from tianshu.types import Action, OriginKind

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
    # The recorded success is the process that actually serves the new version,
    # not a staged child that was closed afterwards.
    upgrades = [
        item
        for item in await manager.local(manager.repository.list, "operations")
        if item.get("kind") == "upgrade" and item["instance_id"] == instance["id"]
    ]
    assert upgrades[-1]["execution"] == "succeeded"
    assert generation(manager, instance["id"])["generation"] == upgrades[-1]["generation"]
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
    with pytest.raises(AppError):
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


CONFIGURED_SOURCE = """
from tianshu_plugin import Plugin, action, Invocation

class Fixture(Plugin):
    @action(id="value", title="value",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {"text": {"type": "string"}}})
    async def value(self, call: Invocation, args):
        return {"text": "configured"}
"""

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "default": "default-label"},
        "token": {"type": "string", "x-secret": True},
    },
    "required": ["token"],
    "additionalProperties": False,
}


def configured_package(directory, identifier, version):
    source = package(directory, identifier, version, "configured", source=CONFIGURED_SOURCE)
    manifest_path = Path(source["path"]) / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["config_schema"] = "config.schema.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (Path(source["path"]) / "config.schema.json").write_text(
        json.dumps(CONFIG_SCHEMA), encoding="utf-8"
    )
    return source


def bound(binding_id, identifier):
    return {
        "kind": "binding",
        "schema_version": 1,
        "binding_id": str(uuid.uuid5(uuid.NAMESPACE_URL, binding_id)),
        "instance_id": identifier,
        "revision": 1,
        "enabled": True,
        "priority": 0,
        "overrides": {},
        "scope": {"kind": "workspace", "workspace_id": "system-1"},
        "entries": [{"kind": "action", "id": "value"}],
    }


async def retained_config_uninstall_scenario(host, tmp_path):
    """A normal uninstall keeps configuration, bindings and secrets for reinstall."""
    manager = await host.open()
    first = await host.install(
        configured_package(tmp_path / "cfg", "fixture.configured", "1.0.0"), "install-cfg"
    )
    instance = await manager.create(first, "Configured", "instance-configured")
    configured = await manager.configure(
        instance["id"], {"label": "kept"}, {"token": "s3cret"}, instance["revision"]
    )
    bound_config = await manager.bindings(
        configured["id"], [bound("workspace-binding", configured["id"])], configured["revision"]
    )
    enabled = await manager.state(bound_config["id"], True, bound_config["revision"])
    assert enabled["state"] == "ready"
    revision = (await manager.instance(instance["id"]))["revision"]
    assert (await manager.uninstall("fixture.configured"))["instance_data"] == "retained"
    retained = await manager.instance(instance["id"])
    assert retained["config"]["label"] == "kept"
    assert retained["bindings"] != []
    assert await manager.local(manager.vault.names, instance["id"]) == {"token": True}
    assert retained["revision"] == revision + 1
    again = await host.install(
        configured_package(tmp_path / "cfg2", "fixture.configured", "1.0.0"), "install-cfg-again"
    )
    assert again == first
    restored = await manager.create(again, "Configured", "instance-configured")
    assert restored["id"] == instance["id"]
    assert restored["config"] == retained["config"]
    assert restored["bindings"] == retained["bindings"]
    assert restored["secret_configured"] == {"token": True}
    ready = await manager.state(instance["id"], True, restored["revision"])
    assert ready["state"] == "ready"
    assert (await host.call(instance["id"], "value", "call-restored"))["result"] == {
        "text": "configured"
    }
    await manager.close()


async def purged_config_uninstall_scenario(host, tmp_path):
    """purge_data removes configuration, bindings, secrets and state for good."""
    manager = await host.open()
    first = await host.install(
        configured_package(tmp_path / "cfg", "fixture.purged", "1.0.0"), "install-purged"
    )
    instance = await manager.create(first, "Purged", "instance-purged")
    configured = await manager.configure(
        instance["id"], {"label": "secret-label"}, {"token": "s3cret"}, instance["revision"]
    )
    bound_config = await manager.bindings(
        configured["id"], [bound("workspace-binding", configured["id"])], configured["revision"]
    )
    enabled = await manager.state(bound_config["id"], True, bound_config["revision"])
    assert enabled["state"] == "ready"
    assert (await host.call(instance["id"], "value", "call-before"))["result"] == {
        "text": "configured"
    }
    # A persisted timer is plugin-owned scheduling data and must stop with the purge.
    schedules_before = [
        item
        for item in await manager.local(manager.repository.list, "schedules")
        if item["instance_id"] == instance["id"]
    ]
    assert schedules_before == [] or all(item.get("enabled") for item in schedules_before)
    result = await manager.uninstall("fixture.purged", purge_data=True)
    assert result["instance_data"] == "purged"
    purged = await manager.instance(instance["id"])
    assert purged["config"] == {}
    assert purged["bindings"] == []
    assert "data_purged_at" in purged
    assert await manager.local(manager.vault.names, instance["id"]) == {}
    schedules = [
        item
        for item in await manager.local(manager.repository.list, "schedules")
        if item["instance_id"] == instance["id"]
    ]
    # No schedule may keep ticking; a row that exists must be disabled.
    assert all(item["enabled"] is False for item in schedules)
    # Reinstall keeps the instance identity but cannot resurrect cleared values.
    again = await host.install(
        configured_package(tmp_path / "cfg2", "fixture.purged", "1.0.0"), "install-purged-again"
    )
    assert again == first
    restored = await manager.create(again, "Purged", "instance-purged")
    assert restored["id"] == instance["id"]
    assert restored["config"] == {}
    assert restored["bindings"] == []
    assert restored["secret_configured"] == {"token": False}
    assert enabled["id"] == instance["id"]
    # Original execution evidence is preserved, not treated as plugin data.
    calls = await manager.local(manager.repository.calls, instance["id"])
    succeeded = [item for item in calls if item["execution"] == "succeeded"]
    assert succeeded and succeeded[0]["result"] == {"text": "configured"}
    assert succeeded[0]["id"] in {item["id"] for item in calls}
    await manager.close()


@pytest.mark.asyncio
async def test_retained_uninstall_keeps_configuration_for_reinstall(lifecycle_database, tmp_path):
    await retained_config_uninstall_scenario(Host(lifecycle_database, tmp_path), tmp_path)


@pytest.mark.asyncio
async def test_purge_uninstall_clears_configuration_and_other_plugin_state(lifecycle_database, tmp_path):
    await purged_config_uninstall_scenario(Host(lifecycle_database, tmp_path), tmp_path)


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


def failing_start_source(message="fixture refuses to start"):
    return (
        "from tianshu_plugin import Plugin, PluginError, action, Invocation\n"
        "\n"
        "class Fixture(Plugin):\n"
        "    async def start(self):\n"
        '        raise PluginError("plugin_start_failed", '
        + repr(message)
        + ', effect="none")\n'
        "\n"
        '    @action(id="value", title="value",\n'
        '        input_schema={"type": "object", "properties": {}},\n'
        '        output_schema={"type": "object", "properties": {"text": {"type": "string"}}})\n'
        "    async def value(self, call: Invocation, args):\n"
        '        return {"text": "never"}\n'
    )


async def upgrade_start_failure_scenario(host, tmp_path):
    """The staged child starts, then its start hook fails: no success may remain."""
    manager = await host.open()
    good = await host.install(package(tmp_path / "good", "fixture.start", "1.0.0", "ok"), "install-good")
    instance = await host.enable(good, "Start", "instance-start")
    assert (await host.call(instance["id"], "value", "call-good"))["result"] == {"text": "ok"}
    await host.install(
        package(
            tmp_path / "bad",
            "fixture.start",
            "2.0.0",
            "never",
            source=failing_start_source(),
        ),
        "install-bad",
    )
    current = await manager.instance(instance["id"])
    with pytest.raises(AppError):
        await manager.upgrade(instance["id"], "2.0.0", current["revision"])
    operations = [
        item
        for item in await manager.local(manager.repository.list, "operations")
        if item.get("kind") == "upgrade"
    ]
    assert operations[-1]["execution"] == "failed"
    assert operations[-1]["error"]["code"] == "plugin_start_failed"
    assert operations[-1]["error"]["rolled_back"] is True
    assert operations[-1]["error"]["restored_version"] == "1.0.0"
    assert operations[-1]["error"]["failed_version"] == "2.0.0"
    assert not [item for item in operations if item["execution"] == "succeeded"]
    restored = await manager.instance(instance["id"])
    assert restored["version"] == "1.0.0" and restored["revision"] == current["revision"]
    # The recovered old version serves the accepted call.
    assert (await host.call(instance["id"], "value", "call-restored"))["result"] == {"text": "ok"}
    await manager.close()


async def upgrade_restore_failure_scenario(host, tmp_path):
    """Rollback itself fails: reported as not rolled back, original cause kept."""
    manager = await host.open()
    marker = tmp_path / "old-version-broken"
    old_source = f'''
import asyncio
from pathlib import Path
from tianshu_plugin import Plugin, PluginError, action, Invocation
MARKER = Path({str(marker)!r})

class Fixture(Plugin):
    async def start(self):
        if MARKER.exists():
            raise PluginError("plugin_start_failed", "old version can no longer start", effect="none")

    @action(id="value", title="value",
        input_schema={{"type": "object", "properties": {{}}}},
        output_schema={{"type": "object", "properties": {{"text": {{"type": "string"}}}}}})
    async def value(self, call: Invocation, args):
        return {{"text": "old"}}
'''
    old = await host.install(
        package(tmp_path / "old", "fixture.unrestorable", "1.0.0", "old", source=old_source),
        "install-old",
    )
    instance = await host.enable(old, "Restore", "instance-restore")
    assert (await host.call(instance["id"], "value", "call-old"))["result"] == {"text": "old"}
    await host.install(
        package(
            tmp_path / "v2",
            "fixture.unrestorable",
            "2.0.0",
            "two",
            source=failing_start_source("new version refuses to start"),
        ),
        "install-v2",
    )
    # From now on the previous version cannot start either; the switch's
    # rollback therefore has no continuity to claim.
    marker.write_text("broken", encoding="utf-8")
    current = await manager.instance(instance["id"])
    with pytest.raises(AppError):
        await manager.upgrade(instance["id"], "2.0.0", current["revision"])
    operations = [
        item
        for item in await manager.local(manager.repository.list, "operations")
        if item.get("kind") == "upgrade"
    ]
    assert operations[-1]["execution"] == "failed"
    assert operations[-1]["error"]["code"] == "plugin_start_failed"
    assert operations[-1]["error"]["failed_version"] == "2.0.0"
    assert operations[-1]["error"]["rolled_back"] is False
    assert operations[-1]["error"]["restored_version"] is None
    assert operations[-1]["error"]["restore_error"] == "plugin_start_failed"
    stored = await manager.instance(instance["id"])
    assert stored["version"] == "1.0.0"
    await manager.close()


async def accepted_call_is_settled_before_the_version_switch(host, tmp_path):
    """A call accepted but not yet executing must not restart the old version."""
    manager = await host.open()
    old = await host.install(package(tmp_path / "old", "fixture.queued", "1.0.0", "old"), "install-old")
    instance = await host.enable(old, "Queued", "instance-queued")
    revision = (await manager.instance(instance["id"]))["revision"]
    await host.install(package(tmp_path / "new", "fixture.queued", "2.0.0", "new"), "install-new")

    # Accept a call against the previous version without letting it execute.
    from tianshu.capabilities.execution_context import CURRENT_ACTION
    from tianshu.types import Action, OriginKind

    trusted = Action(
        instance["id"] + ":queued", "queued-intent",
        "plugin:" + instance["id"] + "::value", None,
        TrustedContext("owner", None, None, None, ("system",), ("system",), 1, OriginKind.HUMAN),
        {}, OriginKind.HUMAN,
    )
    token = CURRENT_ACTION.set(trusted)
    try:
        accepted = await manager.executions.admit(
            instance["id"], "value", {}, "queued-intent", "owner", defer=True
        )
    finally:
        CURRENT_ACTION.reset(token)
    assert accepted["execution"] == "accepted"
    await manager.upgrade(instance["id"], "2.0.0", revision)
    settled = await manager.local(manager.repository.call, accepted["id"])
    assert settled["execution"] == "interrupted"
    assert settled["effect"] == "none"
    assert settled["error"]["code"] == "instance_upgraded"
    assert settled["error"]["retriable"] is False
    # The switch stands: the old package never runs again, the new one serves.
    assert (await manager.instance(instance["id"]))["version"] == "2.0.0"
    assert (await host.call(instance["id"], "value", "call-after"))["result"] == {"text": "new"}
    await manager.close()


@pytest.mark.asyncio
async def test_upgrade_start_failure_never_records_success(lifecycle_database, tmp_path):
    await upgrade_start_failure_scenario(Host(lifecycle_database, tmp_path), tmp_path)


@pytest.mark.asyncio
async def test_upgrade_restore_failure_is_reported_without_false_continuity(lifecycle_database, tmp_path):
    await upgrade_restore_failure_scenario(Host(lifecycle_database, tmp_path), tmp_path)


@pytest.mark.asyncio
async def test_accepted_call_is_settled_before_the_version_switch(lifecycle_database, tmp_path):
    await accepted_call_is_settled_before_the_version_switch(Host(lifecycle_database, tmp_path), tmp_path)



async def uninstall_concurrency_scenario(host, tmp_path):
    """Uninstall holds the package lock: a racing enable cannot resurrect it."""
    manager = await host.open()
    package_id = await host.install(
        package(tmp_path / "race", "fixture.race", "1.0.0", "race"), "install-race"
    )
    instance = await host.enable(package_id, "Race", "instance-race")
    assert instance["state"] == "ready"
    # An unrelated plugin must keep serving throughout the removal.
    bystander_package = await host.install(
        package(tmp_path / "bystander", "fixture.bystander", "1.0.0", "bystander"),
        "install-bystander",
    )
    bystander = await host.enable(bystander_package, "Bystander", "instance-bystander")
    bystander_runtime = generation(manager, bystander["id"])
    revision = (await manager.instance(instance["id"]))["revision"]

    started, release = asyncio.Event(), asyncio.Event()
    original_disable = manager._disable

    async def observed_disable(identifier, expected):
        started.set()
        await release.wait()
        return await original_disable(identifier, expected)

    manager._disable = observed_disable
    unloading = asyncio.create_task(manager.uninstall("fixture.race"))
    await started.wait()

    # Deterministic barrier: enable is issued while the uninstall is mid-flight.
    enabling = asyncio.create_task(manager.state(instance["id"], True, revision))
    await asyncio.sleep(0)
    assert not enabling.done(), "enable must not slip into the middle of an uninstall"
    release.set()
    result = await unloading
    assert result["uninstalled"] is True
    manager._disable = original_disable

    # The blocked enable was refused; the instance is disabled and has no process.
    outcome = await asyncio.gather(enabling, return_exceptions=True)
    assert isinstance(outcome[0], BaseException)
    assert instance["id"] not in manager.workers
    stored = await manager.instance(instance["id"])
    assert stored["desired_state"] == "disabled"
    assert stored["revision"] >= revision + 1
    # No live entry may remain for the removed instance.
    assert not [
        key
        for key in manager.domain_ports.catalog.handlers
        if key.startswith("plugin:" + instance["id"] + ":")
    ]
    # A repeated uninstall does not delete twice and reports already_uninstalled.
    again = await manager.uninstall("fixture.race")
    assert again["already_uninstalled"] is True and again["reclaimed"] == []
    # The other plugin never lost its process and still answers.
    assert generation(manager, bystander["id"]) == bystander_runtime
    assert (await host.call(bystander["id"], "value", "bystander-after"))["result"] == {
        "text": "bystander"
    }
    await manager.close()


@pytest.mark.asyncio
async def test_uninstall_is_mutually_exclusive_with_enable(lifecycle_database, tmp_path):
    await uninstall_concurrency_scenario(Host(lifecycle_database, tmp_path), tmp_path)


async def install_race_scenario(host, tmp_path):
    """A concurrent install of this package cannot outrun its removal."""
    manager = await host.open()
    first = await host.install(
        package(tmp_path / "p", "fixture.installrace", "1.0.0", "one"), "install-one"
    )
    instance = await host.enable(first, "Race", "instance-install")
    source = package(tmp_path / "p2", "fixture.installrace", "1.0.0", "one")
    started, release = asyncio.Event(), asyncio.Event()
    original_disable = manager._disable

    async def observed_disable(identifier, expected):
        started.set()
        await release.wait()
        return await original_disable(identifier, expected)

    manager._disable = observed_disable
    unloading = asyncio.create_task(manager.uninstall("fixture.installrace"))
    await started.wait()
    # The reinstall is issued mid-removal; the install lock and package lock
    # must serialise it behind the removal rather than prepare into a
    # directory that is about to be reclaimed.
    reinstalling = asyncio.create_task(manager.install(source, "install-again", wait=True))
    await asyncio.sleep(0)
    release.set()
    result = await unloading
    assert result["uninstalled"] is True
    manager._disable = original_disable
    operation = await reinstalling
    assert operation["execution"] == "succeeded"
    # The freshly prepared package is readable again and can be enabled.
    package_document = await manager.local(
        manager.repository.get, "packages", "fixture.installrace@1.0.0"
    )
    assert package_document is not None and package_document["installed"] is True
    again = await manager.create("fixture.installrace@1.0.0", "Race", "instance-install")
    assert again["id"] == instance["id"]
    ready = await manager.state(instance["id"], True, again["revision"])
    assert ready["state"] == "ready"
    assert (await host.call(instance["id"], "value", "install-race"))["result"] == {"text": "one"}
    await manager.close()


@pytest.mark.asyncio
async def test_uninstall_serialises_with_a_concurrent_reinstall(lifecycle_database, tmp_path):
    await install_race_scenario(Host(lifecycle_database, tmp_path), tmp_path)


async def ledger_conflict_after_handover_scenario(host, tmp_path):
    """A ledger conflict cannot leave the new version serving behind a failed switch."""
    manager = await host.open()
    old = await host.install(
        package(tmp_path / "old", "fixture.handover", "1.0.0", "old"), "install-old"
    )
    instance = await host.enable(old, "Handover", "instance-handover")
    await host.install(
        package(tmp_path / "new", "fixture.handover", "2.0.0", "new"), "install-new"
    )
    revision = (await manager.instance(instance["id"]))["revision"]
    # Make the final ledger write conflict after the new child already took
    # over, as a concurrent revision bump would.
    original = manager.repository.put

    def conflicting(kind, document, *args, **kwargs):
        if kind == "instances" and document.get("version") == "2.0.0":
            raise Fault("revision_conflict", 409)
        return original(kind, document, *args, **kwargs)

    manager.repository.put = conflicting
    with pytest.raises(AppError) as failure:
        await manager.upgrade(instance["id"], "2.0.0", revision)
    manager.repository.put = original
    # The switch is never recorded as succeeded, whatever the stage.
    assert failure.value.details["execution"] == "failed"
    assert failure.value.code == "revision_conflict"
    recorded = failure.value.details["error"]
    assert recorded["failed_version"] == "2.0.0"
    # Recovery restored the previous version, so continuity is claimed truthfully.
    assert recorded["rolled_back"] is True
    assert recorded["restored_version"] == "1.0.0"
    assert recorded["restore_error"] is None
    # Ledger and live process describe the same version again.
    stored = await manager.instance(instance["id"])
    assert stored["version"] == "1.0.0"
    served = generation(manager, instance["id"])
    assert served is not None
    assert (await host.call(instance["id"], "value", "after-conflict"))["result"] == {
        "text": "old"
    }
    successes = [
        item
        for item in await manager.local(manager.repository.list, "operations")
        if item.get("kind") == "upgrade" and item["execution"] == "succeeded"
    ]
    assert successes == []
    await manager.close()


@pytest.mark.asyncio
async def test_ledger_conflict_after_handover_is_never_recorded_as_success(
    lifecycle_database, tmp_path
):
    await ledger_conflict_after_handover_scenario(
        Host(lifecycle_database, tmp_path), tmp_path
    )


def stateful_source(version, *, failing_start=False):
    """A plugin that keeps and reuses the context its startup obtained."""
    lines = [
        "import asyncio",
        "from tianshu_plugin import Plugin, PluginError, action, Invocation",
        "STARTS = []",
        "",
        "",
        "async def write(ctx, key, value):",
        "    current = await ctx.state.get(key)",
        "    await ctx.state.compare_and_set(",
        "        key, value, expected_revision=current.revision if current else None",
        "    )",
        "",
        "",
        "class Fixture(Plugin):",
        "    async def initialize(self):",
        '        await write(self.ctx, "boot", {"version": "' + version + '"})',
        "",
        "    async def start(self):",
    ]
    if failing_start:
        lines += [
            '        STARTS.append("start-raised")',
            '        raise PluginError("plugin_start_failed", "refuses to start", effect="none")',
        ]
    else:
        lines += [
            '        STARTS.append("started")',
            '        await write(self.ctx, "started", {"version": "' + version + '"})',
        ]
    lines += [
        "",
        '    @action(id="value", title="value",',
        '        input_schema={"type": "object", "properties": {}},',
        '        output_schema={"type": "object", "properties": {"text": {"type": "string"}}})',
        "    async def value(self, call: Invocation, args):",
        '        return {"text": "' + version + '"}',
        "",
        '    @action(id="stored", title="stored",',
        '        input_schema={"type": "object", "properties": {}},',
        '        output_schema={"type": "object", "properties": {}})',
        "    async def stored(self, call: Invocation, args):",
        "        # Deliberately the startup handle, not this invocation's handle.",
        '        boot = await self._maintenance.state.get("boot")',
        '        started = await self._maintenance.state.get("started")',
        '        return {"boot": boot.value if boot else None,',
        '                "started": started.value if started else None,',
        '                "starts": len(STARTS)}',
    ]
    return "\n".join(lines) + "\n"


async def context_handover_scenario(host, tmp_path):
    """A startup context survives the handover and dies with its own child."""
    manager = await host.open()
    old = await host.install(
        package(
            tmp_path / "old", "fixture.context", "1.0.0", "old",
            source=stateful_source("1.0.0"),
            capabilities=[{"id": "state.kv", "major": 1}],
        ),
        "install-context-old",
    )
    instance = await host.enable(old, "Context", "instance-context")
    assert instance["state"] == "ready"
    first = await host.call(instance["id"], "stored", "stored-first")
    # initialize and start both used the startup context, and it stays usable.
    assert first["result"] == {
        "boot": {"version": "1.0.0"},
        "started": {"version": "1.0.0"},
        "starts": 1,
    }
    served = generation(manager, instance["id"])
    old_generation = served["generation"]
    assert [
        reference
        for reference, context in manager.executions.contexts.items()
        if context["instance"] == instance["id"]
    ], "the startup handle stays recorded for this instance"

    await host.install(
        package(
            tmp_path / "new", "fixture.context", "2.0.0", "new",
            source=stateful_source("2.0.0"),
            capabilities=[{"id": "state.kv", "major": 1}],
        ),
        "install-context-new",
    )
    current = await manager.instance(instance["id"])
    upgraded = await manager.upgrade(instance["id"], "2.0.0", current["revision"])
    assert upgraded["version"] == "2.0.0" and upgraded["state"] == "ready"
    serving = generation(manager, instance["id"])
    assert serving["generation"] != old_generation
    # B is the very child that passed its own start: it started exactly once
    # and still uses the context it obtained before the handover.
    after = await host.call(instance["id"], "stored", "stored-second")
    # The direct defect: without the fix this call fails with permission_denied
    # because the handover revoked the context the new child already had.
    assert after["execution"] == "succeeded", after.get("error")
    assert after["result"]["starts"] == 1
    assert after["result"]["boot"] == {"version": "2.0.0"}
    assert after["result"]["started"] == {"version": "2.0.0"}
    # A's handles are gone; B's handle is what remains.
    assert not [
        reference
        for reference, context in manager.executions.contexts.items()
        if context["instance"] == instance["id"]
        and context.get("generation") == old_generation
    ]
    retained = [
        reference
        for reference, context in manager.executions.contexts.items()
        if context["instance"] == instance["id"]
        and context.get("generation") == serving["generation"]
    ]
    assert retained, "the serving child keeps the handle it obtained"
    # A late callback attributed to A cannot use B's handle...
    with pytest.raises(Fault) as denied:
        await manager.executions.callback(
            instance["id"],
            "host.capability",
            {
                "context_ref": retained[0],
                "capability": "state.kv",
                "major": 1,
                "operation": "get",
                "input": {"key": "boot"},
            },
            generation=old_generation,
        )
    assert denied.value.code == "permission_denied"
    # ...while the serving child keeps full use of it.
    assert await manager.executions.callback(
        instance["id"],
        "host.capability",
        {
            "context_ref": retained[0],
            "capability": "state.kv",
            "major": 1,
            "operation": "get",
            "input": {"key": "boot"},
        },
        generation=serving["generation"],
    ) == {"value": {"version": "2.0.0"}, "revision": 2}
    await manager.close()


async def context_restore_scenario(host, tmp_path):
    """A failed candidate's handles die before the previous version is restored."""
    manager = await host.open()
    old = await host.install(
        package(
            tmp_path / "old", "fixture.contextfail", "1.0.0", "old",
            source=stateful_source("1.0.0"),
            capabilities=[{"id": "state.kv", "major": 1}],
        ),
        "install-context-old",
    )
    instance = await host.enable(old, "Context", "instance-context-fail")
    assert (await host.call(instance["id"], "value", "call-old"))["result"] == {"text": "1.0.0"}
    await host.install(
        package(
            tmp_path / "broken", "fixture.contextfail", "2.0.0", "broken",
            source=stateful_source("2.0.0", failing_start=True),
            capabilities=[{"id": "state.kv", "major": 1}],
        ),
        "install-context-broken",
    )
    current = await manager.instance(instance["id"])
    with pytest.raises(AppError) as failure:
        await manager.upgrade(instance["id"], "2.0.0", current["revision"])
    assert failure.value.details["error"]["rolled_back"] is True
    restored = generation(manager, instance["id"])
    # Only the restored child's handles remain: the candidate's are all revoked.
    remaining = {
        context.get("generation")
        for context in manager.executions.contexts.values()
        if context["instance"] == instance["id"]
    }
    assert remaining <= {restored["generation"]}
    # The restored version works again and reads its own startup state.
    assert (await host.call(instance["id"], "value", "call-restored"))["result"] == {
        "text": "1.0.0"
    }
    stored = await host.call(instance["id"], "stored", "stored-restored")
    assert stored["result"]["boot"] == {"version": "1.0.0"}
    assert stored["result"]["started"] == {"version": "1.0.0"}
    await manager.close()


async def same_package_install_during_uninstall_scenario(host, tmp_path):
    """No same-plugin version may publish while that plugin's uninstall runs."""
    manager = await host.open()
    first = await host.install(
        package(tmp_path / "one", "fixture.same", "1.0.0", "one"), "install-one"
    )
    instance = await host.enable(first, "Same", "instance-same")
    assert instance["state"] == "ready"
    source = package(tmp_path / "two", "fixture.same", "2.0.0", "two")

    started, release = asyncio.Event(), asyncio.Event()
    original_disable = manager._disable
    original_put = manager.repository.put
    publishing, guard = [], {"open": False}

    def observed_put(kind, document, *args, **kwargs):
        # Only a publish makes a version visible; the removal writes the same
        # rows as installed=False and must not be counted.
        if kind == "packages" and guard["open"] and document.get("installed"):
            publishing.append(document["id"])
        return original_put(kind, document, *args, **kwargs)

    async def observed_disable(identifier, expected):
        started.set()
        await release.wait()
        return await original_disable(identifier, expected)

    manager.repository.put = observed_put
    manager._disable = observed_disable
    unloading = asyncio.create_task(manager.uninstall("fixture.same"))
    await started.wait()
    # From here the same plugin's removal is mid-flight.
    guard["open"] = True
    installing = asyncio.create_task(manager.install(source, "install-two", wait=True))
    await asyncio.sleep(0)
    await asyncio.sleep(0.05)
    release.set()
    removed = await unloading
    # The publish may only happen once the removal has finished.
    guard["open"] = False
    manager._disable = original_disable
    manager.repository.put = original_put
    assert removed["uninstalled"] is True
    assert publishing == [], f"a same-plugin version published during uninstall: {publishing}"
    operation = await installing
    assert operation["execution"] == "succeeded", operation.get("error")
    # The published package is complete, and the removal only covered what it
    # listed before the publish: the old version is gone from ledger and disk.
    stored = await manager.local(manager.repository.get, "packages", "fixture.same@2.0.0")
    assert stored is not None and stored["installed"] is True
    assert Path(stored["root"]).is_dir()
    old = await manager.local(manager.repository.get, "packages", "fixture.same@1.0.0")
    assert old["installed"] is False
    assert not Path(old["root"]).exists()
    fresh = await manager.create("fixture.same@2.0.0", "Same two", "instance-same-two")
    ready = await manager.state(fresh["id"], True, fresh["revision"])
    assert ready["state"] == "ready" and ready["version"] == "2.0.0"
    assert (await host.call(fresh["id"], "value", "same-after"))["result"] == {"text": "two"}
    # Whatever remains installed must really exist on disk.
    for package_document in await manager.local(manager.repository.list, "packages"):
        if package_document["installed"]:
            assert Path(package_document["root"]).is_dir(), package_document["id"]
    await manager.close()


async def uninstall_during_install_prepare_scenario(host, tmp_path):
    """A prepare and a same-plugin removal cannot delete the files being published."""
    manager = await host.open()
    first = await host.install(
        package(tmp_path / "one", "fixture.prepare", "1.0.0", "same"), "install-one"
    )
    instance = await host.enable(first, "Prepare", "instance-prepare")
    assert instance["state"] == "ready"
    # Identical content: the reinstall reuses the very directory it would lose.
    source = package(tmp_path / "again", "fixture.prepare", "1.0.0", "same")

    import threading

    staged, release = threading.Event(), threading.Event()
    original_prepare = manager.packages.prepare

    def observed_prepare(origin):
        staged.set()
        release.wait(60)
        return original_prepare(origin)

    original_put = manager.repository.put
    publishing = []
    guard = {"open": False}

    def observed_put(kind, document, *args, **kwargs):
        if kind == "packages" and guard["open"] and document.get("installed"):
            publishing.append(document["id"])
        return original_put(kind, document, *args, **kwargs)

    manager.repository.put = observed_put
    manager.packages.prepare = observed_prepare
    installing = asyncio.create_task(
        manager.install(source, "install-again", wait=True)
    )
    assert await asyncio.to_thread(staged.wait, 60)
    guard["open"] = True
    removing = asyncio.create_task(manager.uninstall("fixture.prepare"))
    await asyncio.sleep(0)
    await asyncio.sleep(0.05)
    release.set()
    removed = await removing
    guard["open"] = False
    manager.packages.prepare = original_prepare
    manager.repository.put = original_put
    assert removed["uninstalled"] is True
    # The publish waited for the removal, so the directory it reuses survived it.
    assert publishing == [], f"published while the removal was running: {publishing}"
    operation = await installing
    # The reinstall either completed with real files, or failed cleanly; it can
    # never be an installed package whose directory the removal took away.
    stored = await manager.local(manager.repository.get, "packages", "fixture.prepare@1.0.0")
    if operation["execution"] == "succeeded":
        assert stored["installed"] is True
        assert Path(stored["root"]).is_dir(), "an installed package lost its files"
    for package_document in await manager.local(manager.repository.list, "packages"):
        if package_document["installed"]:
            assert Path(package_document["root"]).is_dir(), package_document["id"]
    await manager.close()


@pytest.mark.asyncio
async def test_startup_context_survives_the_upgrade_handover(lifecycle_database, tmp_path):
    await context_handover_scenario(Host(lifecycle_database, tmp_path), tmp_path)


@pytest.mark.asyncio
async def test_failed_candidate_context_is_revoked_before_restore(lifecycle_database, tmp_path):
    await context_restore_scenario(Host(lifecycle_database, tmp_path), tmp_path)


@pytest.mark.asyncio
async def test_same_package_install_serialises_behind_uninstall(lifecycle_database, tmp_path):
    await same_package_install_during_uninstall_scenario(
        Host(lifecycle_database, tmp_path), tmp_path
    )


@pytest.mark.asyncio
async def test_uninstall_during_install_prepare_keeps_records_coherent(lifecycle_database, tmp_path):
    await uninstall_during_install_prepare_scenario(
        Host(lifecycle_database, tmp_path), tmp_path
    )


async def abandoned_candidate_exit_scenario(host, tmp_path):
    """The failed candidate really exits before the snapshot and old child return."""
    manager = await host.open()
    old = await host.install(
        package(
            tmp_path / "old", "fixture.abandoned", "1.0.0", "old",
            source=stateful_source("1.0.0"),
            capabilities=[{"id": "state.kv", "major": 1}],
        ),
        "install-abandoned-old",
    )
    instance = await host.enable(old, "Abandoned", "instance-abandoned")
    assert (await host.call(instance["id"], "value", "old-call"))["result"] == {"text": "1.0.0"}
    await host.install(
        package(
            tmp_path / "broken", "fixture.abandoned", "2.0.0", "broken",
            source=stateful_source("2.0.0", failing_start=True),
            capabilities=[{"id": "state.kv", "major": 1}],
        ),
        "install-abandoned-broken",
    )

    # Observation only: the snapshot restore and the old child's start still run
    # for real, so the assertions are OS-level facts about the real processes.
    observed = {"children": [], "restore": None, "revive": None, "revive_pid": None}
    original_child, original_restore = manager.child, manager.repository.restore
    original_worker = manager.worker

    def observing_child(package, identifier):
        process = original_child(package, identifier)
        observed["children"].append(process)
        return process

    def observing_restore(identifier_arg, snapshot):
        # A raw OS fact: the child's own return code, not a model-level flag.
        observed["restore"] = [
            member.process.returncode is not None for member in observed["children"]
        ]
        return original_restore(identifier_arg, snapshot)

    def observing_worker(instance_arg, *, prepared=None):
        if prepared is None and observed["revive"] is None:
            observed["revive"] = [
                member.process.returncode is not None for member in observed["children"]
            ]
            observed["revive_pid"] = manager.workers.get(instance_arg["id"])
        return original_worker(instance_arg, prepared=prepared)

    manager.child = observing_child
    manager.repository.restore = observing_restore
    manager.worker = observing_worker
    current = await manager.instance(instance["id"])
    try:
        with pytest.raises(AppError) as failure:
            await manager.upgrade(instance["id"], "2.0.0", current["revision"])
    finally:
        manager.child = original_child
        manager.repository.restore = original_restore
        manager.worker = original_worker

    # Children created after this hook: the staged candidate of the failed switch,
    # then the restored 1.0.0 child that recovery starts afterwards.
    assert len(observed["children"]) == 2, [m.runtime_id for m in observed["children"]]
    candidate = observed["children"][0]
    assert observed["restore"] == [True], (
        "recovery reached the snapshot while the failed candidate was still running"
    )
    assert observed["revive"] == [True], (
        "the previous version restarted while the failed candidate was still running"
    )
    assert candidate.process.returncode is not None
    recorded = failure.value.details["error"]
    assert recorded["failed_version"] == "2.0.0"
    assert recorded["candidate_closed"] is True
    assert recorded["candidate_error"] is None
    assert recorded["rolled_back"] is True
    assert recorded["restored_version"] == "1.0.0"
    # The failed candidate's handles are gone, and only the restored child's stay.
    restored = generation(manager, instance["id"])
    remaining = {
        context.get("generation")
        for context in manager.executions.contexts.values()
        if context["instance"] == instance["id"]
    }
    assert candidate.runtime_id not in remaining
    assert remaining <= {restored["generation"]}
    # The restored version serves again, using its own startup context.
    assert (await host.call(instance["id"], "value", "restored-call"))["result"] == {
        "text": "1.0.0"
    }
    stored = await host.call(instance["id"], "stored", "restored-state")
    assert stored["result"]["boot"] == {"version": "1.0.0"}
    await manager.close()


async def abandoned_candidate_cleanup_spares_replacement_scenario(host, tmp_path):
    """Stopping an unregistered candidate never touches a serving replacement."""
    manager = await host.open()
    first = await host.install(
        package(tmp_path / "one", "fixture.spare", "1.0.0", "one"), "install-spare-one"
    )
    instance = await host.enable(first, "Spare", "instance-spare")
    assert instance["state"] == "ready"
    serving = generation(manager, instance["id"])
    replacement = await manager.worker(await manager.instance(instance["id"]))
    assert replacement.runtime_id == serving["generation"]
    # A candidate that was never registered against this instance.
    stray = manager.child(
        await manager.package(instance["package_id"], instance["version"]), instance["id"]
    )
    await stray.start()
    handles_before = {
        reference
        for reference, context in manager.executions.contexts.items()
        if context["instance"] == instance["id"]
    }
    await manager.stop_worker(instance["id"], "candidate_discarded", stray)
    assert stray.process.returncode is not None
    # The replacement keeps running with the very same generation and handles.
    assert manager.workers[instance["id"]][0].process.returncode is None
    assert manager.workers[instance["id"]][0].runtime_id == serving["generation"]
    assert [
        reference
        for reference, context in manager.executions.contexts.items()
        if context["instance"] == instance["id"]
    ] == list(handles_before)
    assert (await host.call(instance["id"], "value", "spare-after"))["result"] == {"text": "one"}
    # Repeated cleanup of the same candidate stays safe.
    await manager.stop_worker(instance["id"], "candidate_discarded_again", stray)
    assert stray.process.returncode is not None
    assert manager.workers[instance["id"]][0].runtime_id == serving["generation"]
    await manager.close()


@pytest.mark.asyncio
async def test_abandoned_candidate_exits_before_the_previous_version_returns(
    lifecycle_database, tmp_path
):
    await abandoned_candidate_exit_scenario(Host(lifecycle_database, tmp_path), tmp_path)


@pytest.mark.asyncio
async def test_candidate_cleanup_spares_a_serving_replacement(lifecycle_database, tmp_path):
    await abandoned_candidate_cleanup_spares_replacement_scenario(
        Host(lifecycle_database, tmp_path), tmp_path
    )


async def blocked_candidate_scenario(host, tmp_path, mode):
    """A failed candidate that never exits must block every recovery path.

    mode="raises": the closing call throws OSError and the child keeps running.
    mode="silent": the closing call returns normally while the child still runs,
    which is what an implementation checking only "close returned" would accept.
    """
    manager = await host.open()
    old = await host.install(
        package(
            tmp_path / "old", "fixture.blocked", "1.0.0", "old",
            source=stateful_source("1.0.0"),
            capabilities=[{"id": "state.kv", "major": 1}],
        ),
        "install-blocked-old",
    )
    instance = await host.enable(old, "Blocked", "instance-blocked")
    await host.install(
        package(
            tmp_path / "broken", "fixture.blocked", "2.0.0", "broken",
            source=stateful_source("2.0.0", failing_start=True),
            capabilities=[{"id": "state.kv", "major": 1}],
        ),
        "install-blocked-broken",
    )
    other_package = await host.install(
        package(tmp_path / "other", "fixture.blocked.other", "1.0.0", "other"),
        "install-blocked-other",
    )
    other = await host.enable(other_package, "Other", "instance-blocked-other")

    observed = {
        "restores": [],
        "candidate": None,
        "revived": [],
        "closed": 0,
        "restore_exit": [],
        "revive_exit": [],
        "failed": None,
    }
    original_restore = manager.repository.restore
    original_child = manager.child
    original_worker = manager.worker

    def exit_fact():
        failed = observed["failed"]
        return None if failed is None else failed.process.returncode is not None

    def observing_restore(identifier, snapshot):
        observed["restores"].append(identifier)
        # Faithful ordering proof: the snapshot must only be restored after the
        # failed candidate's OS process has already exited.
        observed["restore_exit"].append(exit_fact())
        return original_restore(identifier, snapshot)

    def observing_child(package_document, identifier):
        process = original_child(package_document, identifier)
        if identifier == instance["id"]:
            observed["candidate"] = process
        return process

    def observing_worker(instance_document, *, prepared=None):
        # Only the instance under test is observed; the bystander's own start is
        # not part of this ordering proof.
        if prepared is None and instance_document["id"] == instance["id"]:
            observed["revived"].append(instance_document["id"])
            observed["revive_exit"].append(exit_fact())
        return original_worker(instance_document, prepared=prepared)

    async def refusing_close(_reason="stopped"):
        observed["closed"] += 1
        raise OSError("simulated close failure")

    async def silently_stuck_close(_reason="stopped"):
        observed["closed"] += 1

    # The stub is installed the moment the candidate child is created, before it
    # is ever closed, so the failing close is exercised inside the upgrade itself.
    # It is armed only for the staged candidate, never for a later restored child.
    armed = {"on": False}

    def installing_child(package_document, identifier):
        process = observing_child(package_document, identifier)
        if armed["on"] and identifier == instance["id"]:
            armed["on"] = False
            observed["failed"] = process
            observed["original_close"] = process.close
            process.close = refusing_close if mode == "raises" else silently_stuck_close
        return process

    manager.repository.restore = observing_restore
    manager.child = installing_child
    manager.worker = observing_worker
    current = await manager.instance(instance["id"])
    armed["on"] = True
    with pytest.raises(AppError) as failure:
        await manager.upgrade(instance["id"], "2.0.0", current["revision"])
    armed["on"] = False
    candidate = observed["candidate"]
    assert candidate is not None and candidate.process.returncode is None, (
        "the fixture must leave the candidate really running"
    )
    # Behaviour first: no snapshot may be restored and no previous version may be
    # started while the failed candidate is still a live OS process.
    assert instance["id"] not in observed["restores"], (
        "recovery restored the snapshot while the failed candidate was still alive"
    )
    assert instance["id"] not in observed["revived"], (
        "recovery restarted the previous version while the failed candidate was still alive"
    )
    assert manager.admission.get(instance["id"]) == "instance_cleanup_pending"
    recorded = failure.value.details["error"]
    assert recorded["code"] == "plugin_start_failed"
    assert recorded["failed_version"] == "2.0.0"
    assert recorded["rolled_back"] is False
    assert recorded["restored_version"] is None
    assert recorded["candidate_closed"] is False
    assert recorded["restore_state"] == "blocked_candidate_alive"
    assert recorded["candidate_error"]
    operations = await manager.local(manager.repository.list, "operations")
    stored = [
        operation
        for operation in operations
        if operation.get("kind") == "upgrade" and operation.get("instance_id") == instance["id"]
    ][-1]
    assert stored["execution"] != "succeeded"
    assert stored["error"]["rolled_back"] is False
    assert stored["error"]["candidate_closed"] is False
    assert stored["error"]["candidate_pid"] is not None

    # Ordinary calls cannot reach the old version through worker() either.
    with pytest.raises(Fault) as refused:
        await manager.executions.invoke(instance["id"], "value", {}, "blocked-call", "owner")
    assert refused.value.code == "instance_cleanup_pending"
    # Reload, enable, configuration and bindings refuse through the same gate.
    before = await manager.instance(instance["id"])
    for attempt in (
        lambda: manager.restart(instance["id"], before["revision"]),
        lambda: manager.state(instance["id"], False, before["revision"]),
        lambda: manager.configure(instance["id"], before["config"], {}, before["revision"]),
    ):
        with pytest.raises(Fault) as entry:
            await attempt()
        assert entry.value.code == "instance_cleanup_pending"
    # A later entry point keeps refusing through the same gate: the block is not
    # a one-shot that the next request could bypass.
    for attempt in (
        lambda: manager.restart(instance["id"], before["revision"]),
        lambda: manager.upgrade(instance["id"], "2.0.0", before["revision"]),
        lambda: manager.recover_candidate(instance["id"]),
    ):
        with pytest.raises(Fault) as again:
            await attempt()
        assert again.value.code == "instance_cleanup_pending"
    assert observed["restores"].count(instance["id"]) == 0
    # A different plugin is untouched and still callable.
    assert (await host.call(other["id"], "value", "other-call"))["result"] == {"text": "other"}

    return {
        "host": host,
        "manager": manager,
        "candidate": candidate,
        "instance": instance,
        "other": other,
        "observed": observed,
        "original_close": observed["original_close"],
        "original_restore": original_restore,
        "original_child": original_child,
        "original_worker": original_worker,
    }


async def release_blocked(state, *, terminate):
    """Teardown: restore the real collaborators and never leave a child behind."""
    manager = state["manager"]
    manager.repository.restore = state["original_restore"]
    manager.child = state["original_child"]
    manager.worker = state["original_worker"]
    candidate = state["candidate"]
    if terminate:
        candidate.close = state["original_close"]
        if candidate.process.returncode is None:
            await state["original_close"]("test_teardown")
    assert candidate.process.returncode is not None


@pytest.mark.asyncio
async def test_candidate_that_does_not_exit_blocks_restore_and_revival(
    lifecycle_database, tmp_path
):
    host = Host(lifecycle_database, tmp_path)
    try:
        await blocked_candidate_scenario(host, tmp_path, "raises")
    finally:
        await host.manager.close()


@pytest.mark.asyncio
async def test_candidate_that_returns_without_exiting_blocks_recovery(
    lifecycle_database, tmp_path
):
    host = Host(lifecycle_database, tmp_path)
    state = None
    try:
        state = await blocked_candidate_scenario(host, tmp_path, "silent")
        # "close returned" is not an exit proof: nothing may have recovered.
        assert state["instance"]["id"] not in state["observed"]["restores"]
        assert state["instance"]["id"] not in state["observed"]["revived"]
    finally:
        if state is not None:
            await release_blocked(state, terminate=True)
        await host.manager.close()


@pytest.mark.asyncio
async def test_confirmed_exit_resumes_recovery_exactly_once(lifecycle_database, tmp_path):
    host = Host(lifecycle_database, tmp_path)
    state = None
    try:
        state = await blocked_candidate_scenario(host, tmp_path, "raises")
        manager = state["manager"]
        instance = state["instance"]
        candidate = state["candidate"]
        observations = state["observed"]
        # The candidate is still a real live child, so the instance stays blocked.
        with pytest.raises(Fault) as refused:
            await manager.recover_candidate(instance["id"])
        assert refused.value.code == "instance_cleanup_pending"
        assert instance["id"] not in observations["restores"]

        # Teardown of the injected failure: the real close now runs and the exit
        # is confirmed from the OS process, not from the fact that close returned.
        candidate.close = state["original_close"]
        await candidate.close("candidate_released")
        assert candidate.process.returncode is not None

        # One ordinary entry point settles it through the single recovery path.
        result = await manager.restart(
            instance["id"], (await manager.instance(instance["id"]))["revision"]
        )
        assert instance["id"] in observations["restores"], (
            "the confirmed exit must resume the snapshot restore"
        )
        assert instance["id"] in observations["revived"], (
            "the previous version must be started through the normal path"
        )
        assert observations["restores"].count(instance["id"]) == 1
        # Order: exit confirmed -> snapshot restored -> previous version started.
        # Every observed restore/revival after the cleanup must see a dead candidate;
        # the blocked attempt itself never restored anything at all.
        assert observations["restore_exit"][:1] == [True], observations["restore_exit"]
        # Order for this instance only: exit confirmed -> snapshot restored ->
        # previous version started. No restore or start may be recorded before
        # that, so every entry in both lists must come after the confirmed exit.
        assert observations["restore_exit"] == [True], observations["restore_exit"]
        assert observations["revive_exit"][:1] == [True], observations["revive_exit"]
        assert manager.admission.get(instance["id"]) is None
        assert manager.abandoned.get(instance["id"]) is None
        assert (await host.call(instance["id"], "value", "after-recovery"))["result"] == {
            "text": "1.0.0"
        }
        stored = await host.call(instance["id"], "stored", "state-after-recovery")
        assert stored["result"]["boot"] == {"version": "1.0.0"}
        # A repeated recovery does not restore or start a second time.
        assert manager.abandoned.get(instance["id"]) is None
        with pytest.raises(Fault) as repeated:
            await manager.recover_candidate(instance["id"])
        assert repeated.value.code == "not_found"
        assert observations["restores"].count(instance["id"]) == 1
        assert (await host.call(state["other"]["id"], "value", "other-after"))["result"] == {
            "text": "other"
        }
        return result
    finally:
        if state is not None:
            await release_blocked(state, terminate=True)
        await host.manager.close()


@pytest.mark.asyncio
async def test_restarted_host_honors_the_durable_cleanup_marker(lifecycle_database, tmp_path):
    host = Host(lifecycle_database, tmp_path)
    state = None
    try:
        state = await blocked_candidate_scenario(host, tmp_path, "raises")
        manager, instance = state["manager"], state["instance"]
        # Proven absence is the only evidence a fresh host accepts: the OS fact
        # about the recorded pid, never an in-memory flag.
        candidate = state["candidate"]
        candidate.close = state["original_close"]
        await candidate.close("host_restart_simulation")
        assert candidate.process.returncode is not None
        # Simulate a fresh host: drop the in-memory candidate, keep the marker.
        manager.abandoned.pop(instance["id"], None)
        await manager.close()
        host.manager = None
        fresh = await host.open()
        assert fresh.admission.get(instance["id"]) == "instance_cleanup_pending"
        # Nothing was revived at startup while the marker was unresolved...
        assert instance["id"] not in state["observed"]["revived"]
        # ...and the first ordinary access finishes it exactly once.
        restored = await fresh.recover_candidate(instance["id"])
        assert restored["error"]["rolled_back"] is True, restored["error"]
        assert restored["error"]["restored_version"] == "1.0.0"
        assert fresh.admission.get(instance["id"]) is None
        assert (await host.call(instance["id"], "value", "fresh-host"))["result"] == {
            "text": "1.0.0"
        }
        state = None
    finally:
        if state is not None:
            await release_blocked(state, terminate=True)
        if host.manager is None:
            host.manager = await host.open()
        await host.manager.close()


async def close_error_after_real_exit_scenario(host, tmp_path):
    """A closing error must not override the real exit fact."""
    manager = await host.open()
    old = await host.install(
        package(
            tmp_path / "old", "fixture.closeerr", "1.0.0", "old",
            source=stateful_source("1.0.0"),
            capabilities=[{"id": "state.kv", "major": 1}],
        ),
        "install-closeerr-old",
    )
    instance = await host.enable(old, "CloseError", "instance-closeerr")
    await host.install(
        package(
            tmp_path / "broken", "fixture.closeerr", "2.0.0", "broken",
            source=stateful_source("2.0.0", failing_start=True),
            capabilities=[{"id": "state.kv", "major": 1}],
        ),
        "install-closeerr-broken",
    )
    observed = {"restores": [], "revived": []}
    original_restore = manager.repository.restore
    original_child = manager.child
    original_worker = manager.worker

    def observing_restore(identifier, snapshot):
        observed["restores"].append(identifier)
        return original_restore(identifier, snapshot)

    def observing_worker(instance_document, *, prepared=None):
        if prepared is None and instance_document["id"] == instance["id"]:
            observed["revived"].append(instance_document["id"])
        return original_worker(instance_document, prepared=prepared)

    armed = {"on": False}

    def installing_child(package_document, identifier):
        process = original_child(package_document, identifier)
        if armed["on"] and identifier == instance["id"]:
            armed["on"] = False
            original_close = process.close

            async def closing_then_raising(_reason="stopped"):
                # The child really exits, and the call still reports an error:
                # the OS fact, not the call's return, decides recovery.
                await original_close(_reason)
                raise OSError("simulated close error after exit")

            process.close = closing_then_raising
        return process

    manager.repository.restore = observing_restore
    manager.child = installing_child
    manager.worker = observing_worker
    current = await manager.instance(instance["id"])
    armed["on"] = True
    try:
        with pytest.raises(AppError) as failure:
            await manager.upgrade(instance["id"], "2.0.0", current["revision"])
    finally:
        armed["on"] = False
        manager.repository.restore = original_restore
        manager.child = original_child
        manager.worker = original_worker
    recorded = failure.value.details["error"]
    # The original cause is preserved and the rollback is reported as real.
    assert recorded["code"] == "plugin_start_failed"
    assert recorded["failed_version"] == "2.0.0"
    assert recorded["candidate_closed"] is True
    assert recorded["rolled_back"] is True
    assert recorded["restored_version"] == "1.0.0"
    assert recorded["restore_state"] in {"restored", "restore_failed"}
    assert observed["restores"] == [instance["id"]]
    assert observed["revived"] == [instance["id"]]
    assert manager.admission.get(instance["id"]) is None
    assert (await host.call(instance["id"], "value", "after-close-error"))["result"] == {
        "text": "1.0.0"
    }
    await manager.close()


@pytest.mark.asyncio
async def test_close_error_does_not_override_a_confirmed_exit(lifecycle_database, tmp_path):
    await close_error_after_real_exit_scenario(Host(lifecycle_database, tmp_path), tmp_path)
