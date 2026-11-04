"""Source provenance, immutable supplements, and independent invalidation."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from tianshu.conversation.types import InputPart
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.sources.types import EvidenceSetRef, EvidenceSpec
from tianshu.types import OriginKind


_spec = spec_from_file_location("rb_source_core_fixtures", Path(__file__).parents[1] / "core" / "conftest.py")
_fixtures = module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)
core = _fixtures.core
ordinary_users = _fixtures.ordinary_users


async def _record(core, context, event, *, connection=None):
    return (await core.sources.record_event(
        context, producer="web", event_id=event, revision=1,
        occurred_at=datetime.now(UTC), parts=(InputPart("text", text=event),),
        origin_kind=OriginKind.HUMAN, connection=connection,
    ))[0]


async def _retract(core, context, ref, request):
    await core.sources.invalidate(context, {
        "source_id": ref.source_id, "expected_revision": ref.revision,
        "client_request_id": request,
    })


async def test_same_transaction_evidence_proof_and_and_or_generation(core, ordinary_users):
    alice, _ = ordinary_users
    async with core.database.transaction() as conn:
        required = await _record(core, alice, "required", connection=conn)
        first = await _record(core, alice, "alternative-1", connection=conn)
        second = await _record(core, alice, "alternative-2", connection=conn)
        generated = await _record(core, alice, "generation", connection=conn)
        assert (await core.sources.check_many_on(conn, alice, ((required,),))) == (True,)
        # Another transaction cannot see the just-created receipt yet.
        assert (await core.sources.check_many(alice, ((required,),))) == (False,)
        assert await core.sources.register_evidence_on(conn, alice, EvidenceSpec(required=(required,))) == required
        spec = EvidenceSpec((required,), ((first,), (second,)), (generated,))
        evidence = await core.sources.register_evidence_on(conn, alice, spec)
        reordered = await core.sources.register_evidence_on(
            conn, alice, EvidenceSpec((required,), ((second,), (first,), (first,)), (generated,))
        )
        assert isinstance(evidence, EvidenceSetRef) and evidence == reordered
        assert (await core.sources.prove_many_on(conn, alice, (evidence,), lock=True)) == (True,)
    await _retract(core, alice, first, "withdraw-alternative")
    async with core.database.transaction(read_only=True) as conn:
        assert (await core.sources.prove_many_on(conn, alice, (evidence,))) == (True,)
        status = await core.sources.source_status_many_on(conn, alice, (first, second))
        assert [item.state for item in status] == ["retracted", "active"]
    await _retract(core, alice, generated, "withdraw-generation")
    assert (await core.sources.prove_many(alice, (evidence,))) == (False,)


async def test_supplement_preserves_event_identity_and_retracts_with_original(core, ordinary_users):
    alice, bob = ordinary_users
    original = await _record(core, alice, "original-event")
    media = core.files.put_bytes(b"the received attachment", media_type="image/png")
    arguments = dict(original_ref=original, producer="web", event_id="original-event",
                     event_revision=1, part_index=0, supplement_revision=1)
    supplement = await core.sources.register_media_supplement(alice, media, **arguments)
    assert supplement == await core.sources.register_media_supplement(alice, media, **arguments)
    async with core.database.transaction() as conn:
        row = await (await conn.execute(
            "SELECT scope_ids FROM sources.versions WHERE source_id=%s AND revision=%s",
            (original.source_id, original.revision),
        )).fetchone()
        await conn.execute(
            "INSERT INTO identity.scope_grants(scope_id,principal_id,permissions) "
            "VALUES(%s,%s,ARRAY['read']) ON CONFLICT DO NOTHING",
            (row["scope_ids"][0], bob.principal_id),
        )
        await conn.execute(
            "UPDATE identity.principals SET access_epoch=access_epoch+1 WHERE principal_id=%s",
            (bob.principal_id,),
        )
    bob = await core.identity.context(bob.principal_id, actor_id=bob.actor_id)
    assert (await core.sources.check_many(bob, ((original,),))) == (True,)
    with pytest.raises(AppError, match="media_supplement_owner_required"):
        await core.sources.register_media_supplement(bob, media, **arguments)
    replacement = core.files.put_bytes(b"different attachment", media_type="image/png")
    with pytest.raises(AppError, match="source_revision_conflict"):
        await core.sources.register_media_supplement(alice, replacement, **arguments)
    async with core.database.transaction() as conn:
        proof = {key: value for key, value in arguments.items() if key != "supplement_revision"}
        assert await core.sources.validate_media_supplement_on(conn, alice, supplement, **proof)
        assert not await core.sources.validate_media_supplement_on(
            conn, alice, supplement, **{**proof, "part_index": 1}
        )
    locator = (await core.sources.read(alice, supplement, {}))["locator"]
    assert locator["original_ref"] == wire(original) and locator["event_id"] == "original-event"
    await _retract(core, alice, original, "withdraw-original")
    assert (await core.sources.check_many(alice, ((supplement,),))) == (False,)
    with pytest.raises(AppError):
        await core.sources.register_media_supplement(alice, media, **arguments)


async def test_source_proof_holds_current_grants_until_write_commits(core, ordinary_users):
    alice, _ = ordinary_users
    source = await _record(core, alice, "locked-authority")
    started = asyncio.Event()

    async def revoke():
        async with core.database.transaction() as conn:
            row = await (await conn.execute(
                "SELECT scope_id FROM identity.conversations WHERE conversation_id=%s",
                (alice.conversation_id,),
            )).fetchone()
            started.set()
            await conn.execute(
                "UPDATE identity.scopes SET access_epoch=access_epoch+1 WHERE scope_id=%s",
                (row["scope_id"],),
            )
            await conn.execute(
                "DELETE FROM identity.scope_grants WHERE scope_id=%s AND principal_id=%s",
                (row["scope_id"], alice.principal_id),
            )
            await conn.execute(
                "UPDATE identity.principals SET access_epoch=access_epoch+1 WHERE principal_id=%s",
                (alice.principal_id,),
            )

    async with core.database.transaction() as conn:
        assert await core.sources.validate_persistence(conn, alice, (source,))
        pending = asyncio.create_task(revoke())
        await started.wait()
        assert not (await asyncio.wait({pending}, timeout=0.05))[0]
    await pending
    current = await core.identity.context(alice.principal_id, actor_id=alice.actor_id)
    assert (await core.sources.check_many(current, ((source,),))) == (False,)


async def test_independent_consumers_do_not_rollback_source(core, ordinary_users):
    alice, _ = ordinary_users
    observed = []

    async def failed(_conn, _refs, _kind):
        raise RuntimeError("downstream failed")

    async def working(_conn, refs, kind):
        observed.append((refs, kind))

    async def partitions(_conn, _refs, _kind):
        return ("actor-a", "actor-b")

    async def per_actor(_conn, refs, kind, *, actor_id):
        if actor_id == "actor-a":
            raise RuntimeError("actor-a failed")
        observed.append((refs, kind, actor_id))

    core.sources.register_dependant(failed, name="rb.failed")
    core.sources.register_dependant(working, name="rb.working")
    core.sources.register_dependant(per_actor, name="rb.actors", partitions=partitions)
    original = await _record(core, alice, "independent-withdrawal")
    await _retract(core, alice, original, "independent-withdrawal")
    assert not observed
    assert (await core.sources.check_many(alice, ((original,),))) == (False,)
    async with core.database.transaction(read_only=True) as conn:
        change = await (await conn.execute(
            "SELECT sequence FROM sources.changes WHERE source_id=%s ORDER BY sequence DESC LIMIT 1",
            (original.source_id,),
        )).fetchone()
    await core.sources.propagate(wire(original), "retracted", change_id=change["sequence"])
    event_key = f"change:{change['sequence']}"
    async with core.database.transaction(read_only=True) as conn:
        rows = await (await conn.execute(
            "SELECT key FROM runtime.job_keys WHERE task='sources.dispatch' AND key LIKE %s",
            (f"source-dispatch:{event_key}:%",),
        )).fetchall()
    assert len(rows) == len(core.sources.dependants)
    for name in core.sources.dependants:
        await core.sources.dispatch_dependant(name, (wire(original),), "retracted", event_key)
    async with core.database.transaction(read_only=True) as conn:
        rows = await (await conn.execute(
            "SELECT j.args FROM runtime.job_keys k JOIN procrastinate_jobs j ON j.id=k.job_id "
            "WHERE k.task='sources.notify' AND k.key LIKE %s",
            (f"source-notify:{event_key}:%",),
        )).fetchall()
    # The source has no Memory dependants; only the two global consumers and
    # the two explicit actor shards need notifications.
    assert {(row['args']['consumer'],row['args'].get('actor_id')) for row in rows} == {
        ('rb.failed',None),('rb.working',None),('rb.actors','actor-a'),('rb.actors','actor-b')}
    with pytest.raises(RuntimeError, match="downstream failed"):
        await core.sources.notify_dependant("rb.failed", (wire(original),), "retracted")
    await core.sources.notify_dependant("rb.working", (wire(original),), "retracted")
    with pytest.raises(RuntimeError, match="actor-a failed"):
        await core.sources.notify_dependant(
            "rb.actors", (wire(original),), "retracted", actor_id="actor-a", partitioned=True
        )
    await core.sources.notify_dependant(
        "rb.actors", (wire(original),), "retracted", actor_id="actor-b", partitioned=True
    )
    assert observed == [((original,), "retracted"), ((original,), "retracted", "actor-b")]
    assert (await core.sources.check_many(alice, ((original,),))) == (False,)


async def test_actorless_disclosure_uses_existing_system_scope(core):
    context = core.owner
    original = (await core.sources.record_event(
        context, producer="project", event_id="lesson", revision=1,
        occurred_at=datetime.now(UTC), parts=(InputPart("text", text="evidence"),),
        origin_kind=OriginKind.TOOL_RESULT,
    ))[0]
    async with core.database.transaction(read_only=True) as conn:
        before = await (await conn.execute("SELECT count(*) AS n FROM identity.scope_grants")).fetchone()
    ref = await core.sources.disclose(
        context, producer="projects.experience", event_id="example", revision=1,
        parts=(InputPart("text", text="shared summary"),), source_refs=(original,),
    )
    assert (await core.sources.read(context, ref, {}))["content"] == "shared summary"
    async with core.database.transaction(read_only=True) as conn:
        after = await (await conn.execute("SELECT count(*) AS n FROM identity.scope_grants")).fetchone()
    assert before == after
    with pytest.raises(AppError):
        await core.sources.disclose(
            replace(context, permissions=frozenset({"projects.write"})),
            producer="projects.experience", event_id="unauthorized", revision=1,
            parts=(InputPart("text", text="denied"),), source_refs=(original,),
        )


async def test_automatic_archives_respect_installation_and_explicit_old_ref(core, ordinary_users):
    alice, _ = ordinary_users
    async with core.database.transaction() as conn:
        await conn.execute(
            "INSERT INTO identity.connections(connection_id,principal_id,actor_id,namespace,self_external_id) "
            "VALUES('rb-qq',%s,%s,'qq','self')", (alice.principal_id, alice.actor_id)
        )
        await conn.execute(
            "INSERT INTO identity.conversation_links(connection_id,external_id,conversation_id,mode) "
            "VALUES('rb-qq','room',%s,'reply')", (alice.conversation_id,)
        )
        generation = await (await conn.execute(
            "SELECT id,created_at FROM runtime.installation_generation WHERE singleton"
        )).fetchone()
    def record(message, timestamp):
        return {
            "locator": message, "stored_content_sha256": "a" * 64, "text": message,
            "text_representation": "stored_text", "sender_id": "sender", "timestamp": timestamp,
            "native_aliases": [{"platform": "qq", "account_id": "self", "room_id": "room",
                                "message_type": "private", "id_kind": "external", "message_id": message}],
        }
    old = record("old", (generation["created_at"] - timedelta(days=1)).timestamp())
    fresh = record("new", (generation["created_at"] + timedelta(seconds=1)).timestamp())
    async def register(item, explicit):
        return (await core.sources.register_archive(
            alice, mapping_id="archive", channel={"network": "qq"},
            records=(item,), explicit_request=explicit,
        ))[0]
    assert (await register(old, False))["reason"] == "archive_before_installation"
    explicit = await register(old, True)
    assert explicit["allowed"] and (await register(old, False))["source_ref"] == explicit["source_ref"]
    changed = {**old, "stored_content_sha256": "b" * 64, "text": "changed"}
    assert (await register(changed, False))["reason"] == "archive_before_installation"
    assert (await register(fresh, False))["allowed"]
