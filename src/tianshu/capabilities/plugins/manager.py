"""Plugin package/instance lifecycle; composition roots only attach this owner."""

import asyncio
from collections import defaultdict
import copy
import json
from pathlib import Path
import time
import uuid

from ..contracts import Fault, require
from ..storage import run_repository
from .packages import Packages, relative_path
from .process import Process, RemoteError
from .repository import Repository
from .schema import (
    check,
    defaults,
    merge,
    protocol,
    secret_names,
    validate_config,
    validate_description,
)
from .vault import Vault

AVAILABLE = frozenset(
    {"state.kv", "http.client", "secrets.read", "jobs.manage", "notifications.emit", "models.generate", "files.store", "sources.read", "messages.present"}
)


def validate_settings(settings):
    config = settings.get("plugins", {})
    require(
        isinstance(config, dict) and set(config) <= {"package_directory", "comic_files", "resources"},
        "invalid_input",
        400,
    )
    from ..media.comics.plugin import configuration

    configuration(settings)
    if "package_directory" in config:
        require(
            isinstance(config["package_directory"], str)
            and Path(config["package_directory"]).is_absolute(),
            "invalid_input",
            400,
        )


class PluginManager:
    def __init__(
        self,
        settings,
        *,
        runtime=None,
        local=asyncio.to_thread,
        content_discovery=None,
        comic_files=None,
    ):
        validate_settings(settings)
        anchor = Path(settings["database_path"])
        self.local = local
        self.runtime = runtime
        self.resource_profiles = settings.get("plugins",{}).get("resources",{})
        require(
            content_discovery is None or callable(getattr(content_discovery, "discover", None)),
            "invalid_input",
            400,
        )
        self.content_discovery = content_discovery
        self.comic_files = comic_files
        self.repository = Repository(str(anchor) + ".plugins", runtime=runtime)
        self.vault = Vault(self.repository, runtime.credentials)
        self.packages = Packages(
            settings.get("plugins", {}).get("package_directory", str(anchor) + ".plugins")
        )
        self.workers, self.locks, self.background = {}, defaultdict(asyncio.Lock), set()
        self.installations = set()
        self.scheduler_task = None
        self.operations_lock = asyncio.Lock()
        self.installation_lock = asyncio.Lock()
        self.owner_busy = False
        self.started, self.closing = False, False
        from .owner import Owner

        self.owner = Owner(str(anchor) + ".plugins.owner")
        self.worker_enabled=False
        from .runtime_state import RuntimeState

        self.runtime_state = RuntimeState(self)
        from .execution import Executions
        from .scheduler import Scheduler

        self.executions = Executions(self)
        self.scheduler = Scheduler(self)
        from .task_source import PluginTasks

        self.task_source = PluginTasks(self)

    @property
    def available_capabilities(self):
        return (
            AVAILABLE
            | ({"content.discovery"} if self.content_discovery is not None else set())
            | ({"comics.files"} if self.comic_files is not None else set())
        )

    async def start(self):
        if self.started:
            return
        try:
            self.owner.acquire()
        except Fault as error:
            if error.code == "plugin_host_busy":
                # Another supervisor may own the plugin ledger while this platform
                # endpoint continues serving its other existing capabilities.
                self.owner_busy = True
                return
            raise
        self.closing = False
        self.owner_busy = False
        self.started = True
        self.runtime_state.start()
        # The process that held these contexts is gone. A write is never replayed automatically.
        for call in await self.local(self.repository.unfinished):
            await self.local(
                self.repository.update_call,
                call["id"],
                execution="interrupted",
                effect=("unknown" if call["dispatch"] != "not_sent" else "not_applied")
                if call.get("write")
                else "none",
                error={
                    "code": "outcome_unknown" if call.get("write") else "plugin_interrupted",
                    "retriable": False,
                },
            )
        for operation in await self.local(self.repository.list, "operations"):
            if operation["execution"] in {"accepted", "running"}:
                if operation.get("kind") == "upgrade":
                    await self.local(
                        self.repository.restore, operation["instance_id"], operation["snapshot"]
                    )
                operation.update(execution="interrupted", error={"code": "plugin_interrupted"})
                await self.local(self.repository.put, "operations", operation)
        for instance in await self.local(self.repository.list, "instances"):
            if instance["desired_state"] == "enabled":
                try:
                    async with self.locks[instance["id"]]:
                        await self.worker(instance)
                except (Fault, RemoteError, TimeoutError, OSError):
                    pass
            elif await self.comic_instance(instance):
                # Recover a crash after persisting disable but before releasing
                # the runtime's old owner. Never re-enable that instance.
                try:
                    await self.comic_files.stop(instance["id"])
                except (Fault, TimeoutError, OSError):
                    pass
        if self.comic_files is not None:
            await self.ensure_comic_plugin()
        await self.scheduler.activate()

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.background.add(task)
        task.add_done_callback(self.background.discard)
        return task

    async def check_authorizer(self,authorize,*args):
        import inspect
        result=authorize(*args)
        return await result if inspect.isawaitable(result) else result

    async def ensure_active(self):
        require(self.worker_enabled,"plugin_worker_required",503)
        if not self.started:
            await self.start()
        require(self.started and not self.closing, "plugin_host_busy", 409)

    async def quiesce(self):
        # The host has stopped new root ingress. Existing invocations retain
        # their worker, callbacks and child-job admission while they settle.
        if self.scheduler_task:
            self.scheduler_task.cancel()
            await asyncio.gather(self.scheduler_task, return_exceptions=True)
        while self.executions.running or self.installations:
            tasks = set(self.executions.running.values()) | self.installations
            await asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True)

    async def close(self):
        self.closing = True
        try:
            for task in tuple(self.background):
                task.cancel()
            await asyncio.gather(*tuple(self.background), return_exceptions=True)
            await self.executions.close()
            await asyncio.gather(
                *(self.stop_worker(identifier, "host_shutdown") for identifier in tuple(self.workers)),
                return_exceptions=True,
            )
            self.workers.clear()
        finally:
            self.started = False
            self.owner.close()

    async def reject_capability(self, method, params):
        raise Fault("permission_denied", 403)

    async def catalog(self):
        builtin = Path(__file__).parent / "builtin"
        return [
            {"source_id": path.parent.name, **json.loads(path.read_text(encoding="utf-8"))}
            for path in sorted(builtin.glob("*/plugin.json"))
        ]

    async def install(self, source, client_id, *, wait=False):
        await self.ensure_active()
        require(isinstance(client_id, str) and 1 <= len(client_id) <= 128, "invalid_input", 400)
        require(isinstance(source, dict), "invalid_input", 400)
        if source.get("type") == "builtin":
            identifier = source.get("id")
            require(
                identifier in {item["source_id"] for item in await self.catalog()}, "not_found", 404
            )
            source = {
                "type": "directory",
                "path": str(Path(__file__).parent / "builtin" / identifier),
            }
        from ..contracts import digest

        fingerprint = digest(source)
        identifier = "install:" + digest(client_id)
        async with self.operations_lock:
            old = await self.local(self.repository.get, "operations", identifier)
            if old:
                require(old["fingerprint"] == fingerprint, "idempotency_conflict", 409)
                return old
            operation = {
                "id": identifier,
                "fingerprint": fingerprint,
                "execution": "accepted",
                "created_at": time.time(),
                "error": None,
                "package_id": None,
            }
            await self.local(self.repository.put, "operations", operation)
            task = self.spawn(self._install(operation, source))
            self.installations.add(task)
            task.add_done_callback(self.installations.discard)
        if wait:
            await task
        return operation

    async def ensure_comic_plugin(self):
        """Use normal installation/instance records; seed once, respect later disable."""
        from ..media.comics.plugin import BUILTIN

        instances = await self.local(self.repository.list, "instances")
        for instance in instances:
            if await self.comic_instance(instance):
                return
        operation = await self.install(
            {"type": "builtin", "id": BUILTIN},
            "comic-files-bootstrap:" + uuid.uuid4().hex,
            wait=True,
        )
        if operation["execution"] != "succeeded":
            return
        instance = await self.create(operation["package_id"], "漫画文件辅助", "comic-files-default")
        try:
            await self.state(instance["id"], True, instance["revision"])
        except (Fault, RemoteError, TimeoutError, OSError):
            # Desired and actual state remain visible; an unavailable helper does
            # not prevent unrelated Platform modules from starting.
            pass

    async def comic_instance(self, instance):
        if self.comic_files is None:
            return False
        from ..media.comics.plugin import builtin_package

        package = await self.local(
            self.repository.get, "packages", instance["package_id"] + "@" + instance["version"]
        )
        return package is not None and builtin_package(package)

    async def _install(self, operation, source):
        async with self.installation_lock:
            await self._prepare_install(operation, source)

    async def _prepare_install(self, operation, source):
        process = None
        try:
            operation.update(execution="running")
            await self.local(self.repository.put, "operations", operation)
            package = await self.local(self.packages.prepare, source)
            process = Process(package, self.reject_capability)
            await process.start()
            description = await process.call("plugin.describe", {}, timeout=15)
            manifest = package["manifest"]
            if manifest.get("contributions_file"):
                static = json.loads(
                    (
                        Path(package["root"]) / str(relative_path(manifest["contributions_file"]))
                    ).read_text(encoding="utf-8")
                )
                for key in ("actions", "commands", "tools", "events", "bridges"):
                    require(
                        static.get(key, []) == description.get(key, []),
                        "plugin_description_mismatch",
                        409,
                    )
                description = static
            validate_description(description, manifest)
            package["description"] = description
            old = await self.local(self.repository.get, "packages", package["id"])
            require(
                old is None or old["digest"] == package["digest"], "plugin_version_conflict", 409
            )
            await self.local(self.repository.put, "packages", package)
            operation.update(execution="succeeded", package_id=package["id"])
        except (Fault, RemoteError, TimeoutError, OSError, ValueError, KeyError) as error:
            operation.update(
                execution="failed",
                error={"code": error.code if isinstance(error, Fault) else "plugin_install_failed"},
            )
        finally:
            if process:
                await process.close("inspection_finished")
            await self.local(self.repository.put, "operations", operation)

    async def package(self, identifier, version=None):
        key = identifier if version is None else identifier + "@" + version
        package = await self.local(self.repository.get, "packages", key)
        require(package is not None and package["installed"], "not_found", 404)
        return package

    async def instance(self, identifier):
        instance = await self.local(self.repository.get, "instances", identifier)
        require(instance is not None, "not_found", 404)
        return instance

    async def create(self, package_key, name, client_id):
        await self.ensure_active()
        package = await self.package(package_key)
        require(
            isinstance(name, str)
            and 1 <= len(name.strip()) <= 128
            and isinstance(client_id, str)
            and 1 <= len(client_id) <= 128,
            "invalid_input",
            400,
        )
        identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, "tianshu-plugin-instance:" + client_id))
        async with self.operations_lock:
            old = await self.local(self.repository.get, "instances", identifier)
            if old:
                require(
                    old["package_id"] == package["manifest"]["id"] and old["name"] == name.strip(),
                    "idempotency_conflict",
                    409,
                )
                return await self.projection(old)
            manifest = package["manifest"]
            instance = {
                "id": identifier,
                "package_id": manifest["id"],
                "version": manifest["version"],
                "name": name.strip(),
                "config": defaults(package["schema"]),
                "bindings": [],
                "desired_state": "disabled",
                "revision": 1,
                "created_at": time.time(),
            }
            await self.local(self.repository.put, "instances", instance)
        return await self.projection(instance)

    async def projection(self, instance):
        package = await self.local(
            self.repository.get, "packages", instance["package_id"] + "@" + instance["version"]
        )
        names = await self.local(self.vault.names, instance["id"])
        missing = [
            item["id"]
            for item in package["manifest"]["capabilities"]["required"]
            if item["major"] != 1 or item["id"] not in self.available_capabilities
        ]
        problem = None
        try:
            validate_config(package["schema"], instance["config"], names)
        except Fault as error:
            problem = error.code
        worker = self.workers.get(instance["id"])
        local_ready = bool(worker and worker[0].alive and worker[1] == instance["revision"])
        runtime = await self.local(self.repository.runtime, instance["id"], instance["revision"])
        state = "ready" if local_ready or runtime is not None else "stopped"
        if instance["desired_state"] == "enabled" and state != "ready":
            state = "managed_elsewhere" if self.owner_busy else "error"
        if missing or not package["installed"]:
            state = "blocked"
        if problem:
            state = "pending_configuration"
        file_runtime = None
        if await self.comic_instance(instance):
            try:
                file_runtime = await self.comic_files.status(instance["id"])
                expected = "ready" if instance["desired_state"] == "enabled" else "stopped"
                if file_runtime["state"] != expected:
                    state = "error"
            except (Fault, TimeoutError, OSError):
                file_runtime = {"state": "unavailable"}
                state = "error"
        description = copy.deepcopy(package.get("description", {"actions": []}))
        for action in description["actions"]:
            gaps = [
                port["id"]
                for port in action["required_capabilities"]
                if port["major"] != 1 or port["id"] not in self.available_capabilities
            ]
            action["availability"] = {
                "available": state == "ready" and not gaps,
                "missing_capabilities": gaps,
            }
        return {
            **instance,
            "state": state,
            "plugin_runtime": runtime,
            "missing_capabilities": missing,
            "problem": problem,
            "binding_overrides": package["manifest"].get("binding_overrides", []),
            "secret_configured": {
                key: bool(names.get(key)) for key in secret_names(package["schema"])
            },
            "config_schema": package["schema"],
            "contributions": description,
            **({"file_runtime": file_runtime} if file_runtime is not None else {}),
        }

    async def view(self):
        packages = await self.local(self.repository.list, "packages")
        instances = await self.local(self.repository.list, "instances")
        return {
            "schema_version": 1,
            "packages": [
                self.public_package(package) for package in packages if package["installed"]
            ],
            "instances": [await self.projection(instance) for instance in instances],
            "installations": await self.local(self.repository.list, "operations"),
            "capabilities": sorted(self.available_capabilities),
            "catalog": await self.catalog(),
            "notifications": await self.local(self.repository.list, "notifications"),
        }

    @staticmethod
    def public_package(package):
        return {key: package[key] for key in ("id", "manifest", "digest", "installed", "source")}

    async def configure(self, identifier, config, secrets, expected):
        await self.ensure_active()
        require(isinstance(config, dict) and isinstance(secrets, dict), "invalid_input", 400)
        async with self.locks[identifier]:
            instance = await self.instance(identifier)
            package = await self.package(instance["package_id"], instance["version"])
            schema = package["schema"]
            names = secret_names(schema)
            require(set(secrets) <= names, "invalid_input", 400)
            configured = await self.local(self.vault.names, identifier)
            for name, value in secrets.items():
                if value is not None:
                    check(schema["properties"][name], value)
                    require(
                        isinstance(value, str) and len(value.encode()) <= 16384,
                        "invalid_input",
                        400,
                    )
                configured[name] = value is not None
            effective = merge(defaults(schema), config)
            validate_config(schema, effective, configured)
            inspection = Process(package, self.reject_capability)
            try:
                await inspection.start()
                await inspection.call(
                    "plugin.validate", {"config": effective, "secret_configured": configured}
                )
            except RemoteError:
                raise Fault("config_invalid", 400) from None
            finally:
                await inspection.close("configuration_checked")

            def save():
                with self.repository.transaction() as db:
                    current = self.repository.get("instances", identifier, db)
                    require(current["revision"] == expected, "revision_conflict", 409)
                    for name, value in secrets.items():
                        self.vault.save(identifier, name, value, db)
                    current.update(config=effective, revision=current["revision"] + 1)
                    return self.repository.put("instances", current, db)

            instance = await run_repository(self.runtime.database,save)
            await self.stop_worker(identifier, "configuration_changed")
            if instance["desired_state"] == "enabled":
                await self.worker(instance)
            return await self.projection(instance)

    async def state(self, identifier, enabled, expected):
        await self.ensure_active()
        require(type(enabled) is bool, "invalid_input", 400)
        async with self.locks[identifier]:
            instance = await self.instance(identifier)
            previous_state = instance["desired_state"]
            instance.update(
                desired_state="enabled" if enabled else "disabled",
                revision=instance["revision"] + 1,
            )
            await self.local(self.repository.put, "instances", instance, expected=expected)
            try:
                await self.stop_worker(identifier, "instance_state_changed")
                if enabled:
                    await self.worker(instance)
            except Fault as error:
                if enabled and error.code == "comic_file_owner_conflict":
                    # A rejected second instance must not become a future owner
                    # merely because it sorts before the real owner after reboot.
                    revision = instance["revision"]
                    instance.update(desired_state=previous_state, revision=revision + 1)
                    await self.local(self.repository.put, "instances", instance, expected=revision)
                raise
            return await self.projection(instance)

    async def bindings(self, identifier, bindings, expected):
        await self.ensure_active()
        require(isinstance(bindings, list) and len(bindings) <= 128, "invalid_input", 400)
        async with self.locks[identifier]:
            instance = await self.instance(identifier)
            package = await self.package(instance["package_id"], instance["version"])
            ids = set()
            for binding in bindings:
                protocol("instance", binding)
                require(
                    binding["kind"] == "binding"
                    and binding["instance_id"] == identifier
                    and binding["binding_id"] not in ids,
                    "invalid_input",
                    400,
                )
                ids.add(binding["binding_id"])
                overrides = binding["overrides"]
                require(
                    set(overrides) <= set(package["manifest"].get("binding_overrides", []))
                    and not set(overrides).intersection(secret_names(package["schema"])),
                    "invalid_input",
                    400,
                )
                validate_config(
                    package["schema"],
                    merge(instance["config"], overrides),
                    await self.local(self.vault.names, identifier),
                )
            instance.update(bindings=bindings, revision=instance["revision"] + 1)
            await self.local(self.repository.put, "instances", instance, expected=expected)
            await self.stop_worker(identifier, "bindings_changed")
            if instance["desired_state"] == "enabled":
                await self.worker(instance)
            return await self.projection(instance)

    async def worker(self, instance):
        await self.ensure_active()
        require(
            instance["desired_state"] == "enabled" and not self.closing, "instance_disabled", 409
        )
        package = await self.package(instance["package_id"], instance["version"])
        projection = await self.projection(instance)
        require(not projection["missing_capabilities"], "capability_unavailable", 409)
        validate_config(package["schema"], instance["config"], projection["secret_configured"])
        current = self.workers.get(instance["id"])
        if current and current[0].alive and current[1] == instance["revision"]:
            return current[0]
        await self.stop_worker(instance["id"], "instance_restarted")
        process = Process(
            package, lambda method, params: self.executions.callback(instance["id"], method, params)
        )
        try:
            await process.start()
            context = await self.executions.context(instance, None)
            await process.call("plugin.initialize", context, timeout=15)
            await process.call("plugin.start", {}, timeout=15)
            self.workers[instance["id"]] = process, instance["revision"]
            await self.runtime_state.started(instance["id"], process, instance["revision"])
            self.spawn(self.watch_worker(instance["id"], process))
            await self.scheduler.seed(instance["id"], package["description"])
            return process
        except BaseException:
            current = self.workers.get(instance["id"])
            if current and current[0] is process:
                self.workers.pop(instance["id"], None)
            try:
                await self.runtime_state.forget(instance["id"], process)
            except Exception:
                pass
            await process.close("startup_failed")
            self.executions.revoke(instance["id"])
            if await self.comic_instance(instance):
                try:
                    await self.comic_files.stop(instance["id"])
                except (Fault, TimeoutError, OSError):
                    pass
            raise

    async def watch_worker(self, identifier, process):
        exited = asyncio.create_task(process.process.wait())
        try:
            # A live OS child with a closed/broken RPC reader is not ready either.
            await asyncio.wait((exited, process.reader), return_when=asyncio.FIRST_COMPLETED)
            async with self.locks[identifier]:
                current = self.workers.get(identifier)
                if current and current[0] is process:
                    try:
                        await self.stop_worker(identifier, "worker_exited")
                    except (Fault, TimeoutError, OSError):
                        pass
        finally:
            if not exited.done():
                exited.cancel()
            await asyncio.gather(exited, return_exceptions=True)

    async def stop_worker(self, identifier, reason):
        item = self.workers.pop(identifier, None)
        if item:
            try:
                await self.runtime_state.forget(identifier, item[0])
            finally:
                await item[0].close(reason)
        self.executions.revoke(identifier)
        instance = await self.instance(identifier)
        if await self.comic_instance(instance):
            await self.comic_files.stop(identifier)

    async def uninstall(self, package_id):
        await self.ensure_active()
        packages = [
            package
            for package in await self.local(self.repository.list, "packages")
            if package["manifest"]["id"] == package_id
        ]
        require(packages, "not_found", 404)
        for instance in await self.local(self.repository.list, "instances"):
            if instance["package_id"] == package_id:
                await self.state(instance["id"], False, instance["revision"])
        for package in packages:
            package["installed"] = False
            await self.local(self.repository.put, "packages", package)
        return {"uninstalled": True, "instance_data": "retained", "external_resources": "retained"}

    async def upgrade(self, identifier, version, expected):
        await self.ensure_active()
        async with self.locks[identifier]:
            instance = await self.instance(identifier)
            require(instance["revision"] == expected, "revision_conflict", 409)
            previous = await self.package(instance["package_id"], instance["version"])
            package = await self.package(instance["package_id"], version)
            # Data/config migrations are explicit SDK calls, isolated to this instance's state.
            await self.stop_worker(identifier, "upgrading")
            snapshot = await self.local(self.repository.snapshot, identifier)
            operation = {
                "id": "upgrade:" + uuid.uuid4().hex,
                "kind": "upgrade",
                "instance_id": identifier,
                "snapshot": snapshot,
                "execution": "running",
                "error": None,
                "package_id": package["id"],
                "created_at": time.time(),
            }
            await self.local(self.repository.put, "operations", operation)
            process = Process(
                package, lambda method, params: self.executions.callback(identifier, method, params)
            )
            try:
                await process.start()
                migrated = await process.call(
                    "plugin.migrate_config",
                    {
                        "from_version": previous["manifest"].get("config_version", 0),
                        "config": instance["config"],
                    },
                )
                instance["config"] = merge(defaults(package["schema"]), migrated)
                validate_config(
                    package["schema"],
                    instance["config"],
                    await self.local(self.vault.names, identifier),
                )
                candidate = {**instance, "version": version}
                context = await self.executions.context(candidate, None)
                await process.call("plugin.initialize", context)
                await process.call(
                    "plugin.migrate_data",
                    {
                        "from_version": previous["manifest"].get("data_version", 0),
                        "to_version": package["manifest"].get("data_version", 0),
                    },
                )
                instance.update(version=version, revision=instance["revision"] + 1)

                def commit():
                    with self.repository.transaction() as db:
                        self.repository.put("instances", instance, db, expected=expected)
                        operation["execution"] = "succeeded"
                        self.repository.put("operations", operation, db)

                await run_repository(self.runtime.database,commit)
            except BaseException:
                await self.local(self.repository.restore, identifier, snapshot)
                operation.update(execution="failed", error={"code": "plugin_upgrade_failed"})
                await self.local(self.repository.put, "operations", operation)
                raise
            finally:
                await process.close("upgrade_staged")
                self.executions.revoke(identifier)
                current = await self.instance(identifier)
                if current["desired_state"] == "enabled":
                    await self.worker(current)
            return await self.projection(await self.instance(identifier))
