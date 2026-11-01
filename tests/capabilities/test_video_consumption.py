"""Actual input evidence and persisted progress; no provider or external account calls."""

from contextlib import asynccontextmanager
from io import BytesIO
import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock
import wave

from PIL import Image

from tianshu.capabilities.contracts import Fault, canonical
from tianshu.capabilities.perception.decode import _audio, representation
from tianshu.capabilities.perception.service import Perception
from tianshu.capabilities.perception.understanding import observation
from tianshu.models.types import ModelCompletion, ModelPart, ModelReceipt, ModelResult
from tianshu.runtime.serialization import wire
from tianshu.types import Action, ArtifactRef, Attempt, MediaRef, OriginKind, SourceRef, TrustedContext


def result(attempt_id="attempt", text="Observed actual input"):
    return ModelResult((ModelPart("text", text=text),), ModelReceipt(
        attempt_id, "action", "succeeded", "submitted", "synthetic", 1, "fixture",
        "fixture", "gemini.generate", "fixture"), completion=ModelCompletion("complete", ("STOP",)))


def context(actor="actor"):
    return TrustedContext("owner", actor, None, None, ("private",), ("private",), 1, OriginKind.ACTOR)


def wav_original():
    output = BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x01\x00" * 32000)
    return output.getvalue()


class EvidenceValidation(TestCase):
    def test_subtitles_cannot_become_audio_or_visual_evidence(self):
        for modality in ("audio", "image"):
            with self.subTest(modality=modality), self.assertRaises(Fault):
                observation(canonical({"summary": "claimed", "observations": [
                    {"start": 0, "end": 2, "modality": modality, "detail": "unsupported"}]}),
                    0, 2, ["subtitle"])
        with self.assertRaises(Fault):
            observation(canonical({"summary": "claimed", "observations": [
                {"start": 0, "end": 12, "modality": "audio", "detail": "past actual end"}]}),
                0, 2, ["audio"])


class ActualVideoInputs(IsolatedAsyncioTestCase):
    def owner(self, broker=None):
        stored = []

        async def put(_context, raw, mime, **kwargs):
            stored.append((raw, mime, kwargs))
            return ArtifactRef(str(len(stored)), MediaRef("artifact:" + str(len(stored)),
                "controlled", mime, size_bytes=len(raw)), tuple(kwargs.get("source_refs", ())),
                metadata=kwargs.get("metadata", {}))

        owner = Perception(broker or SimpleNamespace(generate=AsyncMock(return_value=result())),
            SimpleNamespace(put=put), SimpleNamespace(check=AsyncMock()), None)
        return owner, stored

    async def test_actual_wav_clip_is_a_model_audio_part_with_exact_audio_coverage(self):
        decoded = _audio(wav_original(), "original-hash", 0, 2, 1500000)
        owner, stored = self.owner()
        owner._decode_video = AsyncMock(return_value=decoded)
        source = SourceRef("audio", 1)
        action = Action("action", "intent", "video.observe", "actor", context(),
                        {"start": 0, "end": 2, "observation_mode": "sampled"}, OriginKind.ACTOR)
        _, inputs, _, _ = await owner._observe_segment(action, Attempt("attempt", "action"),
            MediaRef("source:audio:1", "controlled", "audio/wav"), source, 0, 2)
        request = owner.broker.generate.await_args.args[0]
        self.assertEqual(request.required_capabilities, frozenset({"audio"}))
        self.assertEqual([part.kind for part in request.messages[0].parts], ["text", "audio"])
        self.assertEqual(inputs["audio_ranges"], [{"unit": "seconds", "start": 0.0, "end": 2.0}])
        with wave.open(BytesIO(stored[0][0]), "rb") as audio:
            self.assertEqual(audio.getnframes(), 32000)
        self.assertEqual(inputs["frame_times"], [])

    async def test_verified_native_route_has_requested_window_and_unknown_internal_sampling(self):
        broker = SimpleNamespace(catalog=SimpleNamespace(resolve=AsyncMock(return_value=SimpleNamespace(
            protocol="gemini.generate", revision=7))))
        owner, _ = self.owner(broker)
        action = Action("action", "intent", "video.observe", "actor", context(),
            {"start": 10, "end": 20}, OriginKind.ACTOR)
        parts, inputs = await owner._video_parts(action, Attempt("attempt", "action"),
            MediaRef("artifact:video", "controlled", "video/mp4", size_bytes=1000), SourceRef("video", 2),
            {"source_has_audio": True, "text": "sample fallback text"}, 10, 20)
        native = next(part for part in parts if part.kind == "video")
        self.assertEqual(native.native["videoMetadata"], {"startOffset": "10.000000s",
            "endOffset": "20.000000s", "fps": 1})
        self.assertEqual(inputs["model_input_modalities"], ["video"])
        self.assertEqual(inputs["frame_times"], [])
        self.assertEqual(inputs["provider_sampling"], "unknown")
        self.assertEqual(broker.catalog.resolve.await_args.args[0].required_capabilities,
                         frozenset({"video", "audio"}))

    async def test_sampled_video_sends_both_actual_frames_and_audio(self):
        output = BytesIO()
        Image.new("RGB", (8, 8), "red").save(output, "JPEG")
        owner, _ = self.owner()
        action = Action("action", "intent", "video.observe", "actor", context(),
            {"start": 0, "end": 2, "observation_mode": "sampled"}, OriginKind.ACTOR)
        decoded = {"text": "actual source", "gaps": ["frames_sampled"], "source_has_audio": True,
            "audio_ranges": [{"unit": "seconds", "start": 0, "end": 1.8}], "representations": [
                representation(output.getvalue(), "image/jpeg", "video_frame", "original", 0),
                representation(wav_original(), "audio/wav", "audio_clip", "original", 0)]}
        parts, inputs = await owner._video_parts(action, Attempt("attempt", "action"),
            MediaRef("artifact:video", "controlled", "video/mp4"), SourceRef("video", 1), decoded, 0, 1.8)
        self.assertEqual({part.kind for part in parts}, {"text", "image", "audio"})
        self.assertEqual(inputs["audio_ranges"][0]["end"], 1.8)
        self.assertEqual(inputs["frame_times"], [0])
        self.assertIn("frames_sampled", inputs["omitted"])

    async def test_vision_only_route_keeps_missing_audio_explicit(self):
        output = BytesIO()
        Image.new("RGB", (8, 8), "red").save(output, "JPEG")
        route = SimpleNamespace(protocol="gemini.generate", revision=1,
                                verified_capabilities=frozenset({"text", "vision"}))
        broker = SimpleNamespace(catalog=SimpleNamespace(resolve=AsyncMock(return_value=route)),
                                 generate=AsyncMock(return_value=result()))
        owner, _ = self.owner(broker)
        owner._decode_video = AsyncMock(return_value={"coverage": {"unit": "seconds", "start": 0, "end": 2},
            "text": "Source has audio and frames", "gaps": ["frames_sampled"], "source_has_audio": True,
            "audio_ranges": [{"unit": "seconds", "start": 0, "end": 2}], "representations": [
                representation(output.getvalue(), "image/jpeg", "video_frame", "original", 0),
                representation(wav_original(), "audio/wav", "audio_clip", "original", 0)]})
        action = Action("action", "intent", "video.observe", "actor", context(),
            {"start": 0, "end": 2, "observation_mode": "sampled"}, OriginKind.ACTOR)
        _, inputs, _, _ = await owner._observe_segment(action, Attempt("attempt", "action"),
            MediaRef("artifact:video", "controlled", "video/mp4"), SourceRef("video", 1), 0, 2)
        request = broker.generate.await_args.args[0]
        self.assertNotIn("audio", {part.kind for part in request.messages[0].parts})
        self.assertEqual(request.required_capabilities, frozenset({"vision"}))
        self.assertEqual(inputs["audio_ranges"], [])
        self.assertIn("audio_not_supported_by_configured_route", inputs["omitted"])


class PersistedConsumption(IsolatedAsyncioTestCase):
    async def test_continuation_loads_server_evidence_and_synthesizes_across_segments(self):
        source = SourceRef("original", 3)
        media = MediaRef("artifact:original", "controlled", "video/mp4")
        previous = {"requested": {"start": 0, "end": 60},
            "processed_segments": [{"unit": "seconds", "start": 0, "end": 30, "analysis_attempt_id": "first"}],
            "next_position": {"start": 30, "end": 60}, "unresolved_questions": [],
            "reading_state": {"segment_summaries": [{"range": {"start": 0, "end": 30},
                "summary": "Server witnessed red before blue", "model_attempt_id": "first"}]}}
        stored = {}

        async def execute(query, params):
            if query.startswith("SELECT"):
                return SimpleNamespace(fetchone=AsyncMock(return_value={"body": previous,
                    "action": {"context": wire(context())}}))
            stored.update(json.loads(params[-1]))
            return SimpleNamespace()

        @asynccontextmanager
        async def transaction(**kwargs):
            yield SimpleNamespace(execute=execute)

        artifact = ArtifactRef("analysis", MediaRef("artifact:analysis", "controlled", "text/plain"), (source,))
        broker = SimpleNamespace(generate=AsyncMock(return_value=result("synthesis", "Red, then blue, then silence.")))
        owner = Perception(broker, SimpleNamespace(reference=AsyncMock(return_value=ArtifactRef(
            "original", media, (source,))), put=AsyncMock(return_value=artifact),
            sources=SimpleNamespace(validate_persistence=AsyncMock(return_value=True))),
            SimpleNamespace(check=AsyncMock()), SimpleNamespace(transaction=transaction))
        owner._observe_segment = AsyncMock(return_value=({"coverage": {"unit": "seconds", "start": 30, "end": 60},
            "total_seconds": 60, "subtitle_ranges": []}, {"modalities": ["audio", "image"],
            "model_input_modalities": ["audio", "image"], "frame_times": [30, 45],
            "sampling": "sampled_frames_and_actual_audio", "omitted": ["frames_sampled"],
            "audio_ranges": [{"start": 30, "end": 60}], "provider_sampling": "unknown"},
            result("second"), {"summary": "The second interval becomes silent.", "observations": [],
                "unresolved_questions": [], "structured": True}))
        action = Action("second-action", "second-intent", "video.observe", "actor", context(),
            {"media": wire(media), "source": wire(source), "start": 30, "end": 60,
             "reading_state": {"consumption_id": "first", "segment_summaries": ["forged narrative"]}}, OriginKind.ACTOR)
        receipt = await owner.execute(action, Attempt("second-attempt", action.action_id))
        self.assertTrue(receipt.result["consumption"]["requested_completed"])
        self.assertEqual(len(receipt.result["consumption"]["processed_segments"]), 2)
        self.assertIsNone(receipt.result["continuation"])
        text = broker.generate.await_args.args[0].messages[0].parts[0].text
        self.assertIn("Server witnessed red before blue", text)
        self.assertNotIn("forged narrative", text)
        self.assertEqual(stored["requested"], {"start": 0, "end": 60})
        self.assertEqual(stored["model_input_modalities"], ["audio", "image"])

    async def test_checkpoint_from_another_actor_is_rejected_before_model_work(self):
        @asynccontextmanager
        async def transaction(**kwargs):
            yield SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(fetchone=AsyncMock(return_value={
                "body": {}, "action": {"context": wire(context("someone-else"))}}))))

        owner = Perception(SimpleNamespace(generate=AsyncMock()), None, SimpleNamespace(check=AsyncMock()),
                           SimpleNamespace(transaction=transaction))
        action = Action("action", "intent", "video.observe", "actor", context(),
                        {"reading_state": {"consumption_id": "other"}}, OriginKind.ACTOR)
        with self.assertRaises(Fault) as forbidden:
            await owner._history(action, SourceRef("source", 1))
        self.assertEqual(forbidden.exception.code, "video_checkpoint_not_authorized")
        owner.broker.generate.assert_not_awaited()
