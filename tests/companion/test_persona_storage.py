"""Personas are editable documents; revisions only protect concurrent updates."""

from dataclasses import replace
from importlib.resources import files
from unittest.mock import AsyncMock

import pytest

from tianshu.companion.module import create_module
from tianshu.companion.service import CompanionService
from tianshu.runtime.commands import CommandStore
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import Migration, migrate, product_migrations
from tianshu.types import OriginKind, TrustedContext


def sql(name):
    return files("tianshu.companion").joinpath(name).read_text(encoding="utf-8")


def service(database):
    authority = AsyncMock()
    companion = CompanionService(
        database, CommandStore(database), authority, None, actor_registry=authority,
        actor_changed=AsyncMock(), sources=None, actor_activated=AsyncMock(),
    )
    context = TrustedContext("owner", None, None, None, (), (), 1, OriginKind.HUMAN,
                             permissions=frozenset({"*"}))
    return companion, context


async def install(database):
    await migrate(database, (
        Migration("0030_companion", sql("schema.sql")),
        Migration("persona_storage", sql("persona_storage.sql")),
    ))


def test_storage_upgrade_is_registered_in_both_installation_paths():
    for migrations in (product_migrations(), create_module(None, None, None).migrations):
        assert [item.sql for item in migrations if item.name == "persona_storage"] == [
            sql("persona_storage.sql")
        ]


async def test_edit_updates_one_document_and_all_bound_roles_without_new_history(database):
    await install(database)
    companion, context = service(database)
    body = {"client_request_id": "new", "name": "澄汐", "author_text": "原人格",
            "tone": "温柔", "world": "日常", "expected_revision": None}
    created = await companion.save_persona(context, body)
    identifier = created.data["persona_id"]
    async with database.transaction() as connection:
        for actor in ("first", "second"):
            await connection.execute(
                "INSERT INTO companion.actors(actor_id,name,persona_id,persona_revision) VALUES(%s,%s,%s,1)",
                (actor, actor, identifier))
    edit = {**body, "client_request_id": "edit", "persona_id": identifier,
            "expected_revision": 1, "author_text": "更新的人格\n保留全文", "tone": "新的语气"}
    saved = await companion.save_persona(context, edit)
    assert saved.data == {"persona_id": identifier, "revision": 2}
    current = await companion.persona(context, identifier)
    assert (current.revision, current.author_text, current.tone, current.world) == (
        2, edit["author_text"], edit["tone"], "日常")
    assert len((await companion.persona_catalog(context))["items"]) == 1
    # An HTTP retry cannot apply the same edit twice; an older editor cannot overwrite it.
    await companion.save_persona(context, edit)
    with pytest.raises(AppError, match="revision_conflict"):
        await companion.save_persona(context, {**edit, "client_request_id": "stale"})
    with pytest.raises(AppError, match="revision_conflict"):
        await companion.persona(context, identifier, 1)
    async with database.transaction(read_only=True) as connection:
        assert (await (await connection.execute(
            "SELECT count(*) AS n FROM companion.persona_revisions")).fetchone())["n"] == 0
        actors = await (await connection.execute(
            "SELECT revision,persona_revision FROM companion.actors ORDER BY actor_id")).fetchall()
    assert actors == [{"revision": 2, "persona_revision": 2}] * 2


async def test_role_binding_always_uses_current_document_instead_of_a_submitted_old_version(database):
    await install(database)
    companion, context = service(database)
    created = await companion.save_persona(context, {
        "client_request_id": "new", "name": "人格", "author_text": "正文"})
    identifier = created.data["persona_id"]
    await companion.save_persona(context, {"client_request_id": "edit", "persona_id": identifier,
        "expected_revision": 1, "name": "人格", "author_text": "新正文"})
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO companion.actors(actor_id,name) VALUES('actor','角色')")
    context = replace(context, actor_id="actor")
    await companion.save_actor(context, {
        "client_request_id": "bind", "name": "角色", "enabled": False,
        "persona_id": identifier, "persona_revision": 1, "expected_revision": 1})
    actor = await companion.actor(context)
    assert actor["persona_revision"] == 2
    assert (await companion.persona(context, identifier, actor["persona_revision"])).author_text == "新正文"


async def test_upgrade_collapses_only_untouched_import_copies_and_preserves_original_text(database):
    await migrate(database, (Migration("0030_companion", sql("schema.sql")),))
    async with database.transaction() as connection:
        for identifier, name, text, note in (
            ("applied", "澄汐（当前已应用快照）", "旧正文", "配置迁移；保留原作者内容；旧来源=actor:old;revision=x"),
            ("draft", "澄汐（作者草稿档案·未绑定）", "不同的旧草稿", "配置迁移；保留原作者内容；旧来源=persona-profile:old;revision=y"),
            ("custom", "澄汐（作者草稿档案·未绑定）", "用户自己新建的档案", "自己新建"),
            ("edited", "澄汐（作者草稿档案·未绑定）", "之后编辑过的草稿", "配置迁移；保留原作者内容；旧来源=persona-profile:edited;revision=z"),
        ):
            await connection.execute(
                "INSERT INTO companion.persona_profiles(persona_id,name) VALUES(%s,%s)", (identifier, name))
            await connection.execute(
                "INSERT INTO companion.persona_revisions(persona_id,revision,author_text,note) VALUES(%s,1,%s,%s)",
                (identifier, text, note))
        for identifier in ("applied", "edited"):
            await connection.execute(
                "INSERT INTO companion.persona_revisions(persona_id,revision,author_text) VALUES(%s,2,'最新保存的正文')",
                (identifier,))
            await connection.execute(
                "UPDATE companion.persona_profiles SET revision=2,current_revision=2 WHERE persona_id=%s", (identifier,))
        await connection.execute(
            "INSERT INTO companion.actors(actor_id,name,persona_id,persona_revision) VALUES('actor','角色','applied',1)")
    await migrate(database, (Migration("persona_storage", sql("persona_storage.sql")),))
    companion, context = service(database)
    catalog = await companion.persona_catalog(context)
    assert {item["persona_id"] for item in catalog["items"]} == {"applied", "custom", "edited"}
    persona = await companion.persona(context, "applied")
    assert (persona.name, persona.author_text, persona.revision) == ("澄汐", "最新保存的正文", 2)
    with pytest.raises(AppError, match="persona_not_found"):
        await companion.persona(context, "draft")
    with pytest.raises(AppError, match="persona_not_found"):
        await companion.save_persona(context, {"client_request_id": "revive", "persona_id": "draft",
            "expected_revision": 1, "name": "草稿", "author_text": "覆盖"})
    async with database.transaction(read_only=True) as connection:
        draft = await (await connection.execute(
            "SELECT author_text FROM companion.persona_profiles WHERE persona_id='draft'")).fetchone()
        actor = await (await connection.execute(
            "SELECT persona_id,persona_revision,revision FROM companion.actors WHERE actor_id='actor'")).fetchone()
        original = await (await connection.execute(
            "SELECT author_text FROM companion.persona_revisions WHERE persona_id='applied' AND revision=1")).fetchone()
    assert draft["author_text"] == "不同的旧草稿"
    assert original["author_text"] == "旧正文"
    assert actor == {"persona_id": "applied", "persona_revision": 2, "revision": 2}
