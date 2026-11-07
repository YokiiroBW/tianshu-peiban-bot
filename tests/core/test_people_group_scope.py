"""Role-separated profiles over group-only acquaintances and private corrections."""

from dataclasses import replace

import pytest

from tianshu.runtime.errors import AppError


async def group_for(core, actor, tag, people):
    await core.management.execute(core.owner, "connections.save", {
        "actor_id": actor, "connection_id": tag, "namespace": "qq",
        "self_external_id": tag, "expected_revision": 0, "client_request_id": tag,
    })
    result = await core.management.execute(core.owner, "conversations.link", {
        "connection_id": tag, "external_id": "group:" + tag, "kind": "group",
        "label": tag, "target_ref": "group:" + tag, "mode": "observe",
        "expected_revision": 0, "client_request_id": "group-" + tag,
    })
    conversation = result.data["conversation_id"]
    async with core.database.transaction() as conn:
        for person in people:
            await conn.execute("INSERT INTO identity.people(person_id,display_name) VALUES(%s,%s) ON CONFLICT DO NOTHING", (person, person))
            await conn.execute("INSERT INTO identity.conversation_members(conversation_id,connection_id,external_id,person_id) VALUES(%s,%s,%s,%s)", (conversation, tag, person, person))
    context = await core.identity.context(core.owner.principal_id, actor_id=actor, conversation_id=conversation)
    return context


async def test_people_and_group_pages_follow_actor_before_pagination(core):
    async with core.database.transaction() as conn:
        other = await core.identity.register_actor(core.owner, connection=conn)
    first = await group_for(core, core.actor, "first", ["alice"])
    second = await group_for(core, other, "second", ["bob"])
    for actor, expected, absent in ((core.actor, "alice", "bob"), (other, "bob", "alice")):
        selected = await core.identity.context(core.owner.principal_id, actor_id=actor)
        people = await core.management.people(selected)
        assert expected in {person["person_id"] for person in people.items}
        assert absent not in {person["person_id"] for person in people.items}
        groups = await core.identity.conversations(selected, actor, kind="group", limit=1)
        assert len(groups.items) == 1 and groups.items[0]["actor_id"] == actor
        assert groups.items[0]["conversation_id"] == (first if actor == core.actor else second).conversation_id
        assert groups.next_cursor is None


async def test_group_only_person_reads_are_scoped_and_revoked_membership_stays_forbidden(core):
    group = await group_for(core, core.actor, "one", ["alice", "bob"])
    selected = await core.identity.context(core.owner.principal_id, actor_id=core.actor, person_id="alice")
    assert set(group.scope_ids) & set(selected.scope_ids)
    async with core.database.transaction(read_only=True) as conn:
        resolved = await core.identity.resolve_memory_subjects_on(conn, selected, ["alice", "bob"])
    assert {person["person_id"] for person in resolved} == {"alice", "bob"}
    async with core.database.transaction() as conn:
        await conn.execute("DELETE FROM identity.conversation_members WHERE person_id='alice'")
    with pytest.raises(AppError, match="person_forbidden"):
        await core.identity.context(core.owner.principal_id, actor_id=core.actor, person_id="alice")
    other = await core.identity.context(core.owner.principal_id, actor_id=core.actor, person_id="bob")
    assert other.person_id == "bob"


async def test_group_only_relationship_edit_creates_private_evidence_without_group_disclosure(core):
    group = await group_for(core, core.actor, "one", ["alice"])
    selected = await core.identity.context(core.owner.principal_id, actor_id=core.actor, person_id="alice")
    await core.memory.save_relationship(selected, {
        "person_id": "alice", "relationship_type": "friend", "display_label": "老朋友",
        "summary": "私下设置的相处方式", "expected_revision": 0, "client_request_id": "relationship",
    })
    refreshed = await core.identity.context(core.owner.principal_id, actor_id=core.actor, person_id="alice")
    assert (await core.memory.relationship(refreshed, "alice"))["summary"] == "私下设置的相处方式"
    assert (await core.memory.relationship(group, "alice"))["summary"] == ""
    prepared = await core.identity.person_write_context(refreshed, permission="memory.manage")
    assert set(prepared.scope_ids) == set(refreshed.scope_ids)
    with pytest.raises(AppError, match="forbidden"):
        await core.identity.person_write_context(replace(refreshed, permissions=frozenset({"memory.read"})), permission="memory.manage")


async def test_group_claim_category_filters_do_not_mix_people_or_other_groups(core):
    from datetime import UTC, datetime

    from tianshu.types import OriginKind

    group = await group_for(core, core.actor, "one", ["alice"])
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE identity.principals SET person_id='alice' WHERE principal_id=%s", (core.owner.principal_id,))
    refs = await core.sources.record_event(group, producer="web", event_id="group-notes", revision=1,
        occurred_at=datetime.now(UTC), parts=({"text": "猫粮指群里分享的猫照片。群里常聊养猫。"},), origin_kind=OriginKind.HUMAN)
    async with core.database.transaction() as conn:
        for predicate, value, memory_kind in (
            ("language.term_meaning", {"term": "猫粮", "meaning": "群里分享的猫照片"}, "hypothesis"),
            ("group.impression", "常聊养猫", "hypothesis"),
        ):
            await core.claims.put_on(conn, group, {"subject_id": "group:" + group.conversation_id,
                "predicate": predicate, "value": value, "memory_kind": memory_kind,
                "assertion": "inferred", "speaker_id": "alice", "source_refs": refs}, automatic=True)
    terms = await core.claims.list(group, subject_id="group:" + group.conversation_id, predicate="language.term_meaning", limit=1)
    impressions = await core.claims.list(group, subject_id="group:" + group.conversation_id, exclude_predicate="language.term_meaning", limit=1)
    assert [item.predicate for item in terms.items] == ["language.term_meaning"]
    assert [item.predicate for item in impressions.items] == ["group.impression"]
    assert terms.next_cursor is None and impressions.next_cursor is None


async def test_group_insights_reads_authorized_sql_records_and_hides_withdrawn_sources(core):
    from datetime import UTC, datetime
    from importlib.resources import files
    from types import SimpleNamespace

    from tianshu.conversation.codec import document
    from tianshu.conversation.group_insights import group_insights
    from tianshu.runtime.migrations import Migration, migrate
    from tianshu.types import OriginKind

    await migrate(core.database, (Migration("0032_conversation", files("tianshu.conversation").joinpath("schema.sql").read_text()),))
    group = await group_for(core, core.actor, "one", ["alice"])
    refs = await core.sources.record_event(group, producer="web", event_id="heat", revision=1,
        occurred_at=datetime.now(UTC), parts=({"kind": "text", "text": "猫粮"},), origin_kind=OriginKind.HUMAN)
    async with core.database.transaction() as conn:
        await conn.execute("INSERT INTO conversation.messages(actor_id,conversation_id,speaker,parts,source_refs,scope_ids) "
            "VALUES(%s,%s,'user',%s,%s,%s)", (core.actor, group.conversation_id,
            document([{"kind": "text", "text": "猫粮"}]), document(refs), list(group.scope_ids)))
    service = SimpleNamespace(database=core.database, identity=core.identity, sources=core.sources)
    result = await group_insights(service, group, {"days": 7})
    assert result["message_count"] == 1 and result["topics"][0]["label"] == "猫粮"
    assert result["coverage"].complete
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE sources.originals SET state='retracted' WHERE source_id=%s", (refs[0].source_id,))
    hidden = await group_insights(service, group, {"days": 7})
    assert hidden["message_count"] == 0 and not hidden["coverage"].complete
