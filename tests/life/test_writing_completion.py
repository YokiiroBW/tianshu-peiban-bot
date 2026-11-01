"""A writing request is not a completion; only an actual saved revision notifies Life."""

from contextlib import asynccontextmanager
import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from tianshu.life.writing import WritingService
from tianshu.models.types import ModelCompletion, ModelPart, ModelReceipt, ModelResult
from tianshu.types import OriginKind, SourceRef, TrustedContext


class WritingCompletion(IsolatedAsyncioTestCase):
    async def test_source_revision_and_goal_notification_share_the_chapter_transaction(self):
        context = TrustedContext("owner", "actor", None, None, ("private",), ("private",), 1, OriginKind.ACTOR)
        original, completion = SourceRef("outline", 1), SourceRef("chapter-original", 1)
        chapter = {"id": "chapter", "work_id": "work", "generation_id": "generation", "state": "generating",
                   "current_revision": 0, "revision": 2, "generation_cancel_requested": False}
        writes, callbacks = [], []

        async def execute(sql, params):
            writes.append((sql, params))
            if sql.startswith("UPDATE life.chapters SET current_revision"):
                chapter.update(current_revision=params[0], state="draft", revision=3)

        connection = SimpleNamespace(execute=execute)

        @asynccontextmanager
        async def transaction(**_kwargs):
            yield connection

        sources = SimpleNamespace(validate_persistence=AsyncMock(return_value=True),
                                  record_event=AsyncMock(return_value=(completion,)))
        life = SimpleNamespace(database=SimpleNamespace(transaction=transaction), sources=sources)
        models = SimpleNamespace(generate=AsyncMock())
        writing = WritingService(life, models, None)
        writing._chapter = AsyncMock(side_effect=lambda *_args, **_kwargs: dict(chapter))

        async def on_completed(actual_context, actual_chapter, refs, actual_connection):
            callbacks.append((actual_context, actual_chapter, refs, actual_connection))
            self.assertEqual(len(writes), 2)
            self.assertEqual(actual_chapter["current_revision"], 1)
            self.assertEqual(actual_chapter["state"], "draft")

        writing.bind_completion(on_completed)
        result = ModelResult((ModelPart("text", text="A real saved fictional chapter."),), ModelReceipt(
            "generation", "writing", "succeeded", "submitted", "route", 1, "provider", "model", "gemini.generate", "quota"),
            completion=ModelCompletion("complete", ("STOP",)))
        await writing.save_generated(context, "chapter", "generation", {"work_revision": 1}, result, (original,))
        self.assertEqual(len(callbacks), 1)
        self.assertIs(callbacks[0][3], connection)
        self.assertEqual(callbacks[0][2], (original, completion))
        event = sources.record_event.await_args.kwargs
        self.assertIs(event["connection"], connection)
        self.assertEqual(event["origin_kind"], OriginKind.ACTOR)
        proof = json.loads(event["parts"][0].text)
        self.assertEqual((proof["chapter_id"], proof["content_revision"]), ("chapter", 1))
        self.assertEqual(proof["character_count"], len("A real saved fictional chapter."))
        self.assertTrue(proof["content_signature"])
        self.assertNotIn("A real saved fictional chapter.", event["parts"][0].text)
        self.assertEqual(event["support_refs"], (original,))
        recipe = writes[0][1][-1].obj
        self.assertEqual(recipe["completion_source_refs"][0]["source_id"], completion.source_id)
        # Reconciliation sees the already-saved draft and cannot write or notify twice.
        await writing.save_generated(context, "chapter", "generation", {"work_revision": 1}, result, (original,))
        self.assertEqual(len(callbacks), 1)
        sources.record_event.assert_awaited_once()
        models.generate.assert_not_awaited()

    async def test_revoked_recipe_never_records_a_completion_source_or_notifies_life(self):
        connection = SimpleNamespace(execute=AsyncMock())

        @asynccontextmanager
        async def transaction(**_kwargs):
            yield connection

        sources = SimpleNamespace(validate_persistence=AsyncMock(return_value=False), record_event=AsyncMock())
        writing = WritingService(SimpleNamespace(database=SimpleNamespace(transaction=transaction), sources=sources), None, None)
        writing._chapter = AsyncMock(return_value={"generation_id": "generation", "state": "generating",
            "generation_cancel_requested": False, "current_revision": 0})
        callback = AsyncMock()
        writing.bind_completion(callback)
        result = ModelResult((ModelPart("text", text="Already generated text"),), ModelReceipt(
            "generation", "writing", "succeeded", "submitted", "route", 1, "provider", "model", "gemini.generate", "quota"),
            completion=ModelCompletion("complete", ("STOP",)))
        await writing.save_generated(None, "chapter", "generation", {"work_revision": 1}, result, ())
        sources.record_event.assert_not_awaited()
        callback.assert_not_awaited()
