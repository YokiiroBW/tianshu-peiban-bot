"""A saved revision and its source proof share one writing transaction."""

from contextlib import asynccontextmanager
import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from tianshu.life.writing import WritingService
from tianshu.models.types import ModelCompletion, ModelPart, ModelReceipt, ModelResult
from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind, SourceRef, TrustedContext


class WritingCompletion(IsolatedAsyncioTestCase):
    async def test_source_revision_and_goal_notification_share_the_chapter_transaction(self):
        context = TrustedContext("owner", "actor", None, None, ("private",), ("private",), 1, OriginKind.ACTOR)
        original, completion = SourceRef("outline", 1), SourceRef("chapter-original", 1)
        chapter = {"id":"chapter", "work_id":"work", "generation_id":"generation", "state":"generating",
            "current_revision":0, "revision":2, "generation_cancel_requested":False}
        writes, callbacks, order = [], [], []

        async def execute(sql, params=()):
            if sql.startswith("SELECT * FROM life.chapters"):
                return SimpleNamespace(fetchone=AsyncMock(return_value=dict(chapter)))
            if sql.startswith("SELECT work_id FROM life.chapters"):
                return SimpleNamespace(fetchone=AsyncMock(return_value={"work_id":"work"}))
            if sql.startswith(("INSERT INTO life.chapter_revisions", "UPDATE life.chapters SET current_revision")):
                writes.append((sql,params))
                if sql.startswith("UPDATE"):
                    chapter.update(current_revision=params[0],state="draft",revision=3)
                return None
            raise AssertionError(sql)

        connection = SimpleNamespace(execute=execute)

        @asynccontextmanager
        async def transaction(**_kwargs):
            yield connection

        async def prepare(*_args, **_kwargs):
            order.append("prepare_sources")

        sources = SimpleNamespace(validate_persistence=AsyncMock(return_value=True),
            prepare_event_writes=AsyncMock(side_effect=prepare),record_event=AsyncMock(return_value=(completion,)))
        life = SimpleNamespace(database=SimpleNamespace(transaction=transaction),sources=sources,
            jobs=SimpleNamespace(assert_generation_on=AsyncMock()),validate_persistence=AsyncMock())
        models = SimpleNamespace(generate=AsyncMock())
        writing = WritingService(life,models,None)
        async def locked_chapter(*_args, **_kwargs):
            order.append("lock_chapter")
            return dict(chapter)
        writing._chapter = AsyncMock(side_effect=locked_chapter)
        writing._work = AsyncMock(return_value={"revision":1})
        writing.recovery = SimpleNamespace(assert_applicable_on=AsyncMock())

        async def on_completed(actual_context, actual_chapter, refs, actual_connection):
            callbacks.append((actual_context,actual_chapter,refs,actual_connection))
            self.assertEqual(len(writes),2)
            self.assertEqual(actual_chapter["current_revision"],1)
            self.assertEqual(actual_chapter["state"],"draft")

        writing.bind_completion(on_completed)
        result = ModelResult((ModelPart("text",text="A real saved fictional chapter."),),ModelReceipt(
            "generation","writing","succeeded","submitted","route",1,"provider","model","gemini.generate","quota",
            data_generation="fixture-generation"),completion=ModelCompletion("complete",("STOP",)))
        recipe = {"work_revision":1,"data_generation":"fixture-generation"}
        with patch("tianshu.life.writing.record_saved_on",new=AsyncMock()):
            await writing.save_generated(context,"chapter","generation",recipe,result,(original,))
            self.assertEqual(order[:2],["prepare_sources","lock_chapter"])
            self.assertEqual(len(callbacks),1)
            self.assertIs(callbacks[0][3],connection)
            self.assertEqual(callbacks[0][2],(original,completion))
            event = sources.record_event.await_args.kwargs
            prepared = sources.prepare_event_writes.await_args
            self.assertIs(prepared.args[0],connection)
            self.assertEqual(prepared.kwargs["additional_refs"],(original,))
            self.assertEqual(prepared.args[2][0]["parts"],event["parts"])
            self.assertIs(event["connection"],connection)
            self.assertEqual(event["origin_kind"],OriginKind.ACTOR)
            proof = json.loads(event["parts"][0].text)
            self.assertEqual((proof["chapter_id"],proof["content_revision"]),("chapter",1))
            self.assertEqual(proof["character_count"],len("A real saved fictional chapter."))
            self.assertTrue(proof["content_signature"])
            self.assertNotIn("A real saved fictional chapter.",event["parts"][0].text)
            self.assertEqual(event["support_refs"],(original,))
            stored_recipe = writes[0][1][-2].obj
            self.assertEqual(stored_recipe["completion_source_refs"][0]["source_id"],completion.source_id)
            # Reconciliation cannot write or notify a second time.
            await writing.save_generated(context,"chapter","generation",recipe,result,(original,))
        self.assertEqual(len(callbacks),1)
        sources.record_event.assert_awaited_once()
        models.generate.assert_not_awaited()

    async def test_revoked_recipe_never_records_a_completion_source_or_notifies_life(self):
        chapter = {"id":"chapter","work_id":"work","current_revision":0,"revision":1}
        connection = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
            fetchone=AsyncMock(return_value=chapter))))

        @asynccontextmanager
        async def transaction(**_kwargs):
            yield connection

        sources = SimpleNamespace(validate_persistence=AsyncMock(return_value=False),
            prepare_event_writes=AsyncMock(side_effect=AppError("source_support_unavailable",status=403)),
            record_event=AsyncMock())
        life = SimpleNamespace(database=SimpleNamespace(transaction=transaction),sources=sources,
            jobs=SimpleNamespace(assert_generation_on=AsyncMock()),validate_persistence=AsyncMock())
        writing = WritingService(life,None,None)
        callback = AsyncMock()
        writing.bind_completion(callback)
        result = ModelResult((ModelPart("text",text="Already generated text"),),ModelReceipt(
            "generation","writing","succeeded","submitted","route",1,"provider","model","gemini.generate","quota",
            data_generation="fixture-generation"),completion=ModelCompletion("complete",("STOP",)))
        with self.assertRaisesRegex(AppError,"source_support_unavailable"):
            await writing.save_generated(None,"chapter","generation",
                {"work_revision":1,"data_generation":"fixture-generation"},result,())
        sources.record_event.assert_not_awaited()
        callback.assert_not_awaited()
