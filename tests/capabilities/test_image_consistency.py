"""Visible mismatch is distinct from generation success and unknown verification."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock

from tianshu.capabilities.contracts import canonical
from tianshu.capabilities.images.consistency import ImageConsistency, consistency_result
from tianshu.capabilities.images.service import Images
from tianshu.models.types import ModelCompletion, ModelPart, ModelReceipt, ModelResult
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
        broker = SimpleNamespace(generate=AsyncMock(side_effect=AppError("model_function_not_configured", status=503)))
        verifier = ImageConsistency(SimpleNamespace(broker=broker))
        action = actor_action()
        artifact = ArtifactRef("image", MediaRef("artifact:image", "controlled", "image/png"))
        checked = await verifier.check(action, Attempt("attempt", action.action_id),
            {"scene": {"local_time": "2026-11-02T23:00:00+08:00"},
             "intent": {"character": "fictional actor", "light": "night lamps"}}, [artifact])
        request = broker.generate.await_args.args[0]
        self.assertEqual(request.function, "vision")
        self.assertEqual(request.service_class, ServiceClass.ELASTIC)
        self.assertEqual(request.required_capabilities, frozenset({"vision"}))
        self.assertEqual(checked["state"], "unknown")
        self.assertEqual(checked["reason"], "model_function_not_configured")
        self.assertEqual(checked["regeneration_attempts"], 0)
        self.assertEqual(broker.generate.await_count, 1)

    async def test_recovery_reuses_persisted_check_without_model_or_regeneration(self):
        images = SimpleNamespace(consistency=SimpleNamespace(check=AsyncMock()))
        prepared = {"completion": {"artifact_ids": ["original"],
                                  "consistency": {"state": "unknown"}, "generated_at": "original-time"}}
        value = await Images.complete_image_result(images, actor_action(), Attempt("a", "image"), prepared, [])
        self.assertIs(value, prepared["completion"])
        images.consistency.check.assert_not_awaited()
