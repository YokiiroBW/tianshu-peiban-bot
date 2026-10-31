"""Archive source intake uses real ordinary-user targets and persists native-message forgetting."""

from datetime import UTC, datetime

import pytest

from tianshu.conversation.types import InputPart
from tianshu.identity.types import IncomingEvent
from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind, SourceRef


async def attach_qq(core, context):
    async with core.database.transaction() as conn:
        await conn.execute(
            "INSERT INTO identity.connections(connection_id,principal_id,actor_id,namespace,self_external_id) "
            "VALUES('qq-connection',%s,%s,'qq','bot-self')",
            (context.principal_id, context.actor_id),
        )
        await conn.execute(
            "INSERT INTO identity.accounts(connection_id,external_id,person_id,principal_id) "
            "VALUES('qq-connection','alice-native',%s,%s)",
            (context.person_id, context.principal_id),
        )
        await conn.execute(
            "INSERT INTO identity.conversation_links(connection_id,external_id,conversation_id,mode) "
            "VALUES('qq-connection','private:alice-native',%s,'reply')",
            (context.conversation_id,),
        )


async def native_event(core, context, message_id, text):
    event = IncomingEvent(
        "qq:qq-connection",
        "message:private:" + message_id,
        1,
        datetime(2026, 10, 31, tzinfo=UTC),
        "private:alice-native",
        "private",
        "alice-native",
        parts=({"kind": "text", "text": text},),
    )
    admitted = await core.identity.admit_event("qq-connection", event)
    assert admitted.disposition == "reply" and admitted.context is not None
    assert admitted.context.person_id == context.person_id
    return await core.sources.record_event(
        admitted.context,
        producer=event.producer,
        event_id=event.event_id,
        revision=event.revision,
        occurred_at=event.occurred_at,
        parts=(InputPart("text", text=text),),
        origin_kind=OriginKind.HUMAN,
    )


def archived_record():
    return {
        "locator": "archive:message-one",
        "archive_observation_id": "observed-now",
        "stored_content_sha256": "a" * 64,
        "text": "a private historical message",
        "text_representation": "stored_text",
        "sender_id": "alice-native",
        "timestamp": 1793404800,
        "native_aliases": [
            {
                "platform": "qq",
                "account_id": "bot-self",
                "room_id": "private:alice-native",
                "message_type": "private",
                "id_kind": "external",
                "message_id": "message-one",
                "import_source_id": None,
            }
        ],
    }


@pytest.mark.parametrize("native_arrives_first", [True, False])
async def test_native_message_forget_blocks_archive_across_mappings(
    core, ordinary_users, native_arrives_first
):
    alice, _bob = ordinary_users
    await attach_qq(core, alice)

    async def native():
        return await native_event(core, alice, "message-one", "a private historical message")

    source = await native() if native_arrives_first else None
    first = await core.sources.register_archive(
        alice,
        mapping_id="first-mapping",
        channel={"network": "qq"},
        records=(archived_record(),),
        explicit_request=False,
    )
    assert (
        first[0]["allowed"] is False and first[0]["reason"] == "explicit_history_request_required"
    )
    imported = await core.sources.register_archive(
        alice,
        mapping_id="first-mapping",
        channel={"network": "qq"},
        records=(archived_record(),),
        explicit_request=True,
    )
    ref = imported[0]["source_ref"]
    assert imported[0]["allowed"]
    assert (await core.sources.read(alice, ref, {}))["origin_kind"] == "tool_result"
    source = source or await native()
    await core.sources.invalidate(
        alice,
        {
            "source_id": source[0].source_id,
            "expected_revision": 1,
            "client_request_id": "forget-original",
        },
        forgotten=True,
    )
    assert (await core.sources.check_many(alice, ((ref,),))) == (False,)
    async with core.database.transaction(read_only=True) as conn:
        row = await (
            await conn.execute(
                "SELECT content FROM sources.versions WHERE source_id=%s AND revision=1",
                (ref.source_id,),
            )
        ).fetchone()
        assert row["content"] is None
    other_mapping = await core.sources.register_archive(
        alice,
        mapping_id="new-mapping",
        channel={"network": "qq"},
        records=(archived_record(),),
        explicit_request=True,
    )
    assert other_mapping[0]["allowed"] is False and other_mapping[0]["reason"] == "source_withdrawn"


async def test_archive_alias_cannot_choose_another_room(core, ordinary_users):
    alice, _bob = ordinary_users
    await attach_qq(core, alice)
    record = archived_record()
    record["native_aliases"][0]["room_id"] = "private:bob-native"
    with pytest.raises(AppError, match="archive_alias_scene_mismatch"):
        await core.sources.register_archive(
            alice,
            mapping_id="first",
            channel={"network": "qq"},
            records=(record,),
            explicit_request=True,
        )


@pytest.mark.parametrize("native_already_present", [True, False])
async def test_forgotten_archive_blocks_late_native_but_accepts_a_new_message(
    core, ordinary_users, native_already_present
):
    alice, _bob = ordinary_users
    await attach_qq(core, alice)

    async def native(event_id):
        return await native_event(core, alice, event_id, "original native content")

    original = await native("message-one") if native_already_present else None
    imported = await core.sources.register_archive(
        alice,
        mapping_id="first-mapping",
        channel={"network": "qq"},
        records=(archived_record(),),
        explicit_request=True,
    )
    ref = imported[0]["source_ref"]
    await core.sources.invalidate(
        alice,
        {"source_id": ref.source_id, "expected_revision": 1, "client_request_id": "forget-archive"},
        forgotten=True,
    )
    if original:
        assert await core.sources.check_many(alice, (original,)) == (False,)
        async with core.database.transaction(read_only=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT content FROM sources.versions WHERE source_id=%s AND revision=1",
                    (original[0].source_id,),
                )
            ).fetchone()
            assert row["content"] is None
    with pytest.raises(AppError, match="source_suppressed"):
        await native("message-one")
    fresh = await native("a-genuinely-new-message")
    assert await core.sources.check_many(alice, (fresh,)) == (True,)


async def test_channel_authorship_still_requires_a_real_admitted_event(core, ordinary_users):
    alice, _bob = ordinary_users
    await attach_qq(core, alice)
    with pytest.raises(AppError, match="event_author_unverified"):
        await core.sources.record_event(
            alice,
            producer="qq:qq-connection",
            event_id="message:private:unadmitted",
            revision=1,
            occurred_at=datetime.now(UTC),
            parts=(InputPart("text", text="not actually admitted"),),
            origin_kind=OriginKind.HUMAN,
        )


async def test_one_support_group_preserves_distinct_fragments_of_one_version(core, ordinary_users):
    alice, _bob = ordinary_users
    refs = await core.sources.record_event(
        alice,
        producer="web",
        event_id="one-original",
        revision=1,
        occurred_at=datetime.now(UTC),
        parts=(InputPart("text", text="0123456789"),),
        origin_kind=OriginKind.HUMAN,
    )
    fragments = tuple(
        SourceRef(refs[0].source_id, 1, {"start": start, "end": end})
        for start, end in ((0, 2), (8, 10))
    )
    publication = await core.sources.disclose(
        alice,
        producer="author",
        event_id="fragment-work",
        revision=1,
        parts=(InputPart("text", text="author work with two selected pieces"),),
        source_refs=fragments,
    )
    async with core.database.transaction(read_only=True) as conn:
        row = await (
            await conn.execute(
                "SELECT count(*) AS count FROM sources.supports WHERE source_id=%s",
                (publication.source_id,),
            )
        ).fetchone()
        assert row["count"] == 2
