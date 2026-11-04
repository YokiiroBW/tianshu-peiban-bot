"""Chapter work uses the same asynchronous receipt path as daily diaries."""

from unittest.mock import AsyncMock
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from tianshu.life.generation_control import GenerationControl
from tianshu.life.writing import WritingService
from tianshu.life import publications
from tianshu.runtime.serialization import wire
from tianshu.runtime.errors import AppError
from tianshu.types import SourceRef
from test_rb_diary_experience import rb_fixture


@pytest.mark.asyncio
async def test_chapter_result_from_old_installation_cannot_be_saved(database):
    from uuid import uuid4

    value = await rb_fixture(database)
    writing = WritingService(value.life,value.models,value.diaries.assembler)
    writing.bind_observability(recovery_policy=value.diaries.recovery.policy,statistics=None)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.works(id,actor_id,title,scope_ids) VALUES('work','actor','Story',ARRAY['actor-scope'])")
        await connection.execute("INSERT INTO life.chapters(id,work_id,position,title) VALUES('chapter','work',1,'First')")
    command = await writing.request_generation(value.context,{"id":"chapter","expected_revision":1,
        "client_request_id":"old-installation"})
    original = await value.jobs.current_generation()
    await writing.generate("chapter",command.attempt_id,original)
    assert value.models.completions[command.attempt_id].data_generation == original
    assert value.models.requests[command.attempt_id].data_generation == original
    value.models.complete(command.attempt_id,"旧代次正文")
    async with database.transaction() as connection:
        await connection.execute("UPDATE runtime.installation_generation SET id=%s WHERE singleton=true",(str(uuid4()),))
    with pytest.raises(AppError,match="job_generation_expired"):
        await writing.generate("chapter",command.attempt_id,original)
    async with database.transaction(read_only=True) as connection:
        assert not await (await connection.execute("SELECT 1 FROM life.chapter_revisions WHERE chapter_id='chapter'")).fetchall()


@pytest.mark.asyncio
async def test_chapter_submit_observe_review_and_publication_use_the_saved_version(database):
    value = await rb_fixture(database)
    value.life.sources.record_event = AsyncMock(return_value=(SourceRef("chapter-save", 1),))
    value.life.sources.disclose = AsyncMock(return_value=SourceRef("published-chapter", 1))
    writing = WritingService(value.life, value.models, value.diaries.assembler)
    writing.bind_observability(recovery_policy=value.diaries.recovery.policy,statistics=None)
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
    writing.bind_observability(recovery_policy=value.diaries.recovery.policy,statistics=None)
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
    await controls.work("chapter", "a", first.attempt_id, wire(value.context), "cancel",await value.jobs.current_generation())
    value.models.complete(first.attempt_id, "迟到的 A")
    value.models.complete(second.attempt_id, "完成的 B")
    await writing.generate("a", first.attempt_id)
    await writing.generate("b", second.attempt_id)
    async with database.transaction(read_only=True) as connection:
        revisions = await (await connection.execute("SELECT chapter_id,content FROM life.chapter_revisions")).fetchall()
    assert revisions == [{"chapter_id":"b", "content":"完成的 B"}]


@pytest.mark.asyncio
async def test_chapter_retry_keeps_budget_and_switches_actual_quota_after_known_failure(database):
    value = await rb_fixture(database)
    writing = WritingService(value.life,value.models,value.diaries.assembler)
    writing.bind_observability(recovery_policy=value.diaries.recovery.policy,statistics=None)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.works(id,actor_id,title,scope_ids) VALUES('work','actor','Story',ARRAY['actor-scope'])")
        await connection.execute("INSERT INTO life.chapters(id,work_id,position,title) VALUES('chapter','work',1,'First')")
    command = await writing.request_generation(value.context,{"id":"chapter","expected_revision":1,
        "client_request_id":"writing-original"})
    attempt = command.attempt_id
    await writing.generate("chapter",attempt)
    value.models.receipts[attempt] = replace(value.models.receipts[attempt],status="unknown",dispatch="unknown",
        error_code="transport_unknown")
    await writing.generate("chapter",attempt)
    await writing.generate("chapter",attempt)
    async with database.transaction(read_only=True) as connection:
        original = await (await connection.execute("SELECT * FROM runtime.recovery_attempts WHERE attempt_id=%s",(attempt,))).fetchone()
    assert original["status"] == "unknown" and len(value.models.requests) == 1

    value.models.receipts[attempt] = replace(value.models.receipts[attempt],status="failed",dispatch="submitted",
        error_code="upstream_http_503")
    await writing.generate("chapter",attempt)
    value.models.selected_route = "backup-route"
    past = datetime.now(UTC)-timedelta(seconds=1)
    async with database.transaction() as connection:
        await connection.execute("UPDATE runtime.recovery_lineages SET due_at=%s WHERE lineage_id=%s",(past,original["lineage_id"]))
        await connection.execute("UPDATE runtime.recovery_dependencies SET cooldown_until=%s",(past,))
        await connection.execute("UPDATE life.chapters SET generation_retry_due_at=%s WHERE id='chapter'",(past,))
    await writing.generate("chapter",attempt)
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT * FROM life.chapters WHERE id='chapter'")).fetchone()
    assert row["generation_id"] != attempt
    await writing.generate("chapter",row["generation_id"])
    assert value.models.requests[row["generation_id"]].data_generation == value.models.requests[attempt].data_generation
    async with database.transaction(read_only=True) as connection:
        lineage = await (await connection.execute("SELECT attempts,dependency FROM runtime.recovery_lineages WHERE lineage_id=%s",(original["lineage_id"],))).fetchone()
    assert lineage == {"attempts":2,"dependency":"models:quota:backup-quota"}


@pytest.mark.asyncio
async def test_completed_model_result_cannot_use_a_revised_previous_chapter(database):
    from tianshu.conversation.codec import document

    value = await rb_fixture(database)
    value.life.sources.record_event = AsyncMock(return_value=(SourceRef("chapter-save", 1),))
    writing = WritingService(value.life,value.models,value.diaries.assembler)
    writing.bind_observability(recovery_policy=value.diaries.recovery.policy,statistics=None)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.works(id,actor_id,title,scope_ids) VALUES('work','actor','Story',ARRAY['actor-scope'])")
        await connection.execute("INSERT INTO life.chapters(id,work_id,position,title,current_revision) VALUES('first','work',1,'First',1),('second','work',2,'Second',0)")
        await connection.execute("INSERT INTO life.chapter_revisions(chapter_id,revision,content,work_revision,recipe) VALUES('first',1,'旧章节',1,%s)",
            (document({"source_refs":[]}),))
    command = await writing.request_generation(value.context,{"id":"second","expected_revision":1,
        "client_request_id":"with-first-chapter"})
    await writing.generate("second",command.attempt_id)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.chapter_revisions(chapter_id,revision,content,work_revision,recipe) VALUES('first',2,'新章节',1,%s)",
            (document({"source_refs":[]}),))
        await connection.execute("UPDATE life.chapters SET current_revision=2,revision=revision+1 WHERE id='first'")
    value.models.complete(command.attempt_id,"基于旧章节的正文")
    await writing.generate("second",command.attempt_id)
    async with database.transaction(read_only=True) as connection:
        chapter = await (await connection.execute("SELECT * FROM life.chapters WHERE id='second'")).fetchone()
        rows = await (await connection.execute("SELECT * FROM life.chapter_revisions WHERE chapter_id='second'")).fetchall()
    assert not rows and chapter["state"] == "failed" and chapter["reason"] == "writing_material_changed"
    value.life.sources.record_event.assert_not_awaited()


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
