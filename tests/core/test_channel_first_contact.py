"""Real first contacts retain the transport policy without creating application users."""

from datetime import UTC, datetime

import pytest

from tianshu.identity.types import IncomingEvent
from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind


async def connection(core, *, enabled=True, default_mode="reply"):
    return await core.management.execute(
        core.owner,
        "connections.save",
        {
            "connection_id": "first",
            "actor_id": core.actor,
            "namespace": "qq",
            "self_external_id": "999",
            "enabled": enabled,
            "default_mode": default_mode,
            "expected_revision": 0,
            "client_request_id": "connect",
        },
    )


def event(kind, target, sender, message, *, text="你好", part_kind="text"):
    return IncomingEvent(
        "qq:first",
        "message:" + kind + ":" + message,
        1,
        datetime.now(UTC),
        target,
        kind,
        sender,
        parts=({"kind": part_kind, "text": text},),
        sender_display_name="真实昵称",
    )


async def test_first_private_uses_real_policy_and_one_native_account(core):
    await connection(core)
    incoming = event("private", "private:1001", "1001", "p1")
    admitted = await core.identity.admit_event("first", incoming)
    assert admitted.disposition == "reply" and admitted.context.delivery_allowed
    assert (
        "*" not in admitted.context.permissions
        and "identity.manage" not in admitted.context.permissions
    )
    async with core.database.transaction(read_only=True) as conn:
        accounts = await (
            await conn.execute(
                "SELECT external_id,principal_id FROM identity.accounts WHERE connection_id='first'"
            )
        ).fetchall()
        members = await (
            await conn.execute(
                "SELECT person_id FROM identity.conversation_members WHERE conversation_id=%s",
                (admitted.context.conversation_id,),
            )
        ).fetchall()
    assert accounts == [{"external_id": "1001", "principal_id": None}]
    assert members == [{"person_id": admitted.context.person_id}]
    duplicate = await core.identity.admit_event("first", incoming)
    assert (
        duplicate.duplicate
        and duplicate.context.conversation_id == admitted.context.conversation_id
    )
    refs = await core.sources.record_event(
        admitted.context,
        producer=incoming.producer,
        event_id=incoming.event_id,
        revision=1,
        occurred_at=incoming.occurred_at,
        parts=incoming.parts,
        origin_kind=OriginKind.HUMAN,
    )
    assert (await core.sources.read(admitted.context, refs[0], {}))[
        "speaker_id"
    ] == admitted.context.person_id


@pytest.mark.parametrize("enabled,mode", [(True, "ignore"), (False, "reply")])
async def test_disabled_or_ignored_private_does_not_create_person_or_reply_right(
    core, enabled, mode
):
    await connection(core, enabled=enabled, default_mode=mode)
    if enabled:
        assert (
            await core.identity.admit_event(
                "first", event("private", "private:1001", "1001", "ignored")
            )
        ).disposition == "ignore"
    else:
        with pytest.raises(AppError, match="connection_not_authorized"):
            await core.identity.admit_event(
                "first", event("private", "private:1001", "1001", "disabled")
            )
    async with core.database.transaction(read_only=True) as conn:
        assert (
            await (
                await conn.execute("SELECT 1 FROM identity.accounts WHERE connection_id='first'")
            ).fetchone()
            is None
        )


async def test_authorized_group_stranger_keywords_and_real_group_memory(core):
    await connection(core)
    assert (
        await core.identity.admit_event(
            "first", event("group", "group:99", "1001", "not-configured")
        )
    ).disposition == "ignore"
    linked = await core.management.execute(
        core.owner,
        "conversations.link",
        {
            "connection_id": "first",
            "external_id": "group:99",
            "kind": "group",
            "mode": "reply",
            "label": "当前群",
            "target_ref": "group:99",
            "mention_required": True,
            "reply_keywords": ["天枢"],
            "expected_revision": 0,
            "client_request_id": "group",
        },
    )
    observed = await core.identity.admit_event(
        "first", event("group", "group:99", "1001", "ordinary")
    )
    assert observed.disposition == "observe" and not observed.context.delivery_allowed
    assert (await core.identity.for_participation(observed.context)).delivery_allowed
    incoming = event("group", "group:99", "1002", "trigger", text="天枢，周六群读书会")
    admitted = await core.identity.admit_event("first", incoming)
    assert (
        admitted.disposition == "reply" and admitted.context.person_id != observed.context.person_id
    )
    quote = await core.identity.admit_event(
        "first", event("group", "group:99", "1002", "quoted", text="天枢", part_kind="quote")
    )
    assert quote.disposition == "observe"
    refs = await core.sources.record_event(
        admitted.context,
        producer=incoming.producer,
        event_id=incoming.event_id,
        revision=1,
        occurred_at=incoming.occurred_at,
        parts=incoming.parts,
        origin_kind=OriginKind.HUMAN,
    )
    async with core.database.transaction() as conn:
        claim = await core.claims.put_on(
            conn,
            admitted.context,
            {
                "subject_id": "group:" + linked.data["conversation_id"],
                "predicate": "meeting.reading",
                "value": "周六读书会",
                "assertion": "reported",
                "source_refs": refs,
            },
        )
    projection = await core.memory.projection(
        admitted.context, group_id=linked.data["conversation_id"]
    )
    assert projection["group"]["subject_id"] == claim.subject_id
    assert [item.claim_id for item in projection["claims"]] == [claim.claim_id]
    private = await core.identity.admit_event(
        "first", event("private", "private:1002", "1002", "private")
    )
    assert await core.sources.check_many(private.context, (refs,)) == (False,)
    # Explicit observe policy cannot be promoted by the participation use case.
    await core.management.execute(
        core.owner,
        "conversations.link",
        {
            "connection_id": "first",
            "external_id": "group:99",
            "kind": "group",
            "mode": "observe",
            "label": "当前群",
            "target_ref": "group:99",
            "mention_required": True,
            "reply_keywords": ["天枢"],
            "expected_revision": 1,
            "client_request_id": "observe",
        },
    )
    current = await core.identity.admit_event("first", event("group", "group:99", "1002", "later"))
    assert await core.identity.for_participation(current.context) is None


async def test_admin_selected_person_is_not_the_human_author(core):
    actor = await core.identity.context(core.owner.principal_id, actor_id=core.actor)
    person = await core.management.execute(
        actor,
        "people.save",
        {"display_name": "别人", "expected_revision": 0, "client_request_id": "person"},
    )
    selected = await core.identity.context(
        core.owner.principal_id, actor_id=core.actor, person_id=person.data["person_id"]
    )
    refs = await core.sources.record_event(
        selected,
        producer="web",
        event_id="reported",
        revision=1,
        occurred_at=datetime.now(UTC),
        parts=({"text": "别人喜欢茶"},),
        origin_kind=OriginKind.HUMAN,
    )
    author = (await core.sources.read(selected, refs[0], {}))["speaker_id"]
    assert author != selected.person_id
    async with core.database.transaction() as conn:
        claim = await core.claims.put_on(
            conn,
            selected,
            {
                "subject_id": selected.person_id,
                "predicate": "interest",
                "value": "茶",
                "speaker_id": author,
                "assertion": "reported",
                "source_refs": refs,
            },
        )
    corrected = await core.claims.correct(
        selected,
        {
            "claim_id": claim.claim_id,
            "value": "咖啡",
            "expected_revision": claim.revision,
            "client_request_id": "correct",
        },
    )
    assert corrected.data.assertion == "reported" and corrected.data.speaker_id == author
