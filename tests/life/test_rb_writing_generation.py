"""Chapter work uses the same asynchronous receipt path as daily diaries."""

from unittest.mock import AsyncMock
from datetime import date

import pytest

from tianshu.life.generation_control import GenerationControl
from tianshu.life.writing import WritingService
from tianshu.life import publications
from tianshu.runtime.serialization import wire
from tianshu.runtime.errors import AppError
from tianshu.types import SourceRef
from test_rb_diary_experience import rb_fixture


@pytest.mark.asyncio
async def test_chapter_submit_observe_review_and_publication_use_the_saved_version(database):
    value = await rb_fixture(database)
    value.life.sources.record_event = AsyncMock(return_value=(SourceRef("chapter-save", 1),))
    value.life.sources.disclose = AsyncMock(return_value=SourceRef("published-chapter", 1))
    writing = WritingService(value.life, value.models, value.diaries.assembler)
    callback = AsyncMock()
    writing.bind_completion(callback)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.works(id,actor_id,title,scope_ids) VALUES('work','actor','Story',ARRAY['actor-scope'])")
        await connection.execute("INSERT INTO life.chapters(id,work_id,position,title,plan) VALUES('chapter','work',1,'First','Plan')")
    command = await writing.request_generation(value.context, {"id":"chapter", "expected_revision":1,
        "client_request_id":"generate", "instruction":"写第一章"})
    await writing.generate("chapter", command.attempt_id)
    assert value.models.completions[command.attempt_id].task == "life.chapter_generate"
    async with database.transaction(read_only=True) as connection:
        assert not await (await connection.execute("SELECT * FROM life.chapter_revisions")).fetchall()
    assert callback.await_count == 0

    value.models.complete(command.attempt_id, "章节正文")
    await writing.generate("chapter", command.attempt_id)
    await writing.generate("chapter", command.attempt_id)
    async with database.transaction(read_only=True) as connection:
        chapter = await (await connection.execute("SELECT * FROM life.chapters WHERE id='chapter'")).fetchone()
        revisions = await (await connection.execute("SELECT * FROM life.chapter_revisions")).fetchall()
    assert len(revisions) == 1 and revisions[0]["model_attempt_id"] == command.attempt_id
    assert callback.await_count == 1 and len(value.models.requests) == 1
    review = await writing.review(value.context, {"id":"chapter", "expected_revision":chapter["revision"],
        "client_request_id":"review", "state":"approved"})
    published = await writing.publish(value.context, {"id":"chapter", "expected_revision":review.revision,
        "client_request_id":"publish", "reader_ids":["reader"]})
    assert published.data["published_revision"] == 1
    disclosure = value.life.sources.disclose.await_args.kwargs
    assert disclosure["reader_ids"] == ("reader",) and disclosure["parts"][0].text == "章节正文"


@pytest.mark.asyncio
async def test_cancelled_chapter_never_applies_late_success_to_another_chapter(database):
    value = await rb_fixture(database)
    value.life.sources.record_event = AsyncMock(return_value=(SourceRef("chapter-save", 1),))
    writing = WritingService(value.life, value.models, value.diaries.assembler)
    controls = GenerationControl(value.life, writing, value.diaries)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.works(id,actor_id,title,scope_ids) VALUES('work','actor','Story',ARRAY['actor-scope'])")
        await connection.execute("INSERT INTO life.chapters(id,work_id,position,title) VALUES('a','work',1,'A'),('b','work',2,'B')")
    first = await writing.request_generation(value.context, {"id":"a", "expected_revision":1,
        "client_request_id":"first"})
    second = await writing.request_generation(value.context, {"id":"b", "expected_revision":1,
        "client_request_id":"second"})
    await writing.generate("a", first.attempt_id)
    await writing.generate("b", second.attempt_id)
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT * FROM life.chapters WHERE id='a'")).fetchone()
    await controls.request(value.context, {"id":"a", "expected_revision":row["revision"],
        "client_request_id":"cancel"}, kind="chapter", operation="cancel")
    await controls.work("chapter", "a", first.attempt_id, wire(value.context), "cancel")
    value.models.complete(first.attempt_id, "迟到的 A")
    value.models.complete(second.attempt_id, "完成的 B")
    await writing.generate("a", first.attempt_id)
    await writing.generate("b", second.attempt_id)
    async with database.transaction(read_only=True) as connection:
        revisions = await (await connection.execute("SELECT chapter_id,content FROM life.chapter_revisions")).fetchall()
    assert revisions == [{"chapter_id":"b", "content":"完成的 B"}]


@pytest.mark.asyncio
async def test_published_reader_sees_only_explicitly_authorized_disclosures():
    class Authority:
        async def check_many(self, context, groups):
            return tuple(bool(group) and group[0].source_id == context for group in groups)

        async def check(self, context, refs):
            if not refs or refs[0].source_id != context:
                raise AppError("forbidden", status=403)

    rows = [{"id":"visible", "actor_id":"actor", "day":date(2026,11,2), "published_revision":1,
             "disclosure_ref":wire(SourceRef("reader", 1))},
            {"id":"private", "actor_id":"actor", "day":date(2026,11,2), "published_revision":1,
             "disclosure_ref":wire(SourceRef("other", 1))}]
    page = await publications.page(Authority(), "reader", rows, 10, publications.diary)
    assert [item["id"] for item in page.items] == ["visible"]
    await publications.readable(Authority(), "reader", rows[0])
    with pytest.raises(AppError, match="forbidden"):
        await publications.readable(Authority(), "reader", rows[1])
