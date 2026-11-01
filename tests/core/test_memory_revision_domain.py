"""Synthetic domain stories for the single final integration run; not executed in development."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from tianshu.memory.evidence import build_evidence_unit
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind


async def original(core, context, event, text):
    return await core.sources.record_event(context, producer="web", event_id=event,
        revision=1, occurred_at=datetime.now(UTC), parts=({"text": text},), origin_kind=OriginKind.HUMAN)


async def fact(core, context, refs, value="苹果", **extra):
    async with core.database.transaction() as conn:
        return await core.claims.put_on(conn, context, {"subject_id": context.person_id,
            "predicate": "preference.ordinary", "value": value, "speaker_id": context.person_id,
            "assertion": "self_confirmed", "source_refs": refs, **extra}, automatic=True)


def payload(context, value, **extra):
    return {"speaker_id": context.person_id, "predicate": "preference.ordinary", "value": value,
            "assertion": "self_confirmed", "polarity": True, "conditions": {},
            "valid_from": None, "valid_until": None, **extra}


def operation(context, kind, target, content, *, reads=(), groups=(("m1",),), op_id="op1", context_refs=()):
    return {"op_id": op_id, "kind": kind, "subject_id": context.person_id, "target": target,
            "payload": content, "support_groups": [list(group) for group in groups],
            "context_refs": list(context_refs), "reads": list(reads), "reason": "合成领域场景的明确依据"}


async def frozen(core, context, refs, old=None, *, batch_id="domain-demo", extra_materials=None):
    async with core.database.transaction() as conn:
        descriptions = await core.sources.describe_for_write_on(conn, context, refs)
    materials = {"m1": {"source_refs": wire(refs), "speaker_id": context.person_id,
                        "origin_kind": "human", "occurred_at": descriptions[0]["occurred_at"].isoformat()}}
    materials.update(extra_materials or {})
    record = ({"r0": {"kind": "hypothesis" if old.memory_kind == "hypothesis" else "claim",
        "id": old.claim_id, "revision": old.revision, "subject_id": old.subject_id,
        "source_refs": wire(old.source_refs), "scope_ids": list(old.scope_ids),
        "current_status": old.current_status}} if old else {})
    return {"batch_id": batch_id, "subjects": {context.person_id: {"person_id": context.person_id}},
            "materials": materials, "records": record, "read_set": list(record.values())}


async def apply(core, context, batch, operations):
    async with core.database.transaction() as conn:
        return await core.claims.revisions.apply_operations_on(conn, context,
            batch_id=batch["batch_id"], frozen=batch, operations=operations)


async def test_different_values_and_conditions_do_not_invent_contradiction(core, ordinary_users):
    alice, _ = ordinary_users
    first = await fact(core, alice, await original(core, alice, "multi-apple", "我喜欢苹果"))
    second = await fact(core, alice, await original(core, alice, "multi-banana", "我也喜欢香蕉"), "香蕉",
                        conditions={"day": "weekend"})
    assert first.claim_id != second.claim_id
    assert not first.contradictions and not second.contradictions
    async with core.database.transaction(read_only=True) as conn:
        links = await (await conn.execute("SELECT link_id FROM memory.semantic_links WHERE actor_id=%s AND relation_kind='contradicts'",
                                         (alice.actor_id,))).fetchall()
    assert not links


async def test_correction_and_transition_have_different_history(core, ordinary_users):
    alice, _ = ordinary_users
    old = await fact(core, alice, await original(core, alice, "wrong", "我不吃香菜"), "爱吃香菜")
    correction = await frozen(core, alice, await original(core, alice, "fix", "刚才记错了，我不吃香菜"), old)
    result = await apply(core, alice, correction, [operation(alice, "revise_claim",
        {"ref": "r0", "expected_revision": old.revision},
        {"revision_mode": "correction", **payload(alice, "不吃香菜")}, reads=("r0",))])
    assert result["changed"][0]["id"] == old.claim_id
    revised = await core.claims.get(alice, old.claim_id)
    assert revised.value == "不吃香菜" and revised.change_kind == "correct"
    assert (await core.claims.history(alice, old.claim_id)).items[1].value == "爱吃香菜"

    transition_refs = await original(core, alice, "later-change", "我现在开始吃香菜了")
    transition = await frozen(core, alice, transition_refs, revised, batch_id="later-change")
    effective = datetime.now(UTC)
    result = await apply(core, alice, transition, [operation(alice, "revise_claim",
        {"ref": "r0", "expected_revision": revised.revision},
        {"revision_mode": "transition", "successor_ref": "n1", "effective_at": effective.isoformat(),
         **payload(alice, "开始吃香菜", valid_from=effective.isoformat())}, reads=("r0",))])
    assert result["local_refs"]["n1"]["id"] != old.claim_id
    closed = await core.claims.get(alice, old.claim_id)
    assert closed.current_status == "history_only" and closed.valid_until == effective
    successor = await core.claims.get(alice, result["local_refs"]["n1"]["id"])
    assert successor.supersedes == (old.claim_id,)


async def test_full_read_set_and_expected_revision_reject_stale_proposal_atomically(core, ordinary_users):
    alice, _ = ordinary_users
    old = await fact(core, alice, await original(core, alice, "read-old", "我喜欢苹果"))
    batch = await frozen(core, alice, await original(core, alice, "batch-input", "我喜欢香蕉"), old)
    await core.claims.correct(alice, {"claim_id": old.claim_id, "expected_revision": old.revision,
                                     "value": "梨", "client_request_id": "front-correction"})
    # Even an add with no operation reads must validate the complete frozen candidate read_set.
    with pytest.raises(AppError, match="revision_conflict"):
        await apply(core, alice, batch, [operation(alice, "add_claim", {"create": "n1"}, payload(alice, "香蕉"))])
    page = await core.claims.list(alice)
    assert {item.value for item in page.items} == {"梨"}


async def test_independent_or_support_survives_but_necessary_context_is_and(core, ordinary_users):
    alice, _ = ordinary_users
    primary = await original(core, alice, "support-one", "我喜欢苹果")
    old = await fact(core, alice, primary)
    secondary = await original(core, alice, "support-two", "我仍然喜欢苹果")
    batch = await frozen(core, alice, secondary, old)
    await apply(core, alice, batch, [operation(alice, "add_support", {"ref": "r0", "expected_revision": old.revision}, {}, reads=("r0",))])
    await core.sources.invalidate(alice, {"source_id": primary[0].source_id, "expected_revision": 1,
        "client_request_id": "forget-first-support"}, forgotten=True)
    assert (await core.claims.get(alice, old.claim_id)).source_refs == secondary

    context_refs = await original(core, alice, "necessary-context", "这里的它指考试")
    new_refs = await original(core, alice, "dependent", "它改到明天了")
    batch = await frozen(core, alice, new_refs, batch_id="and-context",
        extra_materials={"m2": {"source_refs": wire(context_refs), "origin_kind": "human",
                               "speaker_id": alice.person_id, "occurred_at": datetime.now(UTC).isoformat()}})
    result = await apply(core, alice, batch, [operation(alice, "add_claim", {"create": "n1"},
        payload(alice, "考试改期"), context_refs=("m2",))])
    await core.sources.invalidate(alice, {"source_id": context_refs[0].source_id, "expected_revision": 1,
        "client_request_id": "forget-necessary-context"}, forgotten=True)
    with pytest.raises(AppError):
        await core.claims.get(alice, result["local_refs"]["n1"]["id"])


async def test_private_correction_does_not_replace_public_head(core, ordinary_users):
    alice, bob = ordinary_users
    private = await fact(core, alice, await original(core, alice, "interest-publication", "我喜欢苹果"))
    shared = (await core.claims.share_ordinary(alice, {"claim_id": private.claim_id, "expected_revision": private.revision,
                                                    "client_request_id": "publish-interest"})).data
    own_refs = await original(core, bob, "private-disagreement", "对我来说这份公开认识不适用")
    batch = await frozen(core, bob, own_refs, shared, batch_id="private-correction")
    batch["subjects"][shared.subject_id] = {"person_id": shared.subject_id}
    op = operation(bob, "revise_claim", {"ref": "r0", "expected_revision": shared.revision},
                   {"revision_mode": "correction", **payload(bob, "私人解释", assertion="reported")}, reads=("r0",))
    op["subject_id"] = shared.subject_id
    result = await apply(core, bob, batch, [op])
    assert result["changed"][0]["id"] != shared.claim_id
    assert (await core.claims.get(alice, shared.claim_id)).value == "苹果"


async def test_matter_history_completion_basis_and_missing_receipt(core, ordinary_users):
    alice, _ = ordinary_users
    created = await core.claims.save_matter(alice, {"matter_id": "synthetic-matter", "expected_revision": 0,
        "title": "维修", "state": "planned", "client_request_id": "matter-create"})
    completed = await core.claims.save_matter(alice, {"matter_id": created.data.matter_id,
        "expected_revision": created.revision, "title": "维修", "state": "completed",
        "client_request_id": "matter-complete"})
    assert completed.data.completion_basis["kind"] == "human_report"
    history = await core.claims.matter_history(alice, completed.data.matter_id)
    assert [item.state for item in history.items] == ["completed", "planned"]
    assert all(item.id == created.data.matter_id for item in history.items)
    refs = await original(core, alice, "false-receipt", "维修完成")
    batch = await frozen(core, alice, refs, batch_id="false-receipt")
    with pytest.raises(AppError, match="matter_actual_receipt_unproven"):
        await apply(core, alice, batch, [operation(alice, "upsert_matter", {"create": "n1"},
            {"title": "冒用回执", "state": "completed", "due_at": None, "summary": "", "conditions": {},
             "completion_basis": {"kind": "action_receipt", "material_refs": ["m1"]}})])


async def test_current_valid_at_known_at_choose_version_before_validity(core, ordinary_users):
    alice, _ = ordinary_users
    moment = datetime.now(UTC)
    old = await fact(core, alice, await original(core, alice, "time-original", "过去那条记录有误"), "旧错误",
                     valid_from=moment - timedelta(days=10))
    revised = await core.claims.correct(alice, {"claim_id": old.claim_id, "expected_revision": old.revision,
        "value": "纠正后的事实", "client_request_id": "time-correction"})
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.claim_revisions SET recorded_at=%s WHERE claim_id=%s AND revision=1",
                           (moment - timedelta(days=5), old.claim_id))
        await conn.execute("UPDATE memory.claim_revisions SET recorded_at=%s WHERE claim_id=%s AND revision=%s",
                           (moment, old.claim_id, revised.revision))
    current = await core.claims.list(alice)
    past_truth = await core.claims.list(alice, time_mode="valid_at", valid_at=moment - timedelta(days=2))
    past_knowledge = await core.claims.list(alice, time_mode="known_at", known_at=moment - timedelta(days=2))
    assert current.items[0].value == past_truth.items[0].value == "纠正后的事实"
    assert past_knowledge.items[0].value == "旧错误"
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.claim_revisions SET recorded_at=NULL WHERE claim_id=%s", (old.claim_id,))
    unknown = await core.claims.list(alice, time_mode="known_at", known_at=moment)
    assert not unknown.items and "legacy_recorded_time_unknown" in unknown.coverage.missing


async def test_source_forgetting_removes_historical_versions_and_relationships(core, ordinary_users):
    alice, _ = ordinary_users
    refs = await original(core, alice, "old-history-sensitive", "需被遗忘的旧认识")
    old = await fact(core, alice, refs, "旧认识")
    updated = await core.claims.correct(alice, {"claim_id": old.claim_id, "expected_revision": old.revision,
        "value": "后来纠正", "client_request_id": "history-update"})
    assert len((await core.claims.history(alice, old.claim_id)).items) == 2
    await core.sources.invalidate(alice, {"source_id": refs[0].source_id, "expected_revision": 1,
        "client_request_id": "forget-history"}, forgotten=True)
    assert not (await core.claims.history(alice, old.claim_id)).items
    with pytest.raises(AppError):
        await core.claims.get(alice, updated.data.claim_id)


async def test_manual_correction_replay_returns_original_commit_identity(core, ordinary_users):
    alice, _ = ordinary_users
    old = await fact(core, alice, await original(core, alice, "retry-original", "我喜欢苹果"))
    body = {"claim_id": old.claim_id, "expected_revision": old.revision,
            "value": "梨", "client_request_id": "retry-original-correction"}
    first = await core.claims.correct(alice, body)
    replayed = await core.claims.correct(alice, body)
    assert replayed.revision == first.revision and replayed.data.claim_id == first.data.claim_id
    assert len((await core.claims.history(alice, old.claim_id)).items) == 2


async def test_evidence_unit_retains_conditions_time_author_and_matter_details(core, ordinary_users):
    alice, _ = ordinary_users
    value = await fact(core, alice, await original(core, alice, "complete-evidence", "周末才喝咖啡"), "咖啡",
                       conditions={"day": "weekend"})
    unit = build_evidence_unit(value)
    assert unit.conditions == {"day": "weekend"} and unit.speaker_id == alice.person_id
    assert unit.recorded_at is not None and unit.assertion == "self_confirmed"
    unknown_time = build_evidence_unit(replace(value, recorded_at=None), time_mode="valid_at")
    assert {"recorded_at_unknown", "valid_time_unknown"} <= set(unknown_time.coverage.missing)


async def test_about_matter_keeps_event_identity_and_authorized_derived_projection(core, ordinary_users):
    alice, _ = ordinary_users
    claim = await fact(core, alice, await original(core, alice, "event-fact", "我的考试要改期"), "考试")
    matter = (await core.claims.save_matter(alice, {"matter_id": "exam-event", "expected_revision": 0,
        "title": "旧考试标题", "state": "planned", "client_request_id": "exam-create"})).data
    batch = await frozen(core, alice, await original(core, alice, "event-change", "考试改期到周末"), claim,
                         batch_id="matter-reschedule")
    batch["records"]["r1"] = {"kind": "matter", "id": matter.matter_id, "revision": matter.revision,
        "subject_id": matter.subject_id, "source_refs": wire(matter.source_refs), "scope_ids": list(matter.scope_ids)}
    batch["read_set"] = list(batch["records"].values())
    update = operation(alice, "upsert_matter", {"ref": "r1", "expected_revision": matter.revision},
        {"title": "更清楚的考试标题", "state": "rescheduled", "due_at": None,
         "summary": "改期", "conditions": {}, "completion_basis": None}, reads=("r1",))
    link = operation(alice, "link", {"create": "n1"}, {"relation_kind": "about_matter",
        "from_ref": "r0", "to_ref": "r1", "valid_from": None, "valid_until": None},
        reads=("r0", "r1"), op_id="op2")
    await apply(core, alice, batch, [update, link])
    current = await core.claims.get_matter(alice, matter.matter_id)
    assert current.revision == 2 and current.title == "更清楚的考试标题"
    assert current.claim_ids == (claim.claim_id,)
    assert (await core.claims.get(alice, claim.claim_id)).matter_id == matter.matter_id
    assert build_evidence_unit(current).relations[0].to_ref.revision == 1


async def test_supersedes_cycle_rolls_back_the_whole_proposal(core, ordinary_users):
    alice, _ = ordinary_users
    first = await fact(core, alice, await original(core, alice, "cycle-first", "认识一"), "一")
    second = await fact(core, alice, await original(core, alice, "cycle-second", "认识二"), "二")
    batch = await frozen(core, alice, await original(core, alice, "cycle-input", "两个对象需要检查"), first,
                         batch_id="cycle")
    batch["records"]["r1"] = {"kind": "claim", "id": second.claim_id, "revision": second.revision,
        "subject_id": second.subject_id, "source_refs": wire(second.source_refs), "scope_ids": list(second.scope_ids)}
    batch["read_set"] = list(batch["records"].values())
    links = [operation(alice, "link", {"create": "n" + str(index)},
        {"relation_kind": "supersedes", "from_ref": left, "to_ref": right,
         "valid_from": None, "valid_until": None}, reads=("r0", "r1"), op_id="op" + str(index))
        for index, (left, right) in enumerate((("r0", "r1"), ("r1", "r0")), 1)]
    with pytest.raises(AppError, match="memory_supersedes_cycle"):
        await apply(core, alice, batch, links)
    async with core.database.transaction(read_only=True) as conn:
        rows = await (await conn.execute("SELECT link_id FROM memory.semantic_links WHERE actor_id=%s", (alice.actor_id,))).fetchall()
    assert not rows


async def test_hypothesis_counterexample_is_sourced_and_can_be_revised(core, ordinary_users):
    alice, _ = ordinary_users
    refs = await original(core, alice, "habit", "我工作日通常不喝咖啡")
    contrary = await original(core, alice, "counterexample", "不过周五加班时我喝了咖啡")
    batch = await frozen(core, alice, refs, batch_id="hypothesis",
        extra_materials={"m2": {"source_refs": wire(contrary), "speaker_id": alice.person_id,
                               "origin_kind": "human", "occurred_at": datetime.now(UTC).isoformat()}})
    result = await apply(core, alice, batch, [operation(alice, "revise_hypothesis", {"create": "n1"},
        {**payload(alice, "工作日通常不喝咖啡", assertion="inferred", conditions={"exception": "加班"}),
         "counterexample_refs": ["m2"]})])
    hypothesis = await core.claims.get(alice, result["local_refs"]["n1"]["id"])
    unit = build_evidence_unit(hypothesis)
    assert unit.kind == "hypothesis" and unit.interpretation
    assert unit.counterexample_refs == contrary and unit.conditions == {"exception": "加班"}
    await core.sources.invalidate(alice, {"source_id": contrary[0].source_id, "expected_revision": 1,
        "client_request_id": "forget-counterexample"}, forgotten=True)
    assert not (await core.claims.list(alice)).items


async def test_changed_supporting_proposition_retires_current_hypothesis(core, ordinary_users):
    alice, _ = ordinary_users
    old = await fact(core, alice, await original(core, alice, "derived-basis", "我喜欢苹果"))
    inputs = await original(core, alice, "derived-input", "据此形成有条件认识")
    creation = await frozen(core, alice, inputs, batch_id="dependent-create")
    result = await apply(core, alice, creation, [operation(alice, "revise_hypothesis", {"create": "n1"},
        {**payload(alice, "或许偏爱苹果风味", assertion="inferred"), "counterexample_refs": []})])
    hypothesis = await core.claims.get(alice, result["local_refs"]["n1"]["id"])
    links = await frozen(core, alice, inputs, old, batch_id="dependent-link")
    links["records"]["r1"] = {"kind": "hypothesis", "id": hypothesis.claim_id,
        "revision": hypothesis.revision, "subject_id": hypothesis.subject_id,
        "source_refs": wire(hypothesis.source_refs), "scope_ids": list(hypothesis.scope_ids)}
    links["read_set"] = list(links["records"].values())
    await apply(core, alice, links, [operation(alice, "link", {"create": "n1"},
        {"relation_kind": "supports", "from_ref": "r0", "to_ref": "r1", "valid_from": None, "valid_until": None},
        reads=("r0", "r1"))])
    stale = await frozen(core, alice, inputs, hypothesis, batch_id="dependent-stale")
    await core.claims.correct(alice, {"claim_id": old.claim_id, "expected_revision": old.revision,
        "value": "原先记错，其实不喜欢苹果", "client_request_id": "basis-correction"})
    assert (await core.claims.get(alice, hypothesis.claim_id)).current_status == "history_only"
    assert all(item.claim_id != hypothesis.claim_id for item in (await core.claims.list(alice)).items)
    with pytest.raises(AppError, match="revision_conflict"):
        await apply(core, alice, stale, [operation(alice, "add_support", {"ref": "r0", "expected_revision": hypothesis.revision}, {}, reads=("r0",))])
