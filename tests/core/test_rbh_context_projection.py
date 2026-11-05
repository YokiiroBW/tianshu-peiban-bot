"""The assembler consumes real Memory projections and current behavior rules."""

from datetime import UTC, datetime, timedelta
from importlib.resources import files
import json
from types import SimpleNamespace

from psycopg.types.json import Jsonb

from tianshu.companion.behavior_resolver import BehaviorResolver
from tianshu.companion.habits import HabitLedger
from tianshu.conversation.context.assembly import ContextAssembler
from tianshu.identity.types import IncomingEvent
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind, RequestBudget


class ProjectionOnlyRetrieval:
    """Isolate structured projection while retaining real final evidence revalidation."""

    def __init__(self, retrieval):
        self.retrieval = retrieval

    def foreground_budget(self, budget, *, seconds=None):
        return budget

    async def search(self, context, query, budget, *, source_refs=()):
        return (), ()

    async def revalidate(self, context, records, budget):
        return await self.retrieval.revalidate(context, records, budget)


def assembler(core):
    return ContextAssembler(core.identity, core.memory, ProjectionOnlyRetrieval(core.retrieval),
                            settings={"structured_seconds": 10})


async def assemble(selected, context, *, contributions=(), behavior_resolver=None):
    return await selected.assemble(
        context, "chat", "现在聊一聊", RequestBudget(datetime.now(UTC) + timedelta(seconds=30)),
        max_input_tokens=30000, reserved_output_tokens=512,
        contributions=contributions, behavior_resolver=behavior_resolver,
    )


def evidence(pack):
    return {item["id"]: item for item in
            (json.loads(value.text) for value in pack.materials if value.kind == "memory_evidence")}


async def source(core, context, event_id, text):
    return await core.sources.record_event(
        context, producer="web", event_id=event_id, revision=1,
        occurred_at=datetime.now(UTC), parts=({"kind": "text", "text": text},),
        origin_kind=OriginKind.HUMAN,
    )


async def claim(core, context, refs, *, subject_id, value, predicate, **extra):
    async with core.database.transaction() as connection:
        return await core.claims.put_on(connection, context, {
            "subject_id": subject_id, "speaker_id": context.person_id,
            "predicate": predicate, "value": value, "source_refs": refs,
            "assertion": "self_confirmed" if subject_id == context.person_id else "reported",
            **extra,
        }, automatic=True)


async def test_real_person_projection_keeps_current_evidence_and_withdrawal_coverage(core, ordinary_users):
    alice, _ = ordinary_users
    live_refs = await source(core, alice, "rbh-person-current", "我现在喜欢茉莉茶")
    old_refs = await source(core, alice, "rbh-person-expired", "以前只在春天喝乌龙茶")
    withdrawn_refs = await source(core, alice, "rbh-person-withdrawn", "我喜欢桂花茶")
    live = await claim(core, alice, live_refs, subject_id=alice.person_id,
                       predicate="preference.current", value="茉莉茶")
    old = await claim(core, alice, old_refs, subject_id=alice.person_id,
                      predicate="preference.expired", value="乌龙茶",
                      valid_until=datetime.now(UTC) - timedelta(days=1))
    withdrawn = await claim(core, alice, withdrawn_refs, subject_id=alice.person_id,
                            predicate="preference.withdrawn", value="桂花茶")

    selected = assembler(core)
    before = await assemble(selected, alice)
    current = evidence(before)
    assert {live.claim_id, withdrawn.claim_id} <= set(current)
    assert old.claim_id not in current
    assert current[live.claim_id]["subject_id"] == alice.person_id
    assert any(item.kind == "relationship_background" for item in before.materials)
    assert "structured_memory_unavailable" not in before.missing
    assert "structured_memory_partial" not in before.missing

    await core.sources.invalidate(alice, {
        "source_id": withdrawn_refs[0].source_id, "expected_revision": 1,
        "client_request_id": "rbh-person-forget",
    }, forgotten=True)
    projection = await core.memory.projection(alice, alice.person_id, claim_limit=6, matter_limit=8)
    assert not projection["coverage"].complete
    assert "unavailable_sources" in projection["coverage"].missing
    after = await assemble(selected, alice)
    assert live.claim_id in evidence(after) and withdrawn.claim_id not in evidence(after)
    assert "structured_memory_partial" in after.missing
    assert "structured_memory_unavailable" not in after.missing


async def test_real_group_and_no_person_projection_are_both_consumed(core):
    await core.management.execute(core.owner, "connections.save", {
        "connection_id": "rbh-group", "actor_id": core.actor, "namespace": "qq",
        "self_external_id": "999", "expected_revision": 0, "client_request_id": "rbh-connect",
    })
    linked = await core.management.execute(core.owner, "conversations.link", {
        "connection_id": "rbh-group", "external_id": "group:99", "kind": "group",
        "mode": "reply", "label": "当前群", "target_ref": "group:99",
        "expected_revision": 0, "client_request_id": "rbh-group-link",
    })
    incoming = IncomingEvent(
        "qq:rbh-group", "message:group:rbh", 1, datetime.now(UTC),
        "group:99", "group", "1001",
        parts=({"kind": "text", "text": "我喜欢茶"},
               {"kind": "text", "text": "群里周六读书"}),
    )
    admitted = await core.identity.admit_event("rbh-group", incoming)
    context = admitted.context
    assert admitted.disposition == "reply" and context.person_id
    refs = await core.sources.record_event(
        context, producer=incoming.producer, event_id=incoming.event_id, revision=1,
        occurred_at=incoming.occurred_at, parts=incoming.parts, origin_kind=OriginKind.HUMAN,
    )
    person = await claim(core, context, refs[:1], subject_id=context.person_id,
                         predicate="interest.drink", value="茶")
    group = await claim(core, context, refs[1:], subject_id="group:" + linked.data["conversation_id"],
                        predicate="meeting.reading", value="周六读书")
    selected = assembler(core)
    pack = await assemble(selected, context)
    units = evidence(pack)
    assert {person.claim_id, group.claim_id} <= set(units)
    assert units[person.claim_id]["subject_id"] == context.person_id
    assert units[group.claim_id]["subject_id"] == "group:" + context.conversation_id
    assert any(item.kind == "relationship_background" for item in pack.materials)
    assert "structured_memory_unavailable" not in pack.missing
    assert "group_memory_partial" not in pack.missing

    no_person = await core.identity.context(
        context.principal_id, actor_id=context.actor_id,
        conversation_id=context.conversation_id, origin_kind=context.origin_kind,
    )
    assert no_person.person_id is None and no_person.conversation_kind == "group"
    pack = await assemble(selected, no_person)
    assert group.claim_id in evidence(pack)
    assert sum(value.content_revision == f"claim:{group.claim_id}:{group.revision}"
               for value in pack.materials) == 1
    assert not any(item.kind == "relationship_background" for item in pack.materials)
    assert "structured_memory_unavailable" not in pack.missing

    await core.sources.invalidate(context, {
        "source_id": refs[1].source_id, "expected_revision": 1,
        "client_request_id": "rbh-group-forget",
    }, forgotten=True)
    pack = await assemble(selected, context)
    assert person.claim_id in evidence(pack) and group.claim_id not in evidence(pack)
    assert "group_memory_partial" in pack.missing
    assert "structured_memory_partial" not in pack.missing


async def test_real_projection_preserves_current_learning_and_rechecks_revoked_rule(core, ordinary_users):
    alice, _ = ordinary_users
    refs = await source(core, alice, "rbh-learning", "请使用简短称呼")
    current = await claim(core, alice, refs, subject_id=alice.person_id,
                          predicate="preference.address", value="简短称呼")
    await migrate(core.database, tuple(
        Migration(name, files("tianshu.companion").joinpath(path).read_text())
        for name, path in (("0030_companion", "schema.sql"),
                           ("0034_companion_growth_batches", "growth_batches.sql"),
                           ("0037_companion_growth_retry", "growth_retry.sql"),
                           ("0039_companion_learning", "habit_revisions.sql"))
    ))
    async with core.database.transaction() as connection:
        await connection.execute(
            "INSERT INTO companion.actors(actor_id,name,enabled) VALUES(%s,%s,true)",
            (core.actor, "测试角色"),
        )
        await connection.execute(
            "INSERT INTO companion.style_rules(id,actor_id,person_id,kind,title,content,conditions,"
            "counterexamples,episode_ids,source_refs,scope_ids,state,conversation_id,preference_kind) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            ("rbh-rule", core.actor, alice.person_id, "style", "称呼", "请使用简短称呼",
             Jsonb({"when": [], "unless": []}), Jsonb([]), ["fixture-episode"], Jsonb(wire(refs)),
             list(alice.scope_ids), "active", alice.conversation_id, "explicit"),
        )
        await connection.execute(
            "INSERT INTO companion.learning_reflections(proposal_id,actor_id,principal_id,person_id,"
            "conversation_id,content,episode_ids,support_ids,source_refs,scope_ids,conditions,"
            "counterexamples,occurred_at,attempt_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            ("rbh-old-reflection", core.actor, alice.principal_id, alice.person_id, alice.conversation_id,
             "旧反思不能作为当前称呼规则", ["fixture-episode"], ["fixture-episode"], Jsonb(wire(refs)),
             list(alice.scope_ids), Jsonb({}), Jsonb([]), datetime.now(UTC), "fixture-attempt"),
        )
    ledger = HabitLedger(SimpleNamespace(database=core.database, identity=core.identity))
    resolver = BehaviorResolver(core.database, core.identity, core.sources)
    rules = await ledger.material(alice, purpose="conversation")
    assert len(rules) == 1 and rules[0].behavior_ref["rule_id"] == "rbh-rule"
    selected = assembler(core)
    active = await assemble(selected, alice, contributions=rules, behavior_resolver=resolver)
    assert current.claim_id in evidence(active)
    assert any(item.kind == "learned_habit" and "请使用简短称呼" in item.text for item in active.materials)
    assert not any("旧反思不能作为当前称呼规则" in item.text for item in active.materials)
    assert "structured_memory_unavailable" not in active.missing

    async with core.database.transaction() as connection:
        await connection.execute(
            "UPDATE companion.style_rules SET state='revoked',revision=revision+1,"
            "semantic_revision=semantic_revision+1 WHERE id=%s", ("rbh-rule",),
        )
    assert not await ledger.material(alice, purpose="conversation")
    revoked = await assemble(selected, alice, contributions=rules, behavior_resolver=resolver)
    assert current.claim_id in evidence(revoked)
    assert not any(item.kind == "learned_habit" for item in revoked.materials)
    assert not any("旧反思不能作为当前称呼规则" in item.text for item in revoked.materials)
    assert "contribution_unavailable:learned_habit" in revoked.missing
