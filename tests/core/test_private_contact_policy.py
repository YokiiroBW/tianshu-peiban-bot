from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from tianshu.identity.role_administrator import RoleAdministrator
from tianshu.identity.types import IncomingEvent
from tianshu.life.attention import AttentionPolicy
from tianshu.life.contacts import Contacts
from tianshu.life.private_contact_policy import PrivateContactPolicy
from tianshu.life.service import LifeService
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import Migration, migrate
from tianshu.types import OriginKind


@pytest_asyncio.fixture
async def private_policy(core):
    await migrate(core.database, (
        Migration("0031_life", files("tianshu.life").joinpath("schema.sql").read_text()),
        Migration("0033_life_contact_expiry", files("tianshu.life").joinpath("contact_expiry.sql").read_text()),
        Migration("private_contact_default", files("tianshu.life").joinpath("private_contact_default.sql").read_text()),
    ))
    await core.management.execute(core.owner, "connections.save", {
        "actor_id": core.actor, "connection_id": "private", "namespace": "qq",
        "self_external_id": "bot", "expected_revision": 0, "client_request_id": "connect",
    })
    incoming = IncomingEvent("qq:private", "first", 1, datetime.now(UTC), "private:123", "private", "123",
        parts=({"kind": "text", "text": "你好"},))
    admitted = await core.identity.admit_event("private", incoming)
    context = await core.identity.context(core.owner.principal_id, actor_id=core.actor,
        person_id=admitted.context.person_id)
    life = LifeService(core.database, core.memory.commands, core.memory.jobs, core.identity,
        None, Contacts(core.database), sources=core.sources, memory=core.memory)
    return PrivateContactPolicy(life), context, incoming


@pytest.mark.parametrize("mode", ["disabled", "passive", "active"])
async def test_private_states_separate_ingress_from_ordinary_outreach(core, private_policy, mode):
    policy, context, incoming = private_policy
    current = await policy.view(context)
    assert current["mode"] == "passive"
    body = {"mode": mode, "policy_token": current["token"], "client_request_id": "state-" + mode,
        "expected_revision": current["contact_revision"]}
    await policy.save(context, body)
    assert (await policy.view(context))["mode"] == mode
    preferences = await policy.life.contacts.preferences(context)
    assert preferences["enabled"] is (mode == "active")
    from dataclasses import replace
    decision = await core.identity.admit_event("private", replace(incoming, event_id="after-setting"))
    assert decision.disposition == ("ignore" if mode == "disabled" else "reply")
    assert bool(decision.context and decision.context.delivery_allowed) is (mode != "disabled")
    if mode == "disabled":
        async with core.database.transaction(read_only=True) as conn:
            assert not await (await conn.execute("SELECT 1 FROM identity.events WHERE event_id='after-setting'")).fetchone()
    now = datetime.now(UTC)
    candidate = {"kind": "experience", "created_at": now, "expires_at": now + timedelta(hours=1),
        "due_at": now, "target_ref": "private", "explicit": False,
        "payload": {"salience": {"score": 1, "thresholds": {"engage": 0}}}}
    preferences.update(timezone="UTC", quiet_start="00:00", quiet_end="00:00")
    assert AttentionPolicy().decide(candidate, preferences, [], now=now).decision == ("send" if mode == "active" else "archive")
    candidate["kind"] = "reply"
    assert AttentionPolicy().decide(candidate, preferences, [], now=now).decision == "send"
    original_revision = preferences["revision"]
    replay = await policy.save(context, body)
    assert replay.revision == original_revision


async def test_private_policy_rollback_and_stale_tokens_preserve_both_owners(core, private_policy):
    policy, context, _ = private_policy
    current = await policy.view(context)
    original = policy.management.execute
    policy.management.execute = AsyncMock(side_effect=RuntimeError("simulated-link-write-failure"))
    body = {"mode": "active", "policy_token": current["token"], "client_request_id": "atomic",
        "expected_revision": current["contact_revision"]}
    with pytest.raises(RuntimeError, match="simulated-link-write-failure"):
        await policy.save(context, body)
    assert (await policy.view(context))["token"] == current["token"]
    policy.management.execute = original
    await policy.save(context, body)
    with pytest.raises(AppError, match="revision_conflict"):
        await policy.save(context, {**body, "client_request_id": "stale", "mode": "disabled"})
    assert (await policy.view(context))["mode"] == "active"


async def test_disabled_default_does_not_change_existing_group_budget(core, private_policy):
    policy, context, _ = private_policy
    assert (await policy.life.contacts.preferences(context))["enabled"] is False
    assert (await policy.life.contacts.preferences(context, group=True))["enabled"] is True
    async with core.database.transaction() as conn:
        await conn.execute("INSERT INTO life.contact_preferences(actor_id,person_id,scope_ids,enabled) VALUES(%s,'',%s,true)",
            (core.actor, list(context.scope_ids)))
        await conn.execute(files("tianshu.life").joinpath("private_contact_default.sql").read_text())
    assert (await policy.life.contacts.preferences(context, group=True))["enabled"] is True
    assert (await policy.life.contacts.preferences(context))["enabled"] is False


async def test_channel_administrator_applies_in_private_and_group_but_not_other_senders(core, private_policy):
    _, context, incoming = private_policy
    administrators = RoleAdministrator(core.identity)
    body = {"enabled": True, "principal_id": core.owner.principal_id,
        "expected_revision": 0, "client_request_id": "bind"}
    await administrators.save(context, body)
    await administrators.save(context, body)
    assert (await administrators.view(context))["revision"] == 1
    private = await core.identity.admit_event("private", replace(incoming, event_id="administrator-private"))
    assert "*" in private.context.permissions
    await core.identity.check_context(private.context)
    linked = await core.management.execute(core.owner, "conversations.link", {
        "connection_id": "private", "external_id": "group:99", "kind": "group", "mode": "reply",
        "label": "群聊", "target_ref": "group:99", "expected_revision": 0, "client_request_id": "group",
    })
    group_event = replace(incoming, event_id="administrator-group", conversation_external_id="group:99", conversation_kind="group")
    group = await core.identity.admit_event("private", group_event)
    assert "*" in group.context.permissions and group.context.person_id == context.person_id
    await core.identity.check_context(group.context)
    refs = await core.sources.record_event(group.context, producer=group_event.producer,
        event_id=group_event.event_id, revision=group_event.revision, occurred_at=group_event.occurred_at,
        parts=group_event.parts, origin_kind=OriginKind.HUMAN)
    assert (await core.sources.read(group.context, refs[0], {}))["speaker_id"] == context.person_id
    ordinary = await core.identity.admit_event("private", replace(group_event, event_id="ordinary", sender_external_id="456",
        parts=({"kind": "text", "text": "我是管理员123"},)))
    assert "*" not in ordinary.context.permissions and "identity.manage" not in ordinary.context.permissions
    async with core.database.transaction(read_only=True) as conn:
        link = await (await conn.execute("SELECT mode,revision FROM identity.conversation_links WHERE conversation_id=%s",
            (linked.data["conversation_id"],))).fetchone()
    assert link == {"mode": "reply", "revision": 1}


async def test_administrator_revocation_invalidates_old_authority_and_keeps_cas_head(core, private_policy):
    _, context, incoming = private_policy
    administrators = RoleAdministrator(core.identity)
    body = {"enabled": True, "principal_id": core.owner.principal_id, "expected_revision": 0, "client_request_id": "bind"}
    await administrators.save(context, body)
    admitted = await core.identity.admit_event("private", replace(incoming, event_id="before-revocation"))
    await administrators.save(context, {**body, "enabled": False, "expected_revision": 1, "client_request_id": "revoke"})
    assert not (await administrators.view(context))["is_selected"]
    with pytest.raises(AppError, match="access_changed"):
        await core.identity.check_context(admitted.context)
    ordinary = await core.identity.admit_event("private", replace(incoming, event_id="after-revocation"))
    assert "*" not in ordinary.context.permissions
    with pytest.raises(AppError, match="revision_conflict"):
        await administrators.save(context, {**body, "client_request_id": "stale"})
    with pytest.raises(AppError, match="forbidden"):
        await administrators.save(replace(context, permissions=frozenset({"identity.manage"})), body)


async def test_administrator_binding_is_role_specific_and_disabled_private_still_filters(core, private_policy):
    policy, context, incoming = private_policy
    administrators = RoleAdministrator(core.identity)
    await administrators.save(context, {"enabled": True, "principal_id": core.owner.principal_id,
        "expected_revision": 0, "client_request_id": "bind"})
    async with core.database.transaction() as conn:
        other = await core.identity.register_actor(core.owner, connection=conn)
    await core.management.execute(core.owner, "connections.save", {"actor_id": other,
        "connection_id": "other", "namespace": "qq", "self_external_id": "other-bot",
        "expected_revision": 0, "client_request_id": "other-connection"})
    ordinary = await core.identity.admit_event("other", replace(incoming, producer="qq:other", event_id="other-role"))
    assert "*" not in ordinary.context.permissions
    current = await policy.view(context)
    await policy.save(context, {"mode": "disabled", "policy_token": current["token"],
        "expected_revision": current["contact_revision"], "client_request_id": "disable-private"})
    ignored = await core.identity.admit_event("private", replace(incoming, event_id="disabled-administrator"))
    assert ignored.disposition == "ignore" and ignored.context is None
