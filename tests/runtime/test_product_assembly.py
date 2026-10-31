"""The real domain graph registers routes/tasks against the one fresh product baseline."""

from tianshu.models.client import ModelClient
from tianshu.models.management import register_routes
from tianshu.runtime.commands import CommandStore
from tianshu.runtime.composition import DomainModule, RuntimeServices, create_app
from tianshu.runtime.config import Secrets, Settings
from tianshu.runtime.credentials import CredentialStore
from tianshu.runtime.files import FileStore
from tianshu.runtime.jobs import JobQueue
from tianshu.runtime.migrations import migrate, product_migrations
from tianshu.runtime.product import build_product


async def test_actual_product_has_one_artifact_owner_and_complete_registrations(database, tmp_path):
    settings = Settings(
        {
            "identity": {"installation_seal": str(tmp_path / "installed")},
            "artifacts": str(tmp_path / "files"),
            "capabilities": {"state_directory": str(tmp_path / "caps")},
            "channels": {"owner_url": "http://127.0.0.1:8070", "owner_token_ref": "OWNER"},
        },
        Secrets({"OWNER": "synthetic-private-owner"}),
        tmp_path,
    )
    jobs = JobQueue(database.config.dsn)
    await migrate(database, product_migrations(jobs))
    runtime = RuntimeServices(
        database,
        jobs,
        ModelClient("http://127.0.0.1:9", "not-opened"),
        FileStore(tmp_path / "files"),
        settings,
    )
    runtime.commands = CommandStore(database)
    runtime.credentials = CredentialStore(database, settings.secrets)
    product = await build_product(runtime)
    try:
        assert product.capabilities.artifacts is product.authority.artifacts
        assert product.capabilities.reading.reading is not None
        assert product.capabilities.prepared and not product.capabilities.initialized
        for module in product.modules:
            if module.register_tasks:
                module.register_tasks(jobs, runtime)
        assert {
            "memory.extract",
            "sources.propagate",
            "companion.organize",
            "life.activate",
            "life.generation_control",
            "conversation.run",
            "conversation.result",
            "channels.ingest",
            "channels.cancel",
        } <= jobs.tasks.keys()
        app = create_app(runtime, (DomainModule("models", register_routes), *product.modules))
        paths = {route.resource.canonical for route in app.router.routes()}
        assert {
            "/api/v1/session",
            "/api/v1/commands/get",
            "/api/v1/sources/read",
            "/api/v1/memory/projection",
            "/api/v1/conversation/send",
        } <= paths
        assert len({module.name for module in product.modules}) == len(product.modules)
    finally:
        await product.capabilities.close()
