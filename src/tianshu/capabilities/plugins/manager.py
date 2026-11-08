"""Plugin package/instance lifecycle; composition roots only attach this owner."""

import asyncio
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import copy
import json
from pathlib import Path
import time
import uuid

from tianshu.runtime.errors import AppError

from ..contracts import Fault, digest, require
from ..local_work import settled
from ..storage import run_repository
from .packages import Packages, relative_path
from .process import Process, RemoteError, os_process_exited
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

# An instance whose failed upgrade candidate could not be proven exited keeps this
# refusal code: nothing may start it, restore it or auto-recover it until cleanup
# is confirmed through the same controlled path.
CLEANUP_PENDING = "instance_cleanup_pending"

AVAILABLE = frozenset(
    {"state.kv", "http.client", "secrets.read", "jobs.manage", "notifications.emit", "models.generate", "files.store", "sources.read", "messages.present", "channels.native"}
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
        # Instance admission stays open unless that instance's lifecycle is switching;
        # the value is the refusal code its queued and new calls receive.
        self.admission = {}
        # One lifecycle lock per plugin package. Lock order is always
        # package lock -> instance lock; other packages keep serving.
        self.package_locks = defaultdict(asyncio.Lock)
        self.installations = set()
        # Failed upgrade candidates whose exit could not be confirmed yet. They are
        # retried through recover_candidate(); the durable marker lives in the
        # operation ledger so a restarted host reads it back instead of guessing.
        self.abandoned = {}
        # Immutable roots a prepare already created but has not published yet.
        # Reclaim must never remove a directory that is about to become installed.
        self.preparing = defaultdict(int)
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
        self.install_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="tianshu-plugin-install"
        )

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
        unsettled = set()
        for operation in await self.local(self.repository.list, "operations"):
            error = operation.get("error") or {}
            if (
                operation.get("kind") == "upgrade"
                and error.get("candidate_closed") is False
                and operation.get("instance_id")
            ):
                # This host died while a failed candidate was still unproven gone.
                # Read the durable marker back and keep the instance blocked: a
                # restart never restores from an assumption.
                unsettled.add(operation["instance_id"])
                self.admission[operation["instance_id"]] = CLEANUP_PENDING
                # No process object survives a host restart: the recorded pid is
                # the only handle, and it is used solely to prove absence.
                self.abandoned[operation["instance_id"]] = {
                    "process": None,
                    "operation": operation,
                }
            if operation["execution"] in {"accepted", "running"}:
                blocked = operation.get("instance_id") in unsettled
                if operation.get("kind") == "upgrade" and not blocked:
                    await self.local(
                        self.repository.restore,
                        operation["instance_id"],
                        operation["snapshot"],
                    )
                operation.update(
                    execution="interrupted",
                    error=(
                        operation.get("error")
                        if blocked
                        else {"code": "plugin_interrupted"}
                    ),
                )
                await self.local(self.repository.put, "operations", operation)
        async def revive(instance):
            try:
                async with self.locks[instance["id"]]:
                    await self.worker(instance)
                    await self.scheduler.instance_changed(instance["id"])
            except (Fault, RemoteError, TimeoutError, OSError):
                # A blocked instance stays blocked; worker() refused it here.
                pass

        # Recovery runs per package so a slow start never blocks another package,
        # and under that package's lifecycle lock so a switch cannot interleave.
        instances = await self.local(self.repository.list, "instances")
        per_package = defaultdict(list)
        for instance in instances:
            per_package[instance["package_id"]].append(instance)
        for package_id, group in per_package.items():
            async with self.package_locks[package_id]:
                for instance in group:
                    if instance["desired_state"] == "enabled":
                        await revive(instance)
        for instance in instances:
            if instance["desired_state"] != "enabled" and await self.comic_instance(instance):
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
            # A failed switch may still owe a live candidate that never registered.
            for candidate in tuple(self.abandoned.values()):
                process = candidate.get("process")
                if process is not None:
                    try:
                        await process.close("host_shutdown")
                    except (Fault, TimeoutError, OSError):
                        pass
            self.abandoned.clear()
            self.workers.clear()
        finally:
            self.started = False
            try:
                self.owner.close()
            finally:
                self.install_executor.shutdown(wait=True, cancel_futures=True)

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
        # Installation never shares the ordinary capability file workers: staging,
        # dependency resolution and Git fetches run on this owner's own thread.
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
        # The source fingerprint keeps a repeated request from re-preparing
        # different content under the same caller-supplied identity.
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
        """Stage the package, then publish it under this package's lifecycle lock.

        Staging (source fetch, dependency environment, inspection child) never
        holds the package lock, so a slow checkout does not stall that plugin's
        ordinary lifecycle work. The publish step is where a package becomes
        installed, and it takes package lock then installation lock - the same
        order uninstall uses.
        """
        await self._prepare_install(operation, source)

    async def _install_local(self, operation, *args, **kwargs):
        # The installation lock bounds admission. Git/pip waits belong to this
        # owner, independently of ordinary capability file reads and writes.
        context = copy_context()
        return await asyncio.get_running_loop().run_in_executor(
            self.install_executor, lambda: context.run(operation, *args, **kwargs)
        )

    async def _prepare_install(self, operation, source):
        process, package, prepared = None, None, None
        try:
            operation.update(execution="running")
            await self.local(self.repository.put, "operations", operation)
            # Held only while immutable content and its environment are produced
            # and inspected. A slow checkout therefore never blocks this plugin's
            # ordinary lifecycle work, and it is never taken in reverse order.
            async with self.installation_lock:
                package = await settled(self._install_local, self.packages.prepare, source)
                # No package row points at this directory yet: hold this plugin
                # id so reclaim cannot take it while it is inspected or before
                # the publish below submits it.
                prepared = package["manifest"]["id"]
                self.preparing[package["root"]] += 1
                process = Process(package, self.reject_capability)
                await process.start()
                description = await process.call("plugin.describe", {}, timeout=15)
                manifest = package["manifest"]
                if manifest.get("contributions_file"):
                    static = json.loads(
                        (
                            Path(package["root"])
                            / str(relative_path(manifest["contributions_file"]))
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
                await process.close("inspection_finished")
                process = None
            # Publish is the only step that makes this package globally visible,
            # so it is what must not interleave with an uninstall of this plugin.
            # Lock order is always package lock -> installation lock, exactly as
            # uninstall takes them, so no waiter can be held in reverse order.
            async with self.package_locks[prepared], self.installation_lock:
                regenerate = await self.local(self.repository.get, "packages", package["id"])
                require(
                    regenerate is None or regenerate["digest"] == package["digest"],
                    "plugin_version_conflict",
                    409,
                )
                # Reclamation only skips roots a publish is holding, so a second
                # probe here proves the files really survived to publication.
                require(Path(package["root"]).is_dir(), "plugin_install_failed", 409)
                await self.local(self.repository.put, "packages", package)
                operation.update(execution="succeeded", package_id=package["id"])
        except (Fault, RemoteError, TimeoutError, OSError, ValueError, KeyError) as error:
            operation.update(
                execution="failed",
                error={"code": error.code if isinstance(error, Fault) else "plugin_install_failed"},
            )
        finally:
            if package is not None and package["root"] in self.preparing:
                self.preparing[package["root"]] -= 1
                if not self.preparing[package["root"]]:
                    del self.preparing[package["root"]]
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
        manifest = package["manifest"]
        async with self.package_locks[manifest["id"]], self.operations_lock:
            # Re-read under the locks so an uninstall that finished meanwhile is
            # visible here and cannot be raced by a new instance.
            package = await self.package(package_key)
            manifest = package["manifest"]
            old = await self.local(self.repository.get, "instances", identifier)
            if old:
                require(
                    old["package_id"] == manifest["id"] and old["name"] == name.strip(),
                    "idempotency_conflict",
                    409,
                )
                return await self.projection(old)
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
        await self.settle_pending(identifier)
        package_id = (await self.instance(identifier))["package_id"]
        async with self.package_locks[package_id]:
            return await self._configure(identifier, config, secrets, expected)

    async def _configure(self, identifier, config, secrets, expected):
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
        await self.settle_pending(identifier)
        package_id = (await self.instance(identifier))["package_id"]
        async with self.package_locks[package_id]:
            if enabled:
                return await self._enable(identifier, expected)
            return await self._disable(identifier, expected)

    async def _enable(self, identifier, expected):
        async with self.locks[identifier]:
            instance = await self.instance(identifier)
            previous_state = instance["desired_state"]
            instance.update(desired_state="enabled", revision=instance["revision"] + 1)
            await self.local(self.repository.put, "instances", instance, expected=expected)
            try:
                await self.stop_worker(identifier, "instance_state_changed")
                await self.worker(instance)
                await self.scheduler.instance_changed(identifier)
            except Fault as error:
                if error.code == "comic_file_owner_conflict":
                    # A rejected second instance must not become a future owner
                    # merely because it sorts before the real owner after reboot.
                    revision = instance["revision"]
                    instance.update(desired_state=previous_state, revision=revision + 1)
                    await self.local(self.repository.put, "instances", instance, expected=revision)
                raise
            return await self.projection(instance)

    async def _disable(self, identifier, expected):
        """Withdraw this instance's entries, then stop it; the package lock is held."""
        async with self.locks[identifier]:
            instance = await self.instance(identifier)
            instance.update(desired_state="disabled", revision=instance["revision"] + 1)
            await self.local(self.repository.put, "instances", instance, expected=expected)
            # Disable withdraws this instance's entries before its process stops;
            # other instances keep their own handlers and running children.
            ports = getattr(self, "domain_ports", None)
            if ports is not None and hasattr(ports, "revoke"):
                ports.revoke(identifier)
            await self.stop_worker(identifier, "instance_state_changed")
            return await self.projection(instance)

    async def bindings(self, identifier, bindings, expected):
        await self.ensure_active()
        require(isinstance(bindings, list) and len(bindings) <= 128, "invalid_input", 400)
        await self.settle_pending(identifier)
        package_id = (await self.instance(identifier))["package_id"]
        async with self.package_locks[package_id]:
            return await self._bindings(identifier, bindings, expected)

    async def _bindings(self, identifier, bindings, expected):
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
                await self.scheduler.instance_changed(identifier)
            return await self.projection(instance)

    def child(self, package, identifier):
        """A child whose host callbacks are attributed to its own generation.

        The closure captures the generation so a late callback from a replaced
        child can never act under the replacement's handles.
        """
        process = Process(package, None)
        process.callback = lambda method, params: self.executions.callback(
            identifier, method, params, generation=process.runtime_id
        )
        return process

    async def worker(self, instance, *, prepared=None):
        """Return the process that actually serves this instance's revision.

        A caller that already staged a started, initialized and started child
        (an upgrade switch) hands it in as `prepared` so the plugin's start hook
        runs exactly once; the child then becomes the serving process.
        """
        await self.ensure_active()
        require(
            instance["desired_state"] == "enabled" and not self.closing, "instance_disabled", 409
        )
        # A failed switch whose candidate is not proven gone blocks every path that
        # could start a process for this instance, not just the original upgrade.
        # The refusal code is the durable marker itself, so a restarted host that
        # read it back blocks exactly the same way.
        await self.require_clean_settled(instance["id"])
        package = await self.package(instance["package_id"], instance["version"])
        projection = await self.projection(instance)
        require(not projection["missing_capabilities"], "capability_unavailable", 409)
        validate_config(package["schema"], instance["config"], projection["secret_configured"])
        current = self.workers.get(instance["id"])
        if current and current[0].alive and current[1] == instance["revision"]:
            if prepared is not None and prepared is not current[0]:
                self.executions.revoke(instance["id"], prepared.runtime_id)
                await prepared.close("handover_duplicate")
            return current[0]
        # Stop the previous child by identity: only its own handles die, so a
        # prepared replacement keeps the context it already obtained.
        previous = self.workers.get(instance["id"])
        if previous:
            await self.stop_worker(instance["id"], "instance_restarted", previous[0])
        elif prepared is None:
            self.executions.revoke(instance["id"])
        else:
            self.executions.retain(instance["id"], prepared.runtime_id)
        process = prepared or self.child(package, instance["id"])
        try:
            if prepared is None:
                await process.start()
                context = await self.executions.context(
                    instance, None, generation=process.runtime_id
                )
                await process.call("plugin.initialize", context, timeout=15)
                await process.call("plugin.start", {}, timeout=15)
            self.workers[instance["id"]] = process, instance["revision"]
            # This child is now the only serving generation; handles minted for
            # any earlier one must not linger.
            self.executions.retain(instance["id"], process.runtime_id)
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
            self.executions.revoke(instance["id"], process.runtime_id)
            await process.close("startup_failed")
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
                else:
                    # A child that already lost its registration still owns the
                    # handles issued during its startup or staging.
                    self.executions.revoke(identifier, process.runtime_id)
        finally:
            if not exited.done():
                exited.cancel()
            await asyncio.gather(exited, return_exceptions=True)

    async def settle_accepted(self, identifier, code):
        """Settle calls accepted but not yet executing, without replaying them.

        Their original intent/attempt and receipts stay in the ledger; writes are
        reported as unknown because their dispatch state cannot be proven, reads
        as failed. A call already inside the old process keeps running to its own
        outcome and can never resume the replaced version afterwards.
        """
        for call in await self.local(self.repository.accepted, identifier):
            await self.local(
                self.repository.update_call,
                call["id"],
                execution="interrupted",
                effect="unknown" if call.get("write") else "none",
                error={
                    "code": "outcome_unknown" if call.get("write") else code,
                    "retriable": False,
                },
            )

    async def require_clean_settled(self, identifier):
        """Refuse while a failed switch of this instance is awaiting cleanup.

        The refusal is the same code the admission gate uses, so ordinary calls,
        reloads, enables and configuration changes are all stopped in one place
        instead of each endpoint re-checking for itself.
        """
        refusal = self.admission.get(identifier)
        require(refusal != CLEANUP_PENDING, CLEANUP_PENDING, 409)

    async def candidate_gone(self, identifier, process, record=None):
        """Prove from the real OS process that this failed candidate is gone.

        `returncode` is the direct fact when the object is still here; otherwise
        the recorded pid may only prove **absence**. A live pid proves nothing
        (it can already belong to a reused pid), so it never unblocks anything.
        This never terminates a process and never matches a pid alone, so a
        replacement can never be mistaken for this candidate.
        """
        if process is not None and process.exited:
            return True
        pid = getattr(getattr(process, "process", None), "pid", None) if process else None
        if pid is None and record is not None:
            pid = (record.get("error") or {}).get("candidate_pid")
        if pid is None:
            # Nothing was ever recorded, so nothing can be proven.
            return process is None
        return await asyncio.to_thread(lambda: os_process_exited(pid))

    async def settle_pending(self, identifier):
        """Advance a blocked instance only once its candidate is proven gone.

        Called before this instance's lifecycle locks are taken, so it can finish
        the pending recovery through the one controlled path and then let the
        caller proceed. While the candidate cannot be proven gone it refuses with
        the same refusal code admission uses - it never starts a version.
        """
        if self.admission.get(identifier) != CLEANUP_PENDING:
            return None
        return await self.recover_candidate(identifier)

    async def recover_candidate(self, identifier):
        """Finish the pending cleanup of this instance under its lifecycle lock.

        Only a confirmed exit unlocks the instance; the snapshot is restored and
        the previous version restarted exactly through the same path an ordinary
        upgrade failure uses, so the state is never revived from a guess.
        """
        package_id = (await self.instance(identifier))["package_id"]
        async with self.package_locks[package_id], self.locks[identifier]:
            candidate = self.abandoned.get(identifier)
            record = candidate["operation"] if candidate else None
            if record is None:
                # This host never saw the candidate: read the durable marker back
                # and let the recorded pid prove only its absence.
                for operation in reversed(
                    await self.local(self.repository.list, "operations")
                ):
                    if (
                        operation.get("kind") == "upgrade"
                        and operation.get("instance_id") == identifier
                        and (operation.get("error") or {}).get("candidate_closed") is False
                    ):
                        record = operation
                        break
            require(record is not None, "not_found", 404)
            process = candidate["process"] if candidate else None
            if not await self.candidate_gone(identifier, process, record):
                await self.record_cleanup_pending(
                    record, process, "candidate_still_running"
                )
                raise Fault(CLEANUP_PENDING, 409)
            return await self.finish_failed_switch(identifier, record)

    async def record_cleanup_pending(self, operation, process, reason):
        """Persist the durable marker that keeps a restarted host from guessing.

        The candidate's pid is recorded so a later host can *prove absence*. A
        proven-absent pid is the only cross-host evidence accepted; a live pid
        proves nothing (it may be a reused pid), so it keeps the block.
        """
        pid = getattr(getattr(process, "process", None), "pid", None)
        operation.update(
            execution="failed",
            error={
                **(operation.get("error") or {}),
                "candidate_closed": False,
                "candidate_error": reason,
                "candidate_pid": pid,
                "restored_version": None,
                "rolled_back": False,
                "restore_state": "blocked_candidate_alive",
            },
        )
        await self.local(self.repository.put, "operations", operation)
        return operation

    async def settle_failed_candidate(self, identifier, operation, process):
        """Stop this candidate and decide, from the OS fact alone, what happens next.

        Returning `True` means the failed switch may be restored through the same
        path an ordinary upgrade failure uses. Returning `False` means the instance
        stays blocked: the candidate is still alive, so no snapshot may be
        restored and no previous version may be started.
        """
        cleanup_error = None
        try:
            await self.stop_worker(identifier, "upgrade_abandoned", process)
        except (Fault, RemoteError, TimeoutError, OSError) as error:
            # A closing error is not the exit proof: the OS process decides.
            cleanup_error = getattr(error, "code", type(error).__name__)
        if await self.candidate_gone(identifier, process):
            return True, cleanup_error
        # Keep it recorded and reachable, then block this instance for real.
        self.abandoned[identifier] = {"process": process, "operation": operation}
        self.admission[identifier] = CLEANUP_PENDING
        await self.record_cleanup_pending(
            operation, process, cleanup_error or "candidate_still_running"
        )
        return False, cleanup_error or "candidate_still_running"

    async def finish_failed_switch(self, identifier, operation):
        """Restore the snapshot and the previous version for a settled failure.

        The block is only released once the restore actually happened, so a failed
        restore is reported and can be retried instead of silently unblocking.
        """
        error = operation.get("error") or {}
        try:
            await self.local(self.repository.restore, identifier, operation["snapshot"])
        except (Fault, RemoteError, TimeoutError, OSError, AppError) as failure:
            # The candidate is gone, but nothing was restored: keep the block and
            # report the truth instead of claiming a recovered instance.
            operation.update(
                execution="failed",
                error={
                    **error,
                    "candidate_closed": True,
                    "rolled_back": False,
                    "restored_version": None,
                    "restore_state": "restore_failed",
                    "restore_error": getattr(failure, "code", type(failure).__name__),
                },
            )
            await self.local(self.repository.put, "operations", operation)
            raise
        self.abandoned.pop(identifier, None)
        self.admission.pop(identifier, None)
        self.executions.revoke(identifier)
        current = await self.instance(identifier)
        restored, restore_error = True, None
        if current["desired_state"] == "enabled":
            restored = False
            try:
                await self.worker(current)
                restored = True
            except (Fault, RemoteError, TimeoutError, OSError) as failure:
                restore_error = (
                    failure.data.get("code")
                    if isinstance(failure, RemoteError)
                    else getattr(failure, "code", type(failure).__name__)
                )
        restored_version = None
        if restored:
            restored_version = await self.package(
                current["package_id"], current["version"]
            )
            restored_version = restored_version["manifest"]["version"]
        operation.update(
            execution="failed",
            error={
                **error,
                "rolled_back": restored,
                "restored_version": restored_version,
                "candidate_error": None if restored else error.get("candidate_error"),
                "restore_error": restore_error,
                "candidate_closed": True,
                "restore_state": "restored" if restored else "restore_failed",
            },
        )
        await self.local(self.repository.put, "operations", operation)
        return operation

    async def stop_worker(self, identifier, reason, process=None):
        """Stop the named child and revoke only the handles minted for it.

        A process that never registered - a staged upgrade candidate, an
        inspection child, a candidate that lost its registration - is still a
        live OS child with its own handles, so it is revoked **and closed** here.
        Only the named process is touched: a registered replacement is left
        running with its own handles when a different candidate is being stopped.
        """
        item = self.workers.get(identifier)
        if item and (process is None or item[0] is process):
            self.workers.pop(identifier, None)
            self.executions.revoke(identifier, item[0].runtime_id)
            try:
                await self.runtime_state.forget(identifier, item[0])
            finally:
                await item[0].close(reason)
        elif process is not None:
            # A candidate that never registered still owns its handles.
            self.executions.revoke(identifier, process.runtime_id)
            await process.close(reason)
        instance = await self.instance(identifier)
        if await self.comic_instance(instance):
            await self.comic_files.stop(identifier)

    async def restart(self, identifier, expected):
        """Rebuild this instance's process on its existing installed version.

        The instance keeps its identity, configuration, secret references, bindings
        and persisted state; only the running generation changes. The revision bump
        makes an exiting old process unable to observe or stop its replacement.
        """
        await self.ensure_active()
        await self.settle_pending(identifier)
        package_id = (await self.instance(identifier))["package_id"]
        async with self.package_locks[package_id], self.locks[identifier]:
            instance = await self.instance(identifier)
            require(instance["revision"] == expected, "revision_conflict", 409)
            require(instance["desired_state"] == "enabled", "instance_disabled", 409)
            previous = self.workers.get(identifier)
            old = previous[0] if previous else None
            generation = old.runtime_id if old else None
            instance.update(revision=instance["revision"] + 1)
            await self.local(self.repository.put, "instances", instance, expected=expected)
            # New admission stays closed from the old process's stop until the
            # replacement is started; accepted calls keep their own attempt.
            self.admission[identifier] = "instance_reloading"
            try:
                if old is not None:
                    await self.stop_worker(identifier, "instance_reloaded", old)
                process = await self.worker(instance)
            finally:
                self.admission.pop(identifier, None)
            await self.scheduler.instance_changed(identifier)
            return {
                **(await self.projection(await self.instance(identifier))),
                "reload": {
                    "previous_generation": generation,
                    "generation": process.runtime_id,
                    "pid": process.process.pid,
                },
            }

    async def uninstall(self, package_id, *, purge_data=False, client_id=None):
        """Stop this package's instances, revoke entries and reclaim its files.

        The package lock covers the whole operation: no instance of this package
        can be enabled, created, configured, upgraded or reloaded while it runs,
        and other packages keep their own processes and lifecycle locks.
        """
        await self.ensure_active()
        identifier = "uninstall:" + digest(
            [package_id, client_id or str(uuid.uuid4()), bool(purge_data)]
        )
        if client_id:
            old = await self.local(self.repository.get, "operations", identifier)
            if old is not None:
                require(
                    old["fingerprint"] == [package_id, bool(purge_data)],
                    "idempotency_conflict",
                    409,
                )
                return old["result"]
        # A blocked instance is settled through the single recovery path before
        # this package lock is taken, so the order is never lock-then-recover.
        for instance in await self.local(self.repository.list, "instances"):
            if instance["package_id"] == package_id:
                await self.settle_pending(instance["id"])
        async with self.package_locks[package_id]:
            return await self._uninstall(package_id, identifier, purge_data)

    async def _uninstall(self, package_id, identifier, purge_data):
        """The package lock is already held by the caller."""
        packages = [
            package
            for package in await self.local(self.repository.list, "packages")
            if package["manifest"]["id"] == package_id
        ]
        require(packages, "not_found", 404)
        instances = [
            instance
            for instance in await self.local(self.repository.list, "instances")
            if instance["package_id"] == package_id
        ]
        if all(not package["installed"] for package in packages):
            # A repeated request must not delete the same paths twice or touch an
            # instance created after the first removal.
            result = {
                "uninstalled": True,
                "package_id": package_id,
                "instances": [instance["id"] for instance in instances],
                "reclaimed": [],
                "instance_data": "purged" if purge_data else "retained",
                "external_resources": "retained",
                "operation_id": identifier,
                "already_uninstalled": True,
            }
            await self.local(
                self.repository.put,
                "operations",
                {
                    "id": identifier,
                    "kind": "uninstall",
                    "package_id": package_id,
                    "fingerprint": [package_id, bool(purge_data)],
                    "execution": "succeeded",
                    "error": None,
                    "instances": result["instances"],
                    "reclaimed": [],
                    "purged": bool(purge_data),
                    "created_at": time.time(),
                    "result": result,
                    "already_uninstalled": True,
                },
            )
            return result
        operation = {
            "id": identifier,
            "kind": "uninstall",
            "package_id": package_id,
            "fingerprint": [package_id, bool(purge_data)],
            "execution": "running",
            "error": None,
            "instances": [instance["id"] for instance in instances],
            "reclaimed": [],
            "purged": bool(purge_data),
            "created_at": time.time(),
            "result": None,
        }
        await self.local(self.repository.put, "operations", operation)
        stopped = []
        # This method already holds the package lock, so a pending cleanup cannot
        # be settled here without deadlocking. Removal refuses instead: an
        # instance that still owes a confirmed exit is never silently unblocked.
        for instance in instances:
            await self.require_clean_settled(instance["id"])
        # No instance of this package may take a new call while it is removed.
        for instance in instances:
            self.admission[instance["id"]] = "instance_unloading"
        try:
            for instance in instances:
                # `_disable` expects the package lock to be already held.
                await self._disable(instance["id"], instance["revision"])
                ports = getattr(self, "domain_ports", None)
                if ports is not None and hasattr(ports, "revoke"):
                    ports.revoke(instance["id"])
                if purge_data:
                    await self.local(self.repository.purge, instance["id"])
                stopped.append(instance["id"])
            digests = {package["root"] for package in packages}
            others = {
                package["root"]
                for package in await self.local(self.repository.list, "packages")
                if package["manifest"]["id"] != package_id
            }
            # Reclaim only once no instance, process or upgrade snapshot can still
            # run this directory. Package documents stay as installed=False so a
            # retained instance keeps its identity for a later reinstall.
            for package in packages:
                package["installed"] = False
                await self.local(self.repository.put, "packages", package)
            # Reclamation runs under the installation lock: a concurrent prepare of
            # this package may have re-created an identical directory meanwhile, and
            # a currently installed package row always keeps its files.
            async with self.installation_lock:
                referenced = {
                    package["root"]
                    for package in await self.local(self.repository.list, "packages")
                    if package["installed"]
                }
                # A prepare that already created its directory but has not yet
                # published still owns it: reclaiming it would leave that install
                # with a package row pointing at missing files.
                # Roots a prepare already produced but has not published are
                # held: deleting them would leave that publish with no files.
                reclaimed = await settled(
                    self._install_local,
                    self.packages.reclaim,
                    digests - others - referenced - set(self.preparing),
                )
            result = {
                "uninstalled": True,
                "package_id": package_id,
                "instances": [instance["id"] for instance in instances],
                "reclaimed": reclaimed,
                "instance_data": "purged" if purge_data else "retained",
                "external_resources": "retained",
                "operation_id": identifier,
            }
            operation.update(execution="succeeded", reclaimed=reclaimed, result=result)
            await self.local(self.repository.put, "operations", operation)
            return result
        except BaseException as error:
            operation.update(
                execution="failed",
                error={
                    "code": getattr(error, "code", "plugin_uninstall_failed"),
                    "stopped_instances": stopped,
                    "packages_marked": [
                        package["id"] for package in packages if not package["installed"]
                    ],
                    "remaining_instances": [
                        instance["id"]
                        for instance in instances
                        if instance["id"] not in stopped
                    ],
                },
            )
            await self.local(self.repository.put, "operations", operation)
            raise
        finally:
            for instance in instances:
                self.admission.pop(instance["id"], None)
                if instance["id"] in self.abandoned:
                    # Cleanup is still owed, so the block survives this operation.
                    self.admission[instance["id"]] = CLEANUP_PENDING

    async def upgrade(self, identifier, version, expected):
        await self.ensure_active()
        # A pending cleanup always runs first, through this same path only.
        await self.settle_pending(identifier)
        package_id = (await self.instance(identifier))["package_id"]
        async with self.package_locks[package_id], self.locks[identifier]:
            try:
                return await self._upgrade(identifier, version, expected)
            except (Fault, RemoteError, TimeoutError, OSError, AppError) as error:
                # The console must never present a switch as successful when the
                # serving process did not come up. The recorded operation carries
                # the exact stage and any rollback outcome.
                await self.raise_operation_failure(
                    error, kind="upgrade", instance=identifier
                )

    async def raise_operation_failure(self, error, *, kind, instance=None, package=None):
        """Refuse with the recorded stage/rollback facts instead of a bare fault."""
        failure = None
        for operation in reversed(await self.local(self.repository.list, "operations")):
            if operation.get("kind") != kind:
                continue
            if instance is not None and operation.get("instance_id") != instance:
                continue
            if package is not None and operation.get("package_id") != package:
                continue
            failure = operation
            break
        recorded = (failure or {}).get("error") or {}
        raise AppError(
            recorded.get("code") or ("plugin_" + kind + "_failed"),
            409,
            details={
                "operation_id": (failure or {}).get("id"),
                "execution": (failure or {}).get("execution"),
                "error": recorded or None,
                "cause": getattr(error, "code", type(error).__name__),
            },
        ) from None

    async def _upgrade(self, identifier, version, expected):
        """Prepare the new version, hand its process over and only then commit."""
        instance = await self.instance(identifier)
        require(instance["revision"] == expected, "revision_conflict", 409)
        previous = await self.package(instance["package_id"], instance["version"])
        package = await self.package(instance["package_id"], version)
        require(version != instance["version"], "invalid_input", 400)
        snapshot = await self.local(self.repository.snapshot, identifier)
        operation = {
            "id": "upgrade:" + uuid.uuid4().hex,
            "kind": "upgrade",
            "instance_id": identifier,
            "snapshot": snapshot,
            "execution": "running",
            "error": None,
            "package_id": package["id"],
            "from_version": previous["manifest"]["version"],
            "to_version": version,
            "created_at": time.time(),
        }
        await self.local(self.repository.put, "operations", operation)
        # Admission closes for this instance only. A call accepted before the
        # switch keeps its own document and attempt; it is settled by its
        # original snapshot rather than restarting the previous version.
        self.admission[identifier] = "instance_upgrading"
        process = None
        handed_over = False
        try:
            await self.stop_worker(identifier, "upgrading")
            # A call accepted against the previous version but not yet executed
            # would otherwise wake that version behind the switch's back. It is
            # settled now under its original identity: writes become unknown,
            # reads become failed, and nothing is replayed.
            await self.settle_accepted(identifier, "instance_upgraded")
            process = self.child(package, identifier)
            await process.start()
            migrated = await process.call(
                "plugin.migrate_config",
                {
                    "from_version": previous["manifest"].get("config_version", 0),
                    "config": instance["config"],
                },
            )
            candidate = {**instance, "version": version}
            candidate["config"] = merge(defaults(package["schema"]), migrated)
            validate_config(
                package["schema"],
                candidate["config"],
                await self.local(self.vault.names, identifier),
            )
            context = await self.executions.context(
                candidate, None, generation=process.runtime_id
            )
            await process.call("plugin.initialize", context)
            await process.call(
                "plugin.migrate_data",
                {
                    "from_version": previous["manifest"].get("data_version", 0),
                    "to_version": package["manifest"].get("data_version", 0),
                },
            )
            # The staged child runs this plugin's start hook exactly once. Its
            # successful start plus the RPC channel is the switch's health proof;
            # the same child then becomes the serving process.
            await process.call("plugin.start", {}, timeout=15)
            require(process.alive, "plugin_unavailable", 503)
            instance.update(
                config=candidate["config"],
                version=version,
                revision=instance["revision"] + 1,
            )
            served = await self.worker(instance, prepared=process)
            require(served is process, "plugin_upgrade_failed", 409)
            # From here the new version is the serving process; a later ledger
            # failure (for example a CAS conflict) can no longer roll the switch
            # back, so it must not be reported as a restored previous version.
            handed_over = True

            def commit():
                with self.repository.transaction() as db:
                    self.repository.put("instances", instance, db, expected=expected)
                    operation["execution"] = "succeeded"
                    operation["generation"] = process.runtime_id
                    self.repository.put("operations", operation, db)

            await run_repository(self.runtime.database, commit)
            operation["execution"] = "succeeded"
        except BaseException as error:
            code = (
                error.data.get("code")
                if isinstance(error, RemoteError)
                else error.code
                if isinstance(error, Fault)
                else "plugin_upgrade_failed"
            )
            code = code or "plugin_upgrade_failed"
            operation.update(
                error={
                    "code": code,
                    "failed_version": version,
                    "restored_version": previous["manifest"]["version"],
                }
            )
            # Two mutually exclusive outcomes, decided by the OS fact alone:
            # the candidate is proven gone, or the instance stays blocked. A
            # snapshot is never restored and an old version is never started
            # while the failed candidate could still be running.
            settled = True
            if process is not None:
                settled, _ = await self.settle_failed_candidate(
                    identifier, operation, process
                )
            if settled:
                await self.finish_failed_switch(identifier, operation)
            raise
        finally:
            # Cleanup only. This close is never the exit proof, so it cannot unblock
            # an unsettled instance, and a second close must not replace the
            # original failure with a cleanup error.
            if not handed_over and process is not None:
                try:
                    await process.close("upgrade_abandoned")
                except (Fault, RemoteError, TimeoutError, OSError):
                    pass
            # The guard is released unless the candidate still blocks this
            # instance; the exit proof - never this cleanup - decides that.
            if identifier not in self.abandoned:
                self.admission.pop(identifier, None)
        return await self.projection(await self.instance(identifier))
