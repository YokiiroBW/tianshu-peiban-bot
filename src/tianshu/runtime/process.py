"""Role-local processes, native queues and independently supervised consumers."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import inspect
import signal
import time
from uuid import uuid4

from aiohttp import web
from procrastinate.worker import Worker
from psycopg.types.json import Jsonb

from tianshu.models.broker import ModelBroker
from tianshu.models.client import ModelClient
from tianshu.models.http import create_broker_app
from tianshu.models.catalog import NATIVE_PROTOCOLS
from .composition import DomainModule, RuntimeServices, create_app, module_maintenance_roles, start_module
from .database import Database
from .files import FileStore
from .events import EventLog
from .diagnostics import RuntimeDiagnostics
from .commands import CommandStore
from .credentials import CredentialStore
from .jobs import JobQueue
from .migrations import product_migrations, migrate
from .product import build_authority, build_product
from .listeners import start_sites
from .static import register_static
from .serialization import wire
from .execution import PROCESS_ROLES, TASK_LANES, role_worker_groups


RECOVERY_INTERVAL_SECONDS = 30
MAINTENANCE_TIMEOUT_SECONDS = 20


class ProcessHeartbeat:
    def __init__(self, database, role, registration, ready, *, jobs=None):
        self.database, self.role, self.registration, self.is_ready = (
            database,
            role,
            registration,
            ready,
        )
        self.jobs = jobs
        self.instance_id = str(uuid4())
        self.task = None

    async def update(self):
        try:
            current = self.is_ready()
            if inspect.isawaitable(current):
                current = await current
            self.registration.pop("readiness_error", None)
        except Exception as exc:
            current = False
            self.registration["readiness_error"] = type(exc).__name__
        try:
            resources = self.database.pool_stats()
            resources["pools"]["queue"] = {"pool": self.jobs.pool_stats() if self.jobs else None}
            self.registration["database_resources"] = resources
        except Exception as exc:
            # Observability must not take down the independent control pool.
            self.registration["database_resources"] = {"role": self.role, "error": type(exc).__name__}
        async with self.database.transaction(control=True) as conn:
            await conn.execute(
                "INSERT INTO runtime.processes(instance_id,role,ready,registration) "
                "VALUES(%s,%s,%s,%s) ON CONFLICT(instance_id) DO UPDATE SET ready=excluded.ready,"
                "registration=excluded.registration,heartbeat_at=now()",
                (self.instance_id, self.role, bool(current), Jsonb(wire(self.registration))),
            )

    async def run(self):
        while True:
            try:
                await self.update()
                self.registration.pop("heartbeat_error", None)
            except Exception as exc:
                # A shared database outage makes the heartbeat stale; it must not
                # silently cancel unrelated work or pretend this role is ready.
                self.registration["heartbeat_error"] = type(exc).__name__
            await asyncio.sleep(5)

    async def start(self):
        await self.update()
        self.task = asyncio.create_task(self.run(), name="process-heartbeat")

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        async with self.database.transaction(control=True) as conn:
            await conn.execute(
                "DELETE FROM runtime.processes WHERE instance_id=%s", (self.instance_id,)
            )


def stop_event():
    event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(name, event.set)
        except NotImplementedError:
            signal.signal(name, lambda _signal, _frame: loop.call_soon_threadsafe(event.set))
    return event


async def supervise_consumer(jobs, name, group, registration, stop, *, abort_poll_seconds=2,
                             restart_limit=5):
    """A failed native consumer is restarted without cancelling its siblings."""
    failures = 0
    state = registration["consumers"][name]
    while not stop.is_set():
        state.update(state="starting", queues=group["queues"], concurrency=group["concurrency"])
        started = time.monotonic()
        runner = None
        try:
            jobs.app.perform_import_paths()
            worker = Worker(app=jobs.app,
                queues=group["queues"], concurrency=group["concurrency"],
                name="tianshu-" + name, install_signal_handlers=False,
                shutdown_graceful_timeout=30, fetch_job_polling_interval=30,
                abort_job_polling_interval=abort_poll_seconds)
            runner = asyncio.create_task(worker.run())
            while worker.worker_id is None and not runner.done():
                await asyncio.wait([runner], timeout=0.1)
            if worker.worker_id is not None and not runner.done():
                state.update(state="running", worker_id=worker.worker_id)
            await runner
            if stop.is_set():
                break
            error = "consumer_exited"
        except asyncio.CancelledError:
            if runner is not None and not runner.done():
                runner.cancel()
                await asyncio.gather(runner, return_exceptions=True)
            raise
        except Exception as exc:
            error = type(exc).__name__
        if time.monotonic() - started >= 300:
            failures = 0
        failures += 1
        state.update(state="failed" if failures > restart_limit else "restarting",
                     error=error, failures=failures)
        if failures > restart_limit:
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=min(30, 2 ** (failures - 1)))
        except TimeoutError:
            pass
    state["state"] = "stopped"


async def recover_notifications(jobs, stop, role, *, reroute_more=False):
    """Only due or abandoned coordination rows are scanned, in bounded pages."""
    while not stop.is_set():
        try:
            if jobs.recompute_tasks:
                await jobs.recover_recompute_due(limit=128)
            if reroute_more:
                reroute_more = await jobs.route_reassigned_pending(jobs.database, role, limit=128) == 128
        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # The next bounded pass retries after a dependency outage.
        if not jobs.recompute_tasks and not reroute_more:
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=RECOVERY_INTERVAL_SECONDS)
        except TimeoutError:
            pass


async def maintain_domain(module, services, stop, *, interval=RECOVERY_INTERVAL_SECONDS,
                          timeout=MAINTENANCE_TIMEOUT_SECONDS):
    """Run one role-local short callback without blocking its sibling domains."""
    while not stop.is_set():
        try:
            await asyncio.wait_for(module.maintenance(services), timeout=timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            services.module_status.setdefault(module.name, {})["maintenance"] = {
                "state": "failed", "error": type(exc).__name__}
        else:
            services.module_status.setdefault(module.name, {})["maintenance"] = {"state": "ready"}
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


def start_domain_maintenance(modules, services, stop):
    """Start at most one callback loop per eligible module, in its owner role."""
    return tuple(asyncio.create_task(maintain_domain(module, services, stop),
                                     name="domain-maintenance:" + module.name)
        for module in modules if module.maintenance is not None
        and services.role in module_maintenance_roles(module)
        and services.module_status.get(module.name, {}).get("state") != "unavailable")


async def stop_domain_maintenance(tasks):
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def register_domain_tasks(modules, services):
    """A broken optional domain cannot prevent other domains registering."""
    services.jobs.register_static_routes()
    failed = set()
    for module in modules:
        if not module.register_tasks:
            continue
        try:
            module.register_tasks(services.jobs, services)
        except Exception as exc:
            failed.add(module.name)
            services.module_status[module.name] = {
                "state": "unavailable", "error": type(exc).__name__, "stage": "task_registration"}
    return failed


async def run(settings, role, *, initialize=False):
    if role not in PROCESS_ROLES:
        raise ValueError("invalid_process_role")
    database = Database(settings.database(role), role=role)
    worker_settings = settings.document.get("worker", {})
    jobs = JobQueue(
        database.config.dsn, max_connections=worker_settings.get("queue_connections", 8), database=database
    )
    files = FileStore(settings.artifact_directory)
    client = None if role == "model-broker" else ModelClient(
        settings.broker_url, settings.broker_token, **settings.model_transport_for(role))
    services = RuntimeServices(database, jobs, client, files, settings, role)
    services.commands = CommandStore(database)
    services.credentials = CredentialStore(
        database,
        settings.secrets,
        settings.document.get("credentials", {}).get("master_key_ref", "TIANSHU_MASTER_KEY"),
    )
    async with AsyncExitStack() as stack:
        await database.open()
        stack.push_async_callback(database.close)
        if initialize:
            await migrate(database, product_migrations())
            return
        access = None
        if role == "interactive":
            from .access import AccessService

            access = AccessService(database, services.commands, settings)
            settings = await access.load()
            services.settings = settings
        events = EventLog(settings.artifact_directory.parent / "logs", role)
        await events.open()
        stack.push_async_callback(events.close)
        services.events = events
        jobs.events = events
        services.diagnostics = RuntimeDiagnostics(database, client, settings, events=events)
        from tianshu.operations.statistics import StatisticsProjection

        services.statistics = StatisticsProjection(database, services.diagnostics)
        try:
            await events.emit("runtime.starting", "started")
        except Exception:
            pass
        if client is not None:
            await client.open()
            stack.push_async_callback(client.close)
        stop = stop_event()
        if role == "model-broker":
            jobs.register_static_routes()
            authority = build_authority(services)
            from tianshu.companion.behavior_resolver import BehaviorResolver
            from .product_behaviors import validate_action_behaviors

            behavior_reader = BehaviorResolver(database, authority.identity, authority.sources)

            async def validate_behaviors(context, refs, *, purpose):
                return await validate_action_behaviors(behavior_reader, context, refs, purpose=purpose)

            broker = ModelBroker(
                database,
                files,
                settings.secrets,
                jobs=jobs,
                events=events,
                statistics=services.statistics,
                upstream_connections=settings.get("broker", {}).get("upstream_connections", 64),
                diagnostic_connections=settings.get("broker", {}).get("diagnostic_connections", 4),
                validate_submission=authority.identity.check,
                validate_behaviors=validate_behaviors,
                validate_persistence=authority.sources.validate_persistence,
                media_resolver=authority.media.reference,
                provider_credential_refs=settings.get("broker", {}).get(
                    "provider_credential_refs", {}
                ),
                credential_store=services.credentials,
                native_principals={
                    "model-client:" + entry["id"]
                    for entry in settings.document.get("broker", {}).get("external_keys", ())
                },
            )
            await broker.open()
            stack.push_async_callback(broker.close)
            app = create_broker_app(
                broker,
                settings.broker_token,
                settings.document.get("broker", {}).get("external_keys", []),
            )
            registration = {
                "protocols": sorted(NATIVE_PROTOCOLS),
                "quota_groups": list(broker.scheduler.policies),
            }
            ready = lambda: broker.ready
        elif role == "interactive":
            product = await build_product(services)
            modules = product.modules
            register_domain_tasks(modules, services)
            from tianshu.models.management import register_routes

            model_module = DomainModule("models", register_routes)
            app = create_app(
                services,
                (
                    model_module,
                    *modules,
                    DomainModule(
                        "access",
                        lambda app, runtime: access.register(
                            app, runtime, product.authority.identity
                        ),
                    ),
                ),
            )
            from tianshu.models.proxy import register_native_proxy

            register_native_proxy(app, settings)
            if settings.get("http", {}).get("static_directory"):
                register_static(app, settings["http"]["static_directory"])
            registration = {"domains": [module.name for module in modules]}

            async def ready():
                registration["modules"] = services.module_status
                if getattr(product, "channels", None):
                    try:
                        registration["channels"] = await product.channels.status()
                    except Exception as exc:
                        registration["channels"] = {"ready": False, "error": type(exc).__name__}
                if getattr(product, "capabilities", None):
                    try:
                        registration["actions"] = product.capabilities.actions.status()
                    except Exception as exc:
                        registration["actions"] = {"ready": False, "error": type(exc).__name__}
                return True
        else:
            product = await build_product(services)
            modules = product.modules
            failed = register_domain_tasks(modules, services)
            groups = role_worker_groups(worker_settings, role)
            if groups:
                await jobs.open()
                stack.push_async_callback(jobs.close)
            for module in modules:
                if module.name in failed:
                    continue
                if await start_module(module, services) and module.stop:
                    stack.push_async_callback(module.stop, services)
                if services.module_status.get(module.name, {}).get("state") == "unavailable":
                    failed.add(module.name)
            # Never claim a queue whose required domain failed to initialize.
            disabled = {lane.value for task, lane in TASK_LANES.items()
                        if task.split(".", 1)[0] in failed}
            groups = {name: {**group, "queues": [queue for queue in group["queues"] if queue not in disabled]}
                      for name, group in groups.items()}
            groups = {name: group for name, group in groups.items() if group["queues"]}
            # Domain starts may register prepared owners and their work tasks.
            # Apply ordinary queue-ownership changes only after the complete
            # graph is ready, before consumers can claim affected pending jobs.
            reassigned = await jobs.route_reassigned_pending(database, role)
            registration = {
                "tasks": sorted(jobs.tasks), "groups": groups,
                "queue_listeners": len(groups),
                "reassigned_control_jobs": reassigned,
                "modules": services.module_status, "disabled_queues": sorted(disabled),
                "consumers": {name: {} for name in groups},
            }
            workers = []
            for group_name, group in groups.items():
                workers.append(
                    asyncio.create_task(
                        supervise_consumer(jobs, group_name, group, registration, stop,
                            abort_poll_seconds=worker_settings.get("abort_poll_seconds", 2))
                    )
                )
            maintenance = start_domain_maintenance(modules, services, stop)
            stack.push_async_callback(stop_domain_maintenance, maintenance)

            async def worker_ready():
                if role == "operations":
                    if services.role_ready is None:
                        registration["agent"] = {"ready": False, "reason": "agent_status_not_registered"}
                        return False
                    status = services.role_ready()
                    if inspect.isawaitable(status):
                        status = await status
                    registration["agent"] = status if isinstance(status, dict) else {"ready": bool(status)}
                    return bool(registration["agent"].get("ready"))
                return bool(groups) and all(not task.done() for task in workers) and all(
                    item.get("state") == "running" for item in registration["consumers"].values())

            heartbeat = ProcessHeartbeat(database, role, registration, worker_ready, jobs=jobs)
            await heartbeat.start()
            try:
                await events.emit("runtime.started", "succeeded")
            except Exception:
                pass
            stack.push_async_callback(heartbeat.close)
            stop_wait = asyncio.create_task(stop.wait())
            recovery = (asyncio.create_task(
                recover_notifications(jobs, stop, role, reroute_more=reassigned == 128),
                name="recompute-recovery") if jobs.recompute_tasks or reassigned == 128 else None)
            try:
                await stop_wait
            finally:
                stop_wait.cancel()
                if recovery:
                    recovery.cancel()
                for task in workers:
                    task.cancel()
                await asyncio.gather(stop_wait, *([recovery] if recovery else []), *workers, return_exceptions=True)
            return
        app["event_log"] = events
        runner = web.AppRunner(
            app,
            access_log=None,
            handler_cancellation=True,
            shutdown_timeout=settings.get("http", {}).get("drain_seconds", 30),
        )
        await runner.setup()
        stack.push_async_callback(runner.cleanup)
        if role == "interactive":
            maintenance = start_domain_maintenance(modules, services, stop)
            stack.push_async_callback(stop_domain_maintenance, maintenance)
        await start_sites(runner, settings, role)
        heartbeat = ProcessHeartbeat(database, role, registration, ready, jobs=jobs)
        await heartbeat.start()
        try:
            await events.emit("runtime.started", "succeeded")
        except Exception:
            pass
        stack.push_async_callback(heartbeat.close)
        if role == "model-broker":
            stop_wait = asyncio.create_task(stop.wait())
            done, _ = await asyncio.wait(
                [
                    stop_wait,
                    heartbeat.task,
                    broker.owner_watch,
                    broker.external_watch,
                    broker.scheduler.task,
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if any(task is not stop_wait for task in done) and not stop.is_set():
                stop_wait.cancel()
                raise RuntimeError("broker_owner_lost")
        else:
            stop_wait = asyncio.create_task(stop.wait())
            done, _ = await asyncio.wait(
                [stop_wait, heartbeat.task], return_when=asyncio.FIRST_COMPLETED
            )
            stop_wait.cancel()
            await asyncio.gather(stop_wait, return_exceptions=True)
            if heartbeat.task in done:
                heartbeat.task.result()
                raise RuntimeError("process_heartbeat_stopped")
