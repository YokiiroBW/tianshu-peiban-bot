"""The real domain graph registers routes/tasks against the one fresh product baseline."""

import pytest

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
from tianshu.runtime.process import register_domain_tasks


@pytest.mark.parametrize("role", ["interactive", "bot-foreground", "bot-background", "independent", "operations"])
async def test_actual_product_has_one_artifact_owner_and_complete_registrations(empty_database, tmp_path, role):
    database = empty_database
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
    jobs = JobQueue(database.config.dsn, database=database)
    await migrate(database, product_migrations(jobs))
    runtime = RuntimeServices(
        database,
        jobs,
        ModelClient("http://127.0.0.1:9", "not-opened"),
        FileStore(tmp_path / "files"),
        settings,
        role=role,
    )
    runtime.commands = CommandStore(database)
    runtime.credentials = CredentialStore(database, settings.secrets)
    product = await build_product(runtime)
    try:
        assert not register_domain_tasks(product.modules, runtime), runtime.module_status
        names = {module.name for module in product.modules}
        assert len(names) == len(product.modules)
        if role == "operations":
            assert names == {"operations"}
            assert not jobs.tasks
            assert product.capabilities is None and product.channels is None
            assert {"memory.consolidation.resume", "memory.index.rebuild", "growth.retry.resume"} <= product.operations.executor.actions.keys()
            return
        assert product.capabilities.artifacts is product.authority.artifacts
        assert product.capabilities.reading.reading is not None
        assert product.capabilities.prepared and not product.capabilities.initialized
        if role == "independent":
            assert names == {"capabilities", "channels"}
            assert "conversation.run" not in jobs.tasks
            assert "memory.consolidate" not in jobs.tasks
            return
        if role == "bot-foreground":
            assert names == {"capabilities", "channels", "conversation"}
            assert "memory.consolidate" not in jobs.tasks
            assert "conversation.run" in jobs.tasks
            return
        assert {
            "memory.consolidate",
            "sources.propagate",
            "companion.organize",
            "life.activate",
            "life.generation_control",
            "conversation.run",
            "conversation.result",
            "channels.ingest",
            "channels.cancel",
        } <= jobs.tasks.keys()
        assert "memory.extract" not in jobs.tasks and "memory.observe_legacy" not in jobs.tasks
        assert {"capabilities.execute", "conversation.run"} <= jobs.lane_resolvers.keys()
        if role == "bot-background":
            assert "identity" not in names and "operations" not in names
            return
        app = create_app(runtime, (DomainModule("models", register_routes), *product.modules))
        paths = {route.resource.canonical for route in app.router.routes()}
        assert {
            "/api/v1/session",
            "/api/v1/commands/get",
            "/api/v1/sources/read",
            "/api/v1/memory/projection",
            "/api/v1/conversation/send",
        } <= paths
    finally:
        if product.capabilities is not None:
            await product.capabilities.close()
        if product.channels is not None:
            await product.channels.close()
