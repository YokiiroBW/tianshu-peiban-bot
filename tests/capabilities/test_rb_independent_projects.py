"""A global project uses the author's system scope and the existing project store."""

from dataclasses import replace
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.capabilities.contracts import Fault
from tianshu.capabilities.projects.registry import Registry
from tianshu.capabilities.projects.service import Projects
from tianshu.capabilities.projects.store import ProjectStore
from tianshu.capabilities.projects.types import ProjectSettings
from tianshu.runtime.migrations import Migration, migrate
from tianshu.types import Action, OriginKind, TrustedContext


@pytest.mark.asyncio
async def test_global_project_registration_scene_and_owner_isolation(database, tmp_path):
    await migrate(database, (
        Migration("capabilities-projects-0001", files("tianshu.capabilities.projects").joinpath("schema.sql").read_text()),
        Migration("capabilities-projects-0002-independent", files("tianshu.capabilities.projects").joinpath("independent.sql").read_text()),
    ))
    root = tmp_path / "registered"
    root.mkdir()
    authority = SimpleNamespace(check=AsyncMock(), intake_scope=AsyncMock(return_value=(("system-owner",), {})))
    sources = SimpleNamespace(validate_persistence=AsyncMock(return_value=True))
    store = ProjectStore(SimpleNamespace(database=database, credentials=None), authority, sources)

    async def local(operation, *args):
        return operation(*args)

    registry = Registry(store, ProjectSettings((str(tmp_path),)), local)
    owner = TrustedContext("owner", None, None, None, ("system-owner", "unrelated"),
        ("system-owner",), 4, OriginKind.HUMAN, frozenset({"config.publish", "projects.read"}))
    action = Action("action", "intent", "projects.registry.save", None, owner,
        {"project_id": "independent", "label": "Independent", "root": str(root), "expected_revision": 0},
        OriginKind.HUMAN)
    saved = await registry.save(action)
    assert saved["actor_id"] is None and saved["conversation_id"] is None
    assert saved["scope_ids"] == ["system-owner"]
    assert (await registry.view(owner, {}))["items"][0]["project_id"] == "independent"

    projects = SimpleNamespace(store=store, authorize_conversation=AsyncMock())
    scene = await Projects.scene(projects, owner, "independent")
    assert scene.actor_id is None and scene.conversation_id is None
    assert scene.scope_ids == ("system-owner",) and scene.audience == ("system-owner",)
    projects.authorize_conversation.assert_not_awaited()
    async with database.transaction() as conn:
        assert (await store.lock_project(conn, scene, saved))["project_id"] == "independent"

    with pytest.raises(Fault):
        await store.project(replace(owner, principal_id="another-author"), "independent")
    with pytest.raises(Fault):
        await store.project(replace(owner, scope_ids=("unrelated",)), "independent")
    role_without_scene = replace(action, actor_id="actor", context=replace(owner, actor_id="actor"))
    with pytest.raises(Fault) as rejected:
        await registry.save(role_without_scene)
    assert rejected.value.code == "project_scene_required"
