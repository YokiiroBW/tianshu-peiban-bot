"""Three bounded process roles and honest readiness backed by actual registrations."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import importlib
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
from .migrations import Migration, foundation_migrations, migrate


def load_modules(paths):
    modules = []
    for path in paths:
        package, separator, attribute = path.partition(":")
        if not separator or not package.startswith("tianshu."):
            raise ValueError("explicit_product_module_required")
        module = getattr(importlib.import_module(package), attribute)
        if not isinstance(module, DomainModule):
            raise TypeError("domain_module_required")
        modules.append(module)
    return tuple(modules)


class ProcessHeartbeat:
    def __init__(self, database, role, registration, ready):
        self.database, self.role, self.registration, self.is_ready = database, role, registration, ready
        self.instance_id = str(uuid4())
        self.task = None

    async def update(self):
        async with self.database.transaction(control=True) as conn:
            await conn.execute("INSERT INTO runtime.processes(instance_id,role,ready,registration) "
                "VALUES(%s,%s,%s,%s) ON CONFLICT(instance_id) DO UPDATE SET ready=excluded.ready,"
                "registration=excluded.registration,heartbeat_at=now()",
                (self.instance_id, self.role, bool(self.is_ready()), Jsonb(self.registration)))

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
            await conn.execute("DELETE FROM runtime.processes WHERE instance_id=%s", (self.instance_id,))


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
    modules = load_modules(settings.modules)
    database = Database(settings.database(role))
    worker_settings = settings.document.get("worker", {})
    jobs = JobQueue(database.config.dsn, max_connections=worker_settings.get("queue_connections", 8))
    files = FileStore(settings.artifact_directory)
    client = ModelClient(settings.broker_url, settings.broker_token)
    services = RuntimeServices(database, jobs, client, files, settings, role)
    services.commands = CommandStore(database)
    services.credentials = CredentialStore(database, settings.secrets,
        settings.document.get("credentials", {}).get("master_key_ref", "TIANSHU_MASTER_KEY"))
    for module in modules:
        if module.register_tasks:
            module.register_tasks(jobs, services)
    async with AsyncExitStack() as stack:
        await database.open()
        stack.push_async_callback(database.close)
        if initialize:
            migrations = foundation_migrations() + (Migration("0003_procrastinate",
                jobs.app.schema_manager.get_schema()),) + tuple(
                    migration for module in modules for migration in module.migrations)
            await migrate(database, migrations)
            return
        events = EventLog(settings.artifact_directory.parent/"logs", role)
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
            broker = ModelBroker(database, files, settings.secrets, events=events,
                                 credential_store=services.credentials)
            await broker.open()
            stack.push_async_callback(broker.close)
            app = create_broker_app(broker, settings.broker_token,
                                    settings.document.get("broker", {}).get("external_keys", []))
            registration = {"protocols": sorted(NATIVE_PROTOCOLS),
                            "quota_groups": list(broker.scheduler.policies)}
            ready = lambda: broker.ready and events.ready
        elif role == "interactive":
            from tianshu.models.management import register_routes
            model_module = DomainModule("models", register_routes)
            app = create_app(services, (model_module, *modules))
            registration = {"domains": [module.name for module in modules]}
            ready = lambda: bool(modules) and events.ready
        else:
            await jobs.open()
            stack.push_async_callback(jobs.close)
            registration = {"tasks": sorted(jobs.tasks)}
            ready = lambda: bool(jobs.tasks)
            for module in modules:
                if module.start:
                    await module.start(services)
                if module.stop:
                    stack.push_async_callback(module.stop, services)
            workers = []
            for queue, count in (("due", worker_settings.get("due_concurrency", 1)),
                                 ("elastic", worker_settings.get("elastic_concurrency", 2))):
                if count < 1:
                    raise ValueError("positive_worker_share_required")
                workers.append(asyncio.create_task(jobs.app.run_worker_async(
                    queues=[queue], concurrency=count, name="tianshu-"+queue,
                    install_signal_handlers=False, shutdown_graceful_timeout=30,
                    fetch_job_polling_interval=30, abort_job_polling_interval=10)))
            heartbeat = ProcessHeartbeat(database, role, registration,
                lambda: bool(jobs.tasks) and events.ready and all(not task.done() for task in workers))
            await heartbeat.start()
            await events.emit("runtime.started", "succeeded")
            stack.push_async_callback(heartbeat.close)
            stop_wait = asyncio.create_task(stop.wait())
            try:
                done, _ = await asyncio.wait([stop_wait, *workers], return_when=asyncio.FIRST_COMPLETED)
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
        runner = web.AppRunner(app, access_log=None, handler_cancellation=True)
        await runner.setup()
        stack.push_async_callback(runner.cleanup)
        http = settings.document.get("broker" if role == "model-broker" else "http", {})
        await web.TCPSite(runner, http.get("host", "127.0.0.1"),
                          http.get("port", 8071 if role == "model-broker" else 8070)).start()
        heartbeat = ProcessHeartbeat(database, role, registration, ready)
        await heartbeat.start()
        await events.emit("runtime.started", "succeeded")
        stack.push_async_callback(heartbeat.close)
        if role == "model-broker":
            stop_wait = asyncio.create_task(stop.wait())
            done, _ = await asyncio.wait([stop_wait, broker.owner_watch, broker.external_watch,
                                          broker.scheduler.task], return_when=asyncio.FIRST_COMPLETED)
            if any(task is not stop_wait for task in done) and not stop.is_set():
                stop_wait.cancel()
                raise RuntimeError("broker_owner_lost")
        else:
            await stop.wait()
