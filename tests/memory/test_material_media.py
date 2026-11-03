"""Joint image/text material stories; executed by the integration owner."""

from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from tianshu.conversation.context.assembly import TokenCounter
from tianshu.memory.materials import (
    ConsolidationPolicy, MaterialBuilder, PreparationReads, append_context_material, frozen_mapping,
    has_new_material, material_capabilities, model_material, model_messages,
)
from tianshu.models.budgets import MAX_INLINE_MEDIA_BYTES
from tianshu.runtime.api import Page
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import Coverage, MediaRef, OriginKind, SourceRef, TrustedContext


TIME = datetime(2026, 11, 3, 1, 0, tzinfo=UTC)
CONTEXT = TrustedContext("principal", "actor", "alice", "fixture_group", ("group_scope",),
    ("group_scope",), 1, OriginKind.HUMAN, frozenset(), "group", True)


def original(source, *, text=None, mime=None, speaker="alice", event=None, part_kind=None, metadata=None):
    ref = SourceRef(source, 3, {})
    media = MediaRef("source:"+source+":3", "sha256:"+"f"*64, mime, 32, "f"*64) if mime else None
    return {"source_ref": ref, "content": text,
        "coverage": Coverage(True, len(text) if text is not None else None,
            ranges=({"start": 0, "end": len(text)},) if text is not None else (),
            representation="text" if text is not None else "original"),
        "media": wire(media), "next_position": None, "origin_kind": "human" if speaker else "observation",
        "speaker_id": speaker, "occurred_at": TIME,
        "locator": {"event_id": event or source, "part_kind": part_kind or ("image" if mime else "text"),
            "metadata": metadata or {}}}


def episode(index, reads, *, person="alice"):
    return {"episode_id": "episode_"+str(index), "revision": 1, "sequence": index,
        "person_id": person, "origin_kind": "human", "occurred_at": TIME,
        "source_refs": wire(tuple(item["source_ref"] for item in reads)),
        "extraction_sources": [], "extraction_missing": [], "extraction_cursor": None,
        "actual_result": None, "action_id": None, "receipt_id": None, "matter_id": None,
        "scope_ids": ["group_scope"], "conversation_id": "fixture_group"}


class Sources:
    def __init__(self, reads):
        self.values = {(item["source_ref"].source_id, item["source_ref"].revision): item for item in reads}
        self.calls = []

    async def read(self, context, source, requested):
        self.calls.append((context, source, requested))
        result = deepcopy(self.values[(source.source_id, source.revision)])
        result["source_ref"] = source
        if result["content"] is not None:
            total = len(result["content"])
            start = requested.get("position", (source.locator or {}).get("start", 0))
            end = min(total, start+requested["max_characters"], (source.locator or {}).get("end", total))
            result.update(content=result["content"][start:end], next_position=end if end<total else None,
                coverage=Coverage(end==total, total, ranges=({"start": start, "end": end},), representation="text"))
        return result


class Database:
    @asynccontextmanager
    async def transaction(self, **kwargs):
        yield object()


class Identity:
    async def resolve_memory_subjects_on(self, conn, context, subjects, **kwargs):
        return [{"subject_id": subject, "display_name": subject, "kind": "person",
            "aliases": (), "revision": 1} for subject in sorted(subjects)]


class Builder(MaterialBuilder):
    async def old_records(self, context, subjects, matter_ids, materials, **kwargs):
        return {}


def builder(reads, *, context_messages=0):
    memory = SimpleNamespace(sources=Sources(reads), database=Database(), identity=Identity())
    return Builder(memory, ConsolidationPolicy(context_messages=context_messages))


def conversation_message(index, read):
    return {"message_id": index, "event_id": read["locator"]["event_id"],
        "source_refs": wire((read["source_ref"],)), "occurred_at": TIME}


def bind_conversation(selected, messages, *, neighborhood=()):
    calls = []

    async def conversation(context, **kwargs):
        calls.append(kwargs)
        items = [item for item in messages if item["event_id"] in kwargs["event_ids"]] if "event_ids" in kwargs else neighborhood
        return {"items": tuple(items), "cutoff": 99, "coverage": Coverage(True)}

    selected.bind_conversation(conversation)
    return calls


@pytest.mark.asyncio
async def test_two_new_image_only_authors_are_joint_material_without_prior_caption():
    first = original("image_alice", mime="image/png", speaker="alice")
    second = original("image_bob", mime="image/png", speaker="bob")
    selected = builder((first, second))
    frozen = await selected.build(CONTEXT, [episode(1, [first]), episode(2, [second], person="bob")], "batch_images")
    assert frozen["model_protocol"] == "memory.proposal.v2"
    assert has_new_material(frozen)
    assert material_capabilities(frozen) == frozenset({"text", "vision"})
    assert frozen["targets"]["alice"]["new_material_ids"] == ["m1"]
    assert frozen["targets"]["bob"]["new_material_ids"] == ["m2"]
    assert all(not item["missing"] for item in frozen["episodes"])
    assert frozen["materials"]["m2"]["speaker_id"] == "bob"
    assert frozen["materials"]["m2"]["occurred_at"] == wire(TIME)
    assert frozen["materials"]["m2"]["scope_ids"] == ["group_scope"]
    assert frozen["materials"]["m2"]["source_refs"] == wire((second["source_ref"],))
    messages = model_messages(frozen, selected.policy)
    payload = json.loads(messages[0].parts[0].text)
    assert [item["media"]["ref"] for item in payload["materials"]] == ["m1", "m2"]
    assert [part.media.media_id for part in messages[0].parts if part.kind == "image"] == ["source:image_alice:3", "source:image_bob:3"]
    # Equal bytes in distinct Sources versions/authority domains remain two reads.
    estimates = TokenCounter().measure("", messages, ())[2]
    assert len(estimates) == 2 and sum(item["estimated_tokens"] for item in estimates) == 4096


@pytest.mark.asyncio
async def test_repeated_source_version_keeps_each_reporting_subject_but_sends_one_image():
    image = original("shared_image", mime="image/jpeg")
    selected = builder((image,))
    later = episode(2, [image], person="bob")
    later["occurred_at"] = datetime(2026, 11, 3, 1, 5, tzinfo=UTC)
    frozen = await selected.build(CONTEXT, [episode(1, [image]), later], "batch_repeat")
    assert frozen["targets"]["alice"]["new_material_ids"] == ["m1"]
    assert frozen["targets"]["bob"]["new_material_ids"] == ["m2"]
    assert frozen["materials"]["m2"]["reporting_speaker_id"] == "bob"
    assert frozen["materials"]["m2"]["speaker_id"] == "alice"
    messages = model_messages(frozen, selected.policy)
    payload = json.loads(messages[0].parts[0].text)
    assert [item["media"]["ref"] for item in payload["materials"]] == ["m1", "m1"]
    assert payload["materials"][1]["reported_at"] == wire(later["occurred_at"])
    assert sum(part.kind == "image" for part in messages[0].parts) == 1
    assert frozen["episodes"][1]["material_ids"] == ["m2"]


@pytest.mark.asyncio
async def test_channel_original_uses_verified_uploader_and_the_message_time():
    image = original("channel_original", mime="image/png", speaker=None)
    image.update(origin_kind="human", source_person_id="bob", scope_ids=["source_scope"],
        occurred_at=datetime(2026, 11, 3, 1, 1, tzinfo=UTC))
    image["locator"].update(kind="channel_original")
    selected = builder((image,))
    frozen = await selected.build(CONTEXT, [episode(1, [image])], "batch_uploader")
    material = frozen["materials"]["m1"]
    assert material["speaker_id"] == "bob" and material["reporting_speaker_id"] == "alice"
    assert material["occurred_at"] == wire(TIME)
    assert material["source_occurred_at"] == wire(image["occurred_at"])
    assert material["scope_ids"] == ["source_scope"]
    # It remains visual evidence, even though its human uploader is known.
    assert material["evidence_kind"] == "image" and material["text"] == ""


@pytest.mark.asyncio
async def test_reply_supplies_original_image_and_only_local_aliases_to_the_model():
    metadata = {"event_id": "11", "original_message_id": "11", "reply_to_event_id": "message:group:11",
        "original_author_external_id": "12345678", "original_author_display_name": "Bob",
        "verified_original_author_person_id": "bob"}
    reply = original("reply", text=json.dumps({"kind": "reply", "text": None, "media": None, "metadata": metadata}),
        event="message:group:12", part_kind="reply", speaker=None, metadata=metadata)
    speech = original("speech", text="这张图里是我刚搭好的书架。", event="message:group:12")
    image = original("quoted_image", mime="image/webp", speaker="bob", event="message:group:11")
    selected = builder((reply, speech, image))
    calls = bind_conversation(selected, [conversation_message(11, image)])
    frozen = await selected.build(CONTEXT, [episode(1, [reply, speech])], "batch_quote")
    assert any("message:group:11" in call.get("event_ids", ()) for call in calls)
    payload = model_material(frozen, selected.policy)
    materials = {item["id"]: item for item in payload["materials"]}
    assert materials["m1"]["text"] == "" and materials["m1"]["evidence_kind"] == "reference"
    assert materials["m1"]["reply_to"] == ["m3"]
    assert materials["m2"]["reply_to"] == ["m3"]
    assert materials["m2"]["related_material_ids"] == ["m1"]
    assert materials["m3"]["role"] == "context" and materials["m3"]["speaker_id"] == "bob"
    assert frozen["materials"]["m3"]["context_required"] is True
    assert not frozen["episodes"][0]["missing"]
    visible = json.dumps(payload, ensure_ascii=False)
    assert "message:group:11" not in visible and "12345678" not in visible
    assert materials["m1"]["quoted_author"]["verified_original_author_person_id"] == "bob"
    assert sum(part.kind == "image" for part in model_messages(frozen, selected.policy)[0].parts) == 1


@pytest.mark.asyncio
async def test_only_structural_metadata_does_not_promote_historical_images_into_new_input():
    mention = original("mention", text='{"kind":"mention","metadata":{"external_id":"12345678"}}',
        part_kind="mention", metadata={"verified_subject_ids": ["bob"]})
    image = original("history_image", mime="image/png", speaker="bob")
    selected = builder((mention, image), context_messages=6)
    calls = bind_conversation(selected, [], neighborhood=[conversation_message(1, image)])
    frozen = await selected.build(CONTEXT, [episode(1, [mention])], "batch_metadata")
    assert not has_new_material(frozen)
    assert not calls
    assert frozen["materials"]["m1"]["text"] == ""
    assert model_material(frozen, selected.policy)["materials"][0]["mention_subject_ids"] == ["bob"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("mime", "content", "missing"), [
    (None, '{"kind":"image","text":null,"metadata":{"original_pending":true}}', "image_original_unavailable"),
    ("image/svg+xml", None, "image_mime_unsupported"),
])
async def test_unavailable_or_unsupported_images_have_explicit_missing_coverage(mime, content, missing):
    image = original("unavailable", mime=mime, text=content, part_kind="image")
    selected = builder((image,))
    frozen = await selected.build(CONTEXT, [episode(1, [image])], "batch_missing")
    assert not has_new_material(frozen)
    assert missing in frozen["episodes"][0]["missing"]
    assert missing in model_material(frozen, selected.policy)["materials"][0]["coverage_missing"]
    assert material_capabilities(frozen) == frozenset({"text"})
    assert all(part.kind != "image" for part in model_messages(frozen, selected.policy)[0].parts)


@pytest.mark.asyncio
async def test_paged_missing_image_metadata_never_becomes_a_text_fact():
    image = original("missing_paged", text='{"kind":"image","metadata":{"opaque":"'+'x'*1000+'"}}', part_kind="image")
    selected = builder((image,))
    first = episode(1, [image])
    frozen = await selected.build(CONTEXT, [first], "batch_missing_head", text_ceiling=64)
    assert not has_new_material(frozen)
    following = deepcopy(first)
    following["extraction_cursor"] = frozen["episodes"][0]["after"]
    frozen = await selected.build(CONTEXT, [following], "batch_missing_body", text_ceiling=64)
    assert not has_new_material(frozen)
    assert frozen["materials"]["m1"]["text"] == ""
    assert "image_original_unavailable" in frozen["episodes"][0]["missing"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("size", "missing"), [
    (None, "image_size_unknown"), (-1, "image_size_invalid"),
    (MAX_INLINE_MEDIA_BYTES+1, "image_inline_limit_exceeded"),
])
async def test_single_unusable_image_does_not_poison_other_text_in_the_batch(size, missing):
    image = original("oversized", mime="image/png")
    image["media"]["size_bytes"] = size
    speech = original("speech", text="这段文字仍然可供整理。")
    selected = builder((image, speech))
    frozen = await selected.build(CONTEXT, [episode(1, [image, speech])], "batch_image_size")
    assert has_new_material(frozen)
    assert missing in frozen["episodes"][0]["missing"]
    assert frozen["materials"]["m1"]["evidence_kind"] == "reference"
    assert material_capabilities(frozen) == frozenset({"text"})
    assert all(part.kind != "image" for part in model_messages(frozen, selected.policy)[0].parts)


@pytest.mark.asyncio
async def test_historical_images_are_bounded_by_whole_messages_with_reply_original_first():
    reply = original("reply", text='{"kind":"reply","metadata":{}}', speaker=None,
        part_kind="reply", event="message:group:100", metadata={"reply_to_event_id": "message:group:1"})
    speech = original("speech", text="看看我说的这张。", event="message:group:100")
    history = [original("history_"+str(index), mime="image/png", event="message:group:"+str(index)) for index in range(1, 8)]
    selected = builder((reply, speech, *history), context_messages=6)
    selected.policy = replace(selected.policy, max_input_tokens=16384)
    messages = [conversation_message(index, image) for index, image in enumerate(history, 1)]
    bind_conversation(selected, messages, neighborhood=messages[1:])
    frozen = await selected.build(CONTEXT, [episode(1, [reply, speech])], "batch_context_budget")
    quoted = [item for item in frozen["materials"].values() if item.get("context_required")]
    assert len(quoted) == 1 and quoted[0]["original_ref"]["source_id"] == "history_1"
    assert frozen["selection_coverage"]["context_image_budget_omitted"] > 0
    assert sum(part.kind == "image" for part in model_messages(frozen, selected.policy)[0].parts) < len(history)
    supplied = set(frozen["materials"])
    assert all(set(item["reply_to"]) <= supplied for item in model_material(frozen, selected.policy)["materials"])


def test_supplemented_images_keep_source_version_and_alias_after_manifest_restore():
    image = original("supplemented", mime="image/png", speaker="bob")
    materials = {"m4": {"role": "new", "text": "原文", "speaker_id": "alice", "origin_kind": "human",
        "occurred_at": wire(TIME), "reply_to": [], "source_refs": wire((SourceRef("speech", 1),)), "locator": {}}}
    assert append_context_material(materials, image, extra={"context_message_id": 7}) == 1
    assert set(materials) == {"m4", "m5"}
    assert append_context_material(materials, image) == 0
    assert materials["m5"]["source_refs"] == wire((image["source_ref"],))
    frozen = {"batch_id": "batch_restore", "model_protocol": "memory.proposal.v2", "context": {},
        "materials": materials, "subjects": {}, "targets": {}, "records": {}}
    recorded = model_messages(frozen, ConsolidationPolicy())
    saved = frozen_mapping(frozen)
    assert "text" not in saved["materials"]["m5"]
    assert saved["materials"]["m5"]["media"] == image["media"]
    assert saved["materials"]["m5"]["context_message_id"] == 7
    for item in json.loads(recorded[0].parts[0].text)["materials"]:
        saved["materials"][item["id"]]["text"] = item["text"]
    restored = model_messages(saved, ConsolidationPolicy())
    assert restored == recorded
    other = original("different_scope_source", mime="image/png")
    assert append_context_material(saved["materials"], other) == 1
    assert set(saved["materials"]) == {"m4", "m5", "m6"}
    assert sum(part.kind == "image" for part in model_messages(saved, ConsolidationPolicy())[0].parts) == 2


def test_existing_text_payload_and_legacy_frozen_protocol_keep_their_original_path():
    frozen = {"batch_id": "batch_legacy", "context": {}, "subjects": {}, "targets": {}, "records": {},
        "materials": {"m1": {"role": "new", "speaker_id": "alice", "origin_kind": "human",
            "occurred_at": wire(TIME), "reply_to": [], "text": "仍然是原文", "locator": {}, "source_refs": []}}}
    payload = model_material(frozen, ConsolidationPolicy())
    messages = model_messages(frozen, ConsolidationPolicy())
    assert has_new_material(frozen) and material_capabilities(frozen) == frozenset({"text"})
    assert len(messages) == 1 and len(messages[0].parts) == 1
    assert json.loads(messages[0].parts[0].text) == payload
    assert payload["materials"][0]["text"] == "仍然是原文"
    assert "model_protocol" not in payload and "evidence_kind" not in payload["materials"][0]


@pytest.mark.asyncio
async def test_preparation_fits_images_before_one_remote_recall_and_reuses_selected_range():
    image=original("new_image",mime="image/png")
    speech=original("first_speech",text="先确定这条原文和原图。")
    following=original("following",text="下批材料不能进入本次召回。")
    selected=builder((image,speech,following))
    rows=[episode(1,[image,speech]),episode(2,[following])]
    reads=PreparationReads(selected.sources)
    calls=[]
    async def candidates(context,query,budget,**kwargs):
        calls.append(query)
        return Page((),None,Coverage(True))
    async def old_records(context,subjects,matters,materials,*,reads,budget=None):
        query=" ".join(item["text"] for item in materials.values() if item["role"]=="new")
        await reads.candidates(candidates,context,query,budget)
        return {}
    selected.old_records=old_records
    await selected.build(CONTEXT,rows,"trial-large",reads=reads,include_records=False)
    fitted=await selected.build(CONTEXT,rows[:1],"trial-fit",reads=reads,include_records=False)
    assert not calls
    before=model_messages(fitted,selected.policy)
    await selected.attach_records(CONTEXT,fitted,reads=reads)
    await selected.attach_records(CONTEXT,fitted,reads=reads)
    assert calls==[" 先确定这条原文和原图。"]
    assert model_messages(fitted,selected.policy)==before
    assert material_capabilities(fitted)==frozenset({"text","vision"})
    assert len(selected.sources.calls)==3 and reads.metrics.snapshot()["remote_calls"]==1
    assert fitted["targets"]["alice"]["new_material_ids"]==["m1","m2"]


@pytest.mark.asyncio
async def test_preparation_remote_cache_is_bound_to_authority_and_keeps_degradation():
    reads=PreparationReads(Sources(()))
    calls=[]
    async def candidates(context,query,budget,**kwargs):
        calls.append((context,query))
        return Page((),None,Coverage(False,missing=("ranked_selection","rerank_unavailable")))
    await reads.candidates(candidates,CONTEXT,"query",None,subject_ids=("alice",))
    await reads.candidates(candidates,CONTEXT,"query",None,subject_ids=("alice",))
    assert len(calls)==1 and reads.candidate_missing==("ranked_selection","rerank_unavailable")
    await reads.candidates(candidates,replace(CONTEXT,access_epoch=2),"query",None,subject_ids=("alice",))
    assert len(calls)==2


@pytest.mark.asyncio
async def test_large_old_candidates_cannot_evict_fitted_new_or_referenced_images(monkeypatch):
    from tianshu.memory.consolidation import Consolidation
    from tianshu.memory.materials import refresh_read_set

    reply=original("reply",text='{"kind":"reply","metadata":{}}',speaker=None,
        part_kind="reply",event="message:group:2",metadata={"reply_to_event_id":"message:group:1"})
    image=original("new_image",mime="image/png",event="message:group:2")
    quoted=original("quoted_image",mime="image/png",event="message:group:1",speaker="bob")
    selected=builder((reply,image,quoted))
    bind_conversation(selected,[conversation_message(1,quoted)])
    frozen=await selected.build(CONTEXT,[episode(1,[reply,image])],"budget-candidates",include_records=False)
    originals=deepcopy(frozen["materials"])
    frozen["records"]={"r0":{"kind":"claim","id":"old","revision":4,"subject_id":"charlie",
        "content":"可选旧记录","source_refs":wire((SourceRef("old_source",1),))}}
    frozen["subjects"]["charlie"]={"subject_id":"charlie","name":"Charlie","kind":"person","revision":1,"aliases":[]}
    frozen["targets"]["charlie"]={"new_material_ids":[]}
    frozen["candidate_subject_ids"]=["charlie"]
    refresh_read_set(frozen)
    memory=SimpleNamespace(database=None,identity=None,sources=None,jobs=None)
    pipeline=Consolidation(memory)
    pipeline.counter=TokenCounter("o200k_base")
    actual_request=pipeline._request
    def request(context,value,step_id):
        if value["records"]:
            raise AppError("memory_consolidation_input_budget")
        return actual_request(context,value,step_id)
    monkeypatch.setattr(pipeline,"_request",request)
    request=pipeline._budgeted_request(CONTEXT,frozen,"candidate-trial",allow_required_context=False)
    assert frozen["materials"]==originals
    assert frozen["selection_coverage"]["record_omitted"]==["r0"]
    assert "context_omitted" not in frozen["selection_coverage"]
    assert "charlie" not in frozen["subjects"] and "charlie" not in frozen["targets"]
    assert frozen["read_set"]==[]
    assert sum(part.kind=="image" for message in request.messages for part in message.parts)==2


@pytest.mark.asyncio
async def test_candidate_diagnostic_reserve_protects_limit_sized_original_text_and_image(monkeypatch):
    from tianshu.memory.consolidation import Consolidation
    from tianshu.memory.preparation import CANDIDATE_GAP_CODES, CANDIDATE_GAP_CODE_CHARACTERS

    image=original("new_image",mime="image/png")
    # Distinct digest fragments cross the real tokenizer's input limit without
    # the extreme compression of a repeated single character.
    source_text=" ".join(sha256(str(index).encode("ascii")).hexdigest() for index in range(1024))
    speech=original("speech",text=source_text)
    selected=builder((image,speech))
    template=await selected.build(CONTEXT,[episode(1,[image,speech])],"reserved-limit",include_records=False)
    pipeline=Consolidation(SimpleNamespace(database=None,identity=None,sources=None,jobs=None))
    pipeline.policy=replace(pipeline.policy,max_input_tokens=16384,max_old_records=2)
    pipeline.counter=TokenCounter("o200k_base")
    def prefix(length):
        value=deepcopy(template)
        value["materials"]["m2"]["text"]=source_text[:length]
        value["materials"]["m2"]["source_refs"][0]["locator"]["end"]=length
        return value
    pipeline._budgeted_request(CONTEXT,prefix(0),"reserve-limit",reserve_candidates=True)
    with pytest.raises(AppError) as over_limit:
        pipeline._budgeted_request(CONTEXT,prefix(len(source_text)),"reserve-limit",reserve_candidates=True)
    assert over_limit.value.code=="memory_consolidation_input_budget"
    low,high=0,len(source_text)
    while low<high:
        middle=(low+high+1)//2
        try:
            pipeline._budgeted_request(CONTEXT,prefix(middle),"reserve-limit",reserve_candidates=True)
            low=middle
        except AppError as error:
            assert error.code=="memory_consolidation_input_budget"
            high=middle-1
    assert 0<low<len(source_text)
    frozen=prefix(low)
    original_materials=deepcopy(frozen["materials"])
    frozen["selection_coverage"]["record_candidates_missing"]=[
        str(index)+"x"*(CANDIDATE_GAP_CODE_CHARACTERS-1) for index in range(CANDIDATE_GAP_CODES)]
    frozen["records"]={"r"+str(index):{"kind":"claim","id":"old_"+str(index),"revision":1,
        "subject_id":"alice","source_refs":[]} for index in range(2)}
    actual_request=pipeline._request
    def request(context,value,step_id):
        if value["records"]:
            raise AppError("memory_consolidation_input_budget")
        return actual_request(context,value,step_id)
    monkeypatch.setattr(pipeline,"_request",request)
    request=pipeline._budgeted_request(CONTEXT,frozen,"reserve-limit-final",allow_required_context=False)
    assert frozen["materials"]==original_materials
    assert request.estimated_input_tokens<=pipeline.policy.max_input_tokens
    payload=json.loads(request.messages[0].parts[0].text)
    assert payload["coverage"]["record_omitted"]==2
    assert len(payload["coverage"]["record_candidates_missing"])==CANDIDATE_GAP_CODES
    assert sum(part.kind=="image" for message in request.messages for part in message.parts)==1
