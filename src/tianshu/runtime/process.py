"""Three bounded process roles and honest readiness backed by actual registrations."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import inspect
import signal
from uuid import uuid4

from aiohttp import web
from psycopg.types.json import Jsonb

from tianshu.models.broker import ModelBroker
from tianshu.models.client import ModelClient
from tianshu.models.http import create_broker_app
from tianshu.models.catalog import NATIVE_PROTOCOLS
from .composition import DomainModule, RuntimeServices, create_app
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


class ProcessHeartbeat:
    def __init__(self, database, role, registration, ready):
        self.database, self.role, self.registration, self.is_ready = (
            database,
            role,
            registration,
            ready,
        )
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
        async with self.database.transaction(control=True) as conn:
            await conn.execute(
                "INSERT INTO runtime.processes(instance_id,role,ready,registration) "
                "VALUES(%s,%s,%s,%s) ON CONFLICT(instance_id) DO UPDATE SET ready=excluded.ready,"
                "registration=excluded.registration,heartbeat_at=now()",
                (self.instance_id, self.role, bool(current), Jsonb(wire(self.registration))),
            )

    async def run(self):
        while True:
            await self.update()
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


async def run(settings, role, *, initialize=False):
    database = Database(settings.database(role))
    worker_settings = settings.document.get("worker", {})
    jobs = JobQueue(
        database.config.dsn, max_connections=worker_settings.get("queue_connections", 8)
    )
    files = FileStore(settings.artifact_directory)
    client = ModelClient(settings.broker_url, settings.broker_token)
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
            await migrate(database, product_migrations(jobs))
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
        services.diagnostics = RuntimeDiagnostics(database, client, settings)
        await events.emit("runtime.starting", "started")
        await client.open()
        stack.push_async_callback(client.close)
        stop = stop_event()
        if role == "model-broker":
            authority = build_authority(services)
            broker = ModelBroker(
                database,
                files,
                settings.secrets,
                events=events,
                validate_submission=authority.identity.check,
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
            ready = lambda: broker.ready and events.ready
        elif role == "interactive":
            product = await build_product(services)
            modules = product.modules
            for module in modules:
                if module.register_tasks:
                    module.register_tasks(jobs, services)
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
                status = await product.channels.status()
                registration["channels"] = status
                actions = product.capabilities.actions.status()
                registration["actions"] = actions
                return (
                    product.capabilities.initialized
                    and events.ready
                    and status["ready"]
                    and actions["ready"]
                )
        else:
            product = await build_product(services)
            modules = product.modules
            for module in modules:
                if module.register_tasks:
                    module.register_tasks(jobs, services)
            await jobs.open()
            stack.push_async_callback(jobs.close)
            shares = {
                "interactive": worker_settings.get("interactive_concurrency", 2),
                "due": worker_settings.get("due_concurrency", 1),
                "elastic": worker_settings.get("elastic_concurrency", 2),
            }
            registration = {"tasks": sorted(jobs.tasks), "queues": shares}
            for module in modules:
                if module.stop:
                    stack.push_async_callback(module.stop, services)
                if module.start:
                    await module.start(services)
            workers = []
            for queue, count in shares.items():
                if count < 1:
                    raise ValueError("positive_worker_share_required")
                workers.append(
                    asyncio.create_task(
                        jobs.app.run_worker_async(
                            queues=[queue],
                            concurrency=count,
                            name="tianshu-" + queue,
                            install_signal_handlers=False,
                            shutdown_graceful_timeout=30,
                            fetch_job_polling_interval=30,
                            abort_job_polling_interval=worker_settings.get("abort_poll_seconds", 2),
                        )
                    )
                )

            async def worker_ready():
                status = await product.channels.status()
                registration["channels"] = status
                actions = product.capabilities.actions.status()
                registration["actions"] = actions
                return (
                    bool(jobs.tasks)
                    and product.capabilities.initialized
                    and events.ready
                    and status["ready"]
                    and actions["ready"]
                    and all(not task.done() for task in workers)
                )

            heartbeat = ProcessHeartbeat(database, role, registration, worker_ready)
            await heartbeat.start()
            await events.emit("runtime.started", "succeeded")
            stack.push_async_callback(heartbeat.close)
            stop_wait = asyncio.create_task(stop.wait())
            try:
                done, _ = await asyncio.wait(
                    [stop_wait, heartbeat.task, *workers], return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    if task is not stop_wait:
                        task.result()
            finally:
                stop_wait.cancel()
                for task in workers:
                    task.cancel()
                await asyncio.gather(stop_wait, *workers, return_exceptions=True)
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
        await start_sites(runner, settings, role)
        heartbeat = ProcessHeartbeat(database, role, registration, ready)
        await heartbeat.start()
        await events.emit("runtime.started", "succeeded")
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
