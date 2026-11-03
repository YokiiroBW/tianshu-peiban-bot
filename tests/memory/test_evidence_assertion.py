"""Image upload and structured references do not prove a human self-statement."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tianshu.memory.revisions import RevisionService
from tianshu.runtime.errors import AppError
from tianshu.types import SourceRef


async def values(*descriptions, groups=None, assertion="self_confirmed"):
    async def visible(*args):
        return {"person"}
    claims = SimpleNamespace(memory=SimpleNamespace(sources=None), sources=None, jobs=None,
                             visible_subjects=visible)
    writer = RevisionService(claims)
    context = SimpleNamespace(person_id="person", actor_id="actor")
    records = [{"source_id": "source_" + str(index), "revision": 1, "origin_kind": "human",
        "speaker_id": "person", "occurred_at": datetime.now(UTC), **description}
        for index, description in enumerate(descriptions)]
    refs = [SourceRef(item["source_id"], 1) for item in records]
    support = {"groups": groups(refs) if groups else (tuple(refs),), "descriptions": records}
    return await writer._claim_values_on(None, context, {"subject_id": "person", "speaker_id": "person",
        "predicate": "preference.food", "value": "苹果", "assertion": assertion}, support)


@pytest.mark.parametrize("description", [
    {"locator": {"part_kind": "image"}, "media": {"mime_type": "image/png"}},
    {"locator": {"part_kind": "text", "metadata": {"is_ocr": True}}, "media": None},
    {"locator": {"part_kind": "reply", "metadata": {"message_id": "synthetic"}}, "media": None},
    {"locator": {"part_kind": "mention"}, "media": None},
    {"locator": {}, "media": {"mime_type": "image/jpeg"}},
])
async def test_write_authority_rejects_self_confirmation_from_upload_or_technical_part(description):
    with pytest.raises(AppError, match="claim_self_confirmation_unproven") as caught:
        await values(description)
    assert caught.value.status == 403
    assert caught.value.details["path"] == ["support_groups"]
    observed = await values(description, assertion="observed")
    assert observed["assertion"] == "observed"


async def test_direct_text_and_source_proven_unmarked_legacy_text_keep_self_confirmation():
    for description in ({"locator": {"part_kind": "text"}, "media": None}, {}):
        assert (await values(description))["assertion"] == "self_confirmed"


async def test_an_image_can_support_a_statement_but_cannot_create_an_independent_self_confirmed_alternative():
    text = {"locator": {"part_kind": "text"}, "media": None}
    image = {"locator": {"part_kind": "image"}, "media": {"mime_type": "image/png"}}
    assert (await values(text, image))["assertion"] == "self_confirmed"
    with pytest.raises(AppError, match="claim_self_confirmation_unproven"):
        await values(text, image, groups=lambda refs: ((refs[0],), (refs[1],)))
