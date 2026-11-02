"""Visible mismatch is distinct from generation success and unknown verification."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock

from tianshu.capabilities.contracts import canonical
from tianshu.capabilities.images.consistency import ImageConsistency, consistency_result
from tianshu.capabilities.images.service import Images
from tianshu.models.types import ModelCompletion, ModelEvent, ModelPart, ModelReceipt, ModelResult
from tianshu.runtime.errors import AppError
from tianshu.types import Action, ArtifactRef, Attempt, MediaRef, OriginKind, ServiceClass, TrustedContext


def result(checks, completion="complete"):
    return ModelResult((ModelPart("text", text=canonical({"checks": checks})),), ModelReceipt(
        "consistency", "image", "succeeded", "submitted", "configured-vision", 2,
        "fixture-provider", "fixture-vision", "gemini.generate", "fixture-quota"),
        completion=ModelCompletion(completion, ("STOP",)))


def actor_action(kind="share_candidate"):
    context = TrustedContext("owner", "actor", None, "conversation", ("private",), ("private",),
                             1, OriginKind.ACTOR)
    return Action("image", "image-intent", "images.generate", "actor", context,
                  {"delivery_kind": kind}, OriginKind.ACTOR)


class ConsistencyStatus(TestCase):
    def test_daylight_conflict_is_not_a_failed_image_or_a_passed_scene(self):
        state = consistency_result(result([
            {"field": "local_time", "status": "conflict", "observed": "Bright daytime sky through the window"},
            {"field": "outfit", "status": "matched", "observed": "Blue cardigan"}]),
            {"local_time": "2026-11-02T23:00:00+08:00", "outfit": "blue cardigan"})
        self.assertEqual(state["state"], "conflict")
        self.assertEqual(state["regeneration_attempts"], 0)
        self.assertEqual(state["model_receipt"]["route_id"], "configured-vision")

    def test_missing_visual_assessment_stays_unknown(self):
        state = consistency_result(result([]), {"outfit": "blue cardigan"})
        self.assertEqual(state["state"], "unknown")
        self.assertEqual(state["checks"][0]["status"], "unknown")

    def test_recovered_self_initiated_conflict_archives_but_requested_result_remains_available(self):
        artifact = ArtifactRef("image", MediaRef("artifact:image", "controlled", "image/png"))
        completion = {"artifact_ids": ["image"], "consistency": {"state": "conflict"},
                      "generated_at": datetime.now(UTC).isoformat()}
        for kind in ("share_candidate", "requested_delivery"):
            with self.subTest(kind=kind):
                action = actor_action(kind)
                prepared = {"scene": {"local_time": "2026-11-02T23:00:00+08:00"},
                            "intent": {}, "photo_intent": {"delivery_kind": kind}}
                receipt = Images.recovered_receipt(None, action, Attempt("attempt", action.action_id),
                                                  prepared, [artifact], completion)
                self.assertEqual(receipt.execution, "succeeded")
                self.assertEqual(receipt.artifacts, (artifact,))
                self.assertEqual(receipt.result["sharing"]["decision"],
                                 "archive" if kind == "share_candidate" else "recheck")
                self.assertTrue(receipt.result["sharing"]["notice"])

    def test_recovered_create_only_never_requires_expression_recheck(self):
        artifact = ArtifactRef("image", MediaRef("artifact:image", "controlled", "image/png"))
        action = actor_action("create_only")
        prepared = {"scene": {"local_time": "2026-11-02T12:00:00+08:00"},
                    "intent": {}, "photo_intent": {"delivery_kind": "create_only", "target_ref": None}}
        for consistency in ("matched", "unknown", "conflict"):
            with self.subTest(consistency=consistency):
                completion = {"artifact_ids": ["image"], "consistency": {"state": consistency},
                              "generated_at": "2026-11-02T04:00:00+00:00"}
                receipt = Images.recovered_receipt(None, action, Attempt("original-attempt", action.action_id),
                                                  prepared, [artifact], completion)
                self.assertEqual((receipt.execution, receipt.effect, receipt.delivery),
                                 ("succeeded", "confirmed", "not_requested"))
                self.assertEqual(receipt.artifacts, (artifact,))
                self.assertEqual(receipt.attempt_id, "original-attempt")
                self.assertEqual(receipt.result["sharing"]["decision"], "archive")
                self.assertEqual(receipt.result["sharing"]["delivery_kind"], "create_only")
                self.assertEqual(receipt.result["sharing"]["reason"],
                                 "image_consistency_conflict" if consistency == "conflict" else None)
                self.assertEqual(receipt.result["photo_intent"]["artifact_ids"], ["image"])
                self.assertEqual(receipt.result["consistency"], completion["consistency"])


class BoundedResultCheck(IsolatedAsyncioTestCase):
    async def test_unconfigured_vision_is_unknown_and_preserves_generated_artifact(self):
        broker = SimpleNamespace(generate=AsyncMock(), result=AsyncMock(return_value=None),
            generation_profile=AsyncMock(side_effect=AppError("model_function_not_configured", status=503)))
        verifier = ImageConsistency(SimpleNamespace(broker=broker))
        action = actor_action()
        artifact = ArtifactRef("image", MediaRef("artifact:image", "controlled", "image/png"))
        checked = await verifier.check(action, Attempt("attempt", action.action_id),
            {"scene": {"local_time": "2026-11-02T23:00:00+08:00"},
             "intent": {"character": "fictional actor", "light": "night lamps"}}, [artifact])
        request = broker.generation_profile.await_args.args[0]
        self.assertEqual(request.function, "vision")
        self.assertEqual(request.service_class, ServiceClass.ELASTIC)
        self.assertEqual(request.required_capabilities, frozenset({"vision"}))
        self.assertEqual(checked["state"], "unknown")
        self.assertEqual(checked["reason"], "model_function_not_configured")
        self.assertEqual(checked["regeneration_attempts"], 0)
        broker.generate.assert_not_awaited()
        self.assertEqual(broker.generation_profile.await_count, 1)

    async def test_recovery_reuses_persisted_check_without_model_or_regeneration(self):
        images = SimpleNamespace(consistency=SimpleNamespace(check=AsyncMock()))
        prepared = {"completion": {"artifact_ids": ["original"],
                                  "consistency": {"state": "unknown"}, "generated_at": "original-time"}}
        value = await Images.complete_image_result(images, actor_action(), Attempt("a", "image"), prepared, [])
        self.assertIs(value, prepared["completion"])
        images.consistency.check.assert_not_awaited()

    async def test_verified_gemini_stream_uses_native_budget_and_only_persisted_complete_result(self):
        checked_result = result([{"field": "light", "status": "conflict", "observed": "daylight"}])
        requests = []

        async def stream(request):
            requests.append(request)
            yield ModelEvent("text", text='{"checks":[{"field":"light","status":"matched","observed":"uncommitted"}]}')
            yield ModelEvent("receipt", receipt=checked_result.receipt, data=checked_result)

        broker = SimpleNamespace(generate=AsyncMock(), stream=stream,
            result=AsyncMock(side_effect=[None, checked_result]),
            generation_profile=AsyncMock(return_value={"route_id": "configured-vision", "route_revision": 2,
                "protocol": "gemini.generate", "verified_capabilities": ["vision", "stream"],
                "max_output_tokens": 1200, "output_parameters": {"generationConfig": {"maxOutputTokens": 1200}}}))
        action = replace(actor_action(), deadline_at=datetime.now(UTC) + timedelta(seconds=45))
        artifact = ArtifactRef("image", MediaRef("artifact:image", "controlled", "image/png"))
        checked = await ImageConsistency(SimpleNamespace(broker=broker)).check(action, Attempt("original", action.action_id),
            {"scene": {}, "intent": {"light": "night lamps"}}, [artifact])
        self.assertEqual(checked["state"], "conflict")
        self.assertEqual(checked["checks"][0]["observed"], "daylight")
        request = requests[0]
        self.assertEqual((request.attempt_id, request.route_id, request.route_revision, request.protocol),
                         ("original:consistency", "configured-vision", 2, "gemini.generate"))
        self.assertEqual(request.max_output_tokens, 32768)
        self.assertEqual(request.parameters["generationConfig"]["maxOutputTokens"], 32768)
        self.assertEqual(request.required_capabilities, frozenset({"vision", "stream"}))
        self.assertEqual(request.budget.deadline_at, action.deadline_at)
        self.assertEqual(len(requests), 1)
        broker.generate.assert_not_awaited()

    async def test_unknown_original_attempt_is_read_once_without_reprofile_or_resubmission(self):
        original = replace(result([]), parts=(), completion=ModelCompletion(),
            receipt=replace(result([]).receipt, status="unknown", error_code="upstream_response_incomplete", first_output_ms=15))
        broker = SimpleNamespace(result=AsyncMock(return_value=original),
            generate=AsyncMock(), generation_profile=AsyncMock(), stream=AsyncMock())
        artifact = ArtifactRef("image", MediaRef("artifact:image", "controlled", "image/png"))
        checked = await ImageConsistency(SimpleNamespace(broker=broker)).check(actor_action(), Attempt("original", "image"),
            {"scene": {}, "intent": {"light": "night lamps"}}, [artifact])
        self.assertEqual((checked["state"], checked["reason"]), ("unknown", "upstream_response_incomplete"))
        self.assertEqual(checked["model_receipt"]["status"], "unknown")
        self.assertTrue(checked["diagnostics"]["output_observed"])
        self.assertEqual(checked["diagnostics"]["completion"]["state"], "unknown")
        broker.result.assert_awaited_once_with("original:consistency", context=actor_action().context)
        broker.generation_profile.assert_not_awaited()
        broker.generate.assert_not_awaited()
        broker.stream.assert_not_awaited()

    async def test_missing_native_terminal_returns_diagnostics_and_never_repeats_provider_attempt(self):
        original = replace(result([]), parts=(), completion=ModelCompletion(),
            receipt=replace(result([]).receipt, status="unknown", error_code="incomplete_native_stream"))
        requests = []

        async def stream(request):
            requests.append(request)
            yield ModelEvent("native", data={"chunk_b64": "private provider bytes are not diagnostics"})
            raise AppError("incomplete_native_stream", status=502)

        broker = SimpleNamespace(generate=AsyncMock(), stream=stream,
            result=AsyncMock(side_effect=[None, original]),
            generation_profile=AsyncMock(return_value={"route_id": "configured-vision", "route_revision": 2,
                "protocol": "gemini.generate", "verified_capabilities": ["vision", "stream"],
                "max_output_tokens": 40000, "output_parameters": {"generationConfig": {"maxOutputTokens": 40000}}}))
        artifact = ArtifactRef("image", MediaRef("artifact:image", "controlled", "image/png"))
        checked = await ImageConsistency(SimpleNamespace(broker=broker)).check(actor_action(), Attempt("original", "image"),
            {"scene": {}, "intent": {"light": "night lamps"}}, [artifact])
        self.assertEqual(checked["reason"], "incomplete_native_stream")
        self.assertEqual(checked["state"], "unknown")
        self.assertEqual(checked["diagnostics"]["max_output_tokens"], 40000)
        self.assertTrue(checked["diagnostics"]["streamed"] and checked["diagnostics"]["output_observed"])
        self.assertNotIn("private provider bytes", str(checked))
        self.assertEqual(checked["regeneration_attempts"], 0)
        self.assertEqual(len(requests), 1)
        broker.generate.assert_not_awaited()

    async def test_unverified_stream_uses_one_nonstream_check_and_preserves_length_stop(self):
        truncated = replace(result([]), completion=ModelCompletion("incomplete", ("MAX_TOKENS",)))
        broker = SimpleNamespace(result=AsyncMock(return_value=None), generate=AsyncMock(return_value=truncated),
            generation_profile=AsyncMock(return_value={"route_id": "configured-vision", "route_revision": 2,
                "protocol": "gemini.generate", "verified_capabilities": ["vision"],
                "max_output_tokens": 1200, "output_parameters": {}}))
        artifact = ArtifactRef("image", MediaRef("artifact:image", "controlled", "image/png"))
        checked = await ImageConsistency(SimpleNamespace(broker=broker)).check(actor_action(), Attempt("original", "image"),
            {"scene": {}, "intent": {"light": "night lamps"}}, [artifact])
        self.assertEqual(checked["state"], "unknown")
        self.assertEqual(checked["diagnostics"]["completion"]["stop_reasons"], ["MAX_TOKENS"])
        self.assertFalse(checked["diagnostics"]["streamed"])
        self.assertEqual(broker.generate.await_count, 1)
