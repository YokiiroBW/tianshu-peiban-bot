"""Scene identity is stable while the authority snapshots remain restrictive."""

from dataclasses import replace
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from tianshu.identity.service import IdentityService
from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind, TrustedContext


_spec = spec_from_file_location("rb_core_fixtures", Path(__file__).parents[1] / "core" / "conftest.py")
_fixtures = module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)
core = _fixtures.core


def test_scene_identity_excludes_grants_epochs_and_group_speaker():
    original = TrustedContext(
        "owner", "actor", "alice", "room", ("one",), ("one",), 1,
        OriginKind.HUMAN, frozenset({"memory.read"}), "group", False,
    )
    updated = replace(original, principal_id="other-viewer", person_id="bob", scope_ids=("two",),
                      permissions=frozenset({"chat"}), access_epoch=9)
    assert IdentityService.scene_id(original) == IdentityService.scene_id(updated)
    assert IdentityService.scene_id(original) != IdentityService.scene_id(
        replace(original, conversation_id="another-room")
    )
    assert IdentityService.scene_id(replace(original, conversation_id=None, person_id="alice")) != (
        IdentityService.scene_id(replace(updated, conversation_id=None, person_id="bob"))
    )


async def test_scheduled_global_scope_is_intersection_and_scene_is_stable(core):
    principal = core.owner.principal_id
    async with core.database.transaction() as conn:
        row = await (await conn.execute(
            "SELECT person_id FROM identity.principals WHERE principal_id=%s", (principal,)
        )).fetchone()
        for scope in ("rb-kept", "rb-lost"):
            await conn.execute(
                "INSERT INTO identity.scopes(scope_id,person_id,kind,label) VALUES(%s,%s,'system',%s)",
                (scope, row["person_id"], scope),
            )
            await conn.execute(
                "INSERT INTO identity.scope_grants VALUES(%s,%s,ARRAY['read'])", (scope, principal)
            )
        await conn.execute(
            "UPDATE identity.principals SET access_epoch=access_epoch+1 WHERE principal_id=%s",
            (principal,),
        )
    saved = await core.identity.context(principal)
    async with core.database.transaction() as conn:
        before = await core.identity.scene_authority_on(conn, saved)
        await conn.execute("DELETE FROM identity.scope_grants WHERE scope_id='rb-lost' AND principal_id=%s", (principal,))
        await conn.execute(
            "INSERT INTO identity.scopes(scope_id,person_id,kind,label) VALUES('rb-new',%s,'system','new')",
            (row["person_id"],),
        )
        await conn.execute("INSERT INTO identity.scope_grants VALUES('rb-new',%s,ARRAY['read'])", (principal,))
        await conn.execute(
            "UPDATE identity.principals SET access_epoch=access_epoch+1 WHERE principal_id=%s",
            (principal,),
        )
    refreshed = await core.identity.refresh_scheduled_context(
        saved, permission="projects.read", require_actor=False
    )
    assert refreshed.actor_id is None
    assert "rb-kept" in refreshed.scope_ids
    assert "rb-lost" not in refreshed.scope_ids and "rb-new" not in refreshed.scope_ids
    assert core.identity.scene_id(refreshed) == before.scene_id
    assert refreshed.access_epoch > before.principal_epoch


async def test_system_context_requires_real_principal_and_current_permission(core):
    context = await core.identity.system_context(core.owner.principal_id, permission="config.publish")
    assert context.origin_kind == OriginKind.SYSTEM and context.actor_id is None
    assert set(context.scope_ids) == set(core.owner.scope_ids)
    with pytest.raises(AppError):
        await core.identity.system_context("not-a-real-principal", permission="config.publish")
    with pytest.raises(AppError):
        await core.identity.system_context(core.owner.principal_id, permission="")
