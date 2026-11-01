"""One bounded video step, source-bound continuation, reread and cross-step synthesis."""

import asyncio
import base64
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from tianshu.models.completion import content_completion
from tianshu.models.types import ModelMessage, ModelPart, ModelRequest
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import Coverage, MediaRef, Receipt, RequestBudget, SourceRef

from ..contracts import Fault, canonical
from ..dto import ConsumptionReceipt
from ..external import ProviderError, execution_service_class
from ..local_work import settled
from .decode import _audio, _video
from .understanding import OBSERVATION_INSTRUCTION, covered, observation


class VideoObservation:
    async def _history(self, action, source):
        identifier = (action.arguments.get("reading_state") or {}).get("consumption_id")
        if not identifier:
            return {}
        async with self.database.transaction(read_only=True) as conn:
            row = await (await conn.execute(
                "SELECT c.body,a.action FROM capabilities.consumptions c "
                "JOIN capabilities.actions a ON a.id=c.action_id "
                "WHERE c.id=%s AND c.source_id=%s AND c.source_revision=%s",
                (identifier, source.source_id, source.revision))).fetchone()
        if not row:
            raise Fault("video_checkpoint_not_found", 409)
        original = row["action"]["context"]
        if (original.get("actor_id") != action.context.actor_id
                or not set(original.get("scope_ids", [])) <= set(action.context.scope_ids)
                or not set(original.get("audience", [])) <= set(action.context.audience)):
            raise Fault("video_checkpoint_not_authorized", 403)
        await self.authority.check(action.context, (source,))
        return row["body"]

    async def _decode_video(self, action, media, start, end, *, dense=False):
        async with self.artifacts.open_media(action.context, media) as reader:
            if media.mime_type.startswith("video/"):
                return await settled(asyncio.to_thread, _video, reader, media.sha256,
                                     start, end, 1_500_000, 24 if dense else 8)
            return await settled(asyncio.to_thread, _audio, reader, media.sha256,
                                 start, end, 1_500_000)

    def _video_request(self, action, attempt, parts, refs, *, suffix="", system=OBSERVATION_INSTRUCTION):
        return ModelRequest(intent_id=action.action_id + suffix, attempt_id=attempt.attempt_id + suffix,
            function="video.observe", service_class=execution_service_class(action), context=action.context,
            budget=RequestBudget(action.deadline_at or datetime.now(UTC) + timedelta(minutes=10)),
            messages=(ModelMessage("user", tuple(parts)),), system=system,
            required_capabilities=frozenset("vision" if part.kind == "image" else part.kind
                for part in parts if part.kind != "text") or frozenset({"text"}),
            max_output_tokens=2500, source_refs=refs)

    async def _video_result(self, request):
        result = await self.broker.generate(request)
        text = "".join(part.text or "" for part in result.parts
                       if part.kind == "text" and not part.native.get("thought"))
        if (result.receipt.status != "succeeded" or content_completion(result).state != "complete"
                or not text.strip()):
            raise ProviderError("video_analysis_incomplete", 502,
                effect="none" if result.receipt.status in {"succeeded", "failed", "cancelled"} else "unknown")
        return result, text

    async def _video_parts(self, action, attempt, media, source, decoded, start, end, *, dense=False):
        text = (action.arguments.get("question") or "Understand this actual interval in temporal order.")
        text += f"\nActual source interval: {start:.6f}–{end:.6f}s.\n" + decoded.get("text", "")
        mode = action.arguments.get("observation_mode", "auto")
        # The current Broker can inline native media up to 32 MiB. Larger originals
        # use the established bounded frame/audio route, without inventing an upload API.
        if mode != "sampled" and media.mime_type.startswith("video/") and media.size_bytes is not None and media.size_bytes <= 24 * 1024 * 1024:
            native = ModelPart("video", media=media, native={"videoMetadata": {
                "startOffset": f"{start:.6f}s", "endOffset": f"{end:.6f}s", "fps": 4 if dense else 1}})
            request = self._video_request(action, attempt, [ModelPart("text", text=text), native], (source,))
            required = {"video", "audio"} if decoded.get("source_has_audio") else {"video"}
            request = replace(request, required_capabilities=frozenset(required))
            catalog = getattr(self.broker, "catalog", None)
            if catalog is not None:
                try:
                    route = await catalog.resolve(request)
                    if route.protocol == "gemini.generate":
                        native_text = (action.arguments.get("question") or "Understand this actual interval in temporal order.")
                        native_text += f"\nActual original video, requested window {start:.6f}–{end:.6f}s. "
                        native_text += "Provider internal sampling is unknown. Do not claim every frame was inspected."
                        return [ModelPart("text", text=native_text), native], {
                            "modalities": sorted(required), "model_input_modalities": ["video"],
                            "frame_times": [], "sampling": "native_video_requested_fps_4" if dense else "native_video_requested_fps_1",
                            "provider_sampling": "unknown", "omitted": ["provider_internal_sampling_unknown"],
                            "audio_ranges": [{"unit": "seconds", "start": start, "end": end,
                                "basis": "native_media_input_requested_window"}] if "audio" in required else [],
                            "protocol": route.protocol, "route_revision": route.revision}
                except (AppError, Fault) as error:
                    if getattr(error, "code", "") not in {"model_capability_unverified", "model_function_not_configured", "model_route_unavailable"}:
                        raise
            if mode == "native":
                raise Fault("native_video_route_unavailable", 503)
        elif mode == "native":
            raise Fault("native_video_inline_budget", 413)
        parts, modalities, frame_times = [ModelPart("text", text=text)], set(), []
        for representation in decoded["representations"]:
            kind = "audio" if representation["kind"] == "audio_clip" else "image"
            artifact = await self.artifacts.put(action.context, base64.b64decode(representation["data_base64"]),
                representation["media_type"], source_refs=(source,), metadata={
                    "source_media_id": media.media_id, "at_seconds": representation.get("at_seconds")})
            if kind == "image":
                frame_times.append(representation["at_seconds"])
                parts.append(ModelPart("text", text=f"Actual frame at {representation['at_seconds']:.6f}s"))
            parts.append(ModelPart(kind, media=artifact.media))
            modalities.add(kind)
        if not modalities:
            raise Fault("actual_media_unavailable", 503)
        omitted = list(decoded["gaps"])
        if media.mime_type.startswith("video/") and not decoded.get("source_has_audio"):
            omitted.append("source_has_no_audio")
        return parts, {"modalities": sorted(modalities), "model_input_modalities": sorted(modalities),
            "frame_times": frame_times, "sampling": "dense_frames_and_actual_audio" if dense else "sampled_frames_and_actual_audio",
            "provider_sampling": "unknown", "omitted": omitted,
            "audio_ranges": decoded.get("audio_ranges", [])}

    async def _observe_segment(self, action, attempt, media, source, start, end, *, suffix="", dense=False):
        decoded = await self._decode_video(action, media, start, end, dense=dense)
        actual = decoded["coverage"]
        input_ranges = [actual, *decoded.get("audio_ranges", [])]
        if decoded.get("visual_range"):
            input_ranges.append(decoded["visual_range"])
        model_start = min(item["start"] for item in input_ranges)
        model_end = max(item["end"] for item in input_ranges)
        parts, inputs = await self._video_parts(action, attempt, media, source, decoded,
                                                model_start, model_end, dense=dense)
        request = self._video_request(action, attempt, parts, (source,), suffix=suffix)
        if inputs.get("protocol"):
            request = replace(request, protocol=inputs["protocol"], route_revision=inputs["route_revision"],
                              required_capabilities=frozenset(inputs["modalities"]))
            decoded = {**decoded, "coverage": {"unit": "seconds", "start": model_start, "end": model_end}}
        else:
            catalog = getattr(self.broker, "catalog", None)
            if catalog is not None:
                route = await catalog.resolve(replace(request, required_capabilities=frozenset()))
                missing = {kind for kind in inputs["modalities"]
                           if ("vision" if kind == "image" else kind) not in route.verified_capabilities}
                if missing:
                    parts = [part for part in parts if part.kind not in missing]
                    inputs = {**inputs, "modalities": [kind for kind in inputs["modalities"] if kind not in missing],
                        "model_input_modalities": [kind for kind in inputs["modalities"] if kind not in missing],
                        "omitted": [*inputs["omitted"], *(kind + "_not_supported_by_configured_route" for kind in sorted(missing))],
                        "audio_ranges": [] if "audio" in missing else inputs["audio_ranges"],
                        "frame_times": [] if "image" in missing else inputs["frame_times"]}
                    if not inputs["modalities"]:
                        raise Fault("actual_media_modality_unavailable", 503)
                    parts.insert(0, ModelPart("text", text="Actual supplied modalities: " + ",".join(inputs["modalities"])
                        + ". The decoder description only describes source representations; omitted modalities were not provided to this model."))
                    request = self._video_request(action, attempt, parts, (source,), suffix=suffix)
                request = replace(request, route_revision=route.revision, protocol=route.protocol)
        model, text = await self._video_result(request)
        available = [*inputs["modalities"], *(("subtitle",) if decoded.get("subtitle_ranges") and not inputs.get("protocol") else ())]
        understanding = observation(text, model_start, model_end, available)
        return decoded, inputs, model, understanding

    async def execute(self, action, attempt):
        args = dict(action.arguments)
        await self.authority.check(action.context, action.source_refs)
        if args.get("url"):
            if not getattr(self, "video_originals", None):
                raise Fault("video_acquisition_unavailable", 503)
            artifact, source = await self.video_originals.acquire(action)
            args.update(media=wire(artifact.media), source=wire(source))
        media, source = MediaRef(**args["media"]), SourceRef(**args["source"])
        if not media.mime_type.startswith(("video/", "audio/")):
            raise Fault("media_not_supported", 415)
        await self.authority.check(action.context, (source,))
        original_metadata = {}
        if media.media_id.startswith("artifact:"):
            original = await self.artifacts.reference(action.context, media.media_id)
            if not any((ref.source_id, ref.revision) == (source.source_id, source.revision)
                       for ref in original.source_refs):
                raise Fault("video_source_mismatch", 403)
            media = original.media
            original_metadata = original.metadata
        elif media.media_id != f"source:{source.source_id}:{source.revision}":
            raise Fault("video_source_mismatch", 403)
        start = float(args["start"])
        duration = original_metadata.get("duration_seconds")
        requested_end = float(args.get("end") or duration or start + 30)
        if not 0 <= start < requested_end or requested_end > 86400:
            raise Fault("invalid_range", 400)
        locator = source.locator or {}
        if start < locator.get("start", 0) or requested_end > locator.get("end", 86400):
            raise Fault("video_source_range_forbidden", 403)
        history = await self._history(action, source)
        if history.get("media") and (history["media"].get("media_id") != media.media_id
                or history["media"].get("sha256") != media.sha256):
            raise Fault("video_checkpoint_media_changed", 409)
        original_request = history.get("requested") or {"start": start, "end": requested_end}
        full_part = history.get("reading_state", {}).get("full_part", "end" not in args)
        acquisition = history.get("reading_state", {}).get("acquisition") or {
            key: args[key] for key in ("url", "account_id", "quality") if key in args}
        if history:
            position = history.get("next_position")
            if not position or abs(float(position["start"]) - start) > .05 or abs(float(position["end"]) - requested_end) > .05:
                raise Fault("video_checkpoint_position_changed", 409)
        decoded, inputs, model, understood = await self._observe_segment(
            action, attempt, media, source, start, min(requested_end, start + 30))
        interval = decoded["coverage"]
        evidence = [{"kind": "segment", "range": interval, "model_receipt": wire(model.receipt),
                     "observations": understood["observations"], "input": inputs}]
        unresolved = [*history.get("unresolved_questions", []), *understood["unresolved_questions"]]
        reread = None
        question = next((item for item in unresolved if not item.get("revisited")), None)
        if question and args.get("revisit", True):
            unresolved.remove(question)
            left, right = question["start"], min(question["end"], question["start"] + 8)
            try:
                revisit_action = replace(action, arguments={**args, "question": question["question"]})
                second, second_inputs, second_model, reread = await self._observe_segment(
                    revisit_action, attempt, media, source, left, right, suffix=":revisit", dense=True)
                evidence.append({"kind": "revisit", "range": second["coverage"],
                    "model_receipt": wire(second_model.receipt), "input": second_inputs,
                    "observations": reread["observations"], "question": question["question"]})
                # A successful call alone does not prove the question was resolved.
                unresolved.extend({**item, "revisited": True} for item in reread["unresolved_questions"])
                if not reread["structured"]:
                    unresolved.append({**question, "revisited": True})
            except (Fault, AppError) as error:
                unresolved.append({**question, "revisited": True})
                evidence.append({"kind": "revisit_unavailable", "code": error.code,
                                 "range": {"start": left, "end": right}})
        segments = [*history.get("processed_segments", []), {"unit": "seconds", **interval,
            "analysis_attempt_id": model.receipt.attempt_id, "modalities": inputs["modalities"],
            "model_input_modalities": inputs["model_input_modalities"], "frame_times": inputs["frame_times"],
            "audio_ranges": inputs["audio_ranges"], "omitted": inputs["omitted"]}]
        total = decoded.get("total_seconds")
        target_end = min(float(original_request["end"]), total) if total is not None else float(original_request["end"])
        complete = covered(segments, float(original_request["start"]), target_end)
        next_position = {"unit": "seconds", "start": interval["end"], "end": target_end} if not complete and interval["end"] < target_end else None
        if interval["end"] <= start:
            raise Fault("video_no_progress", 409)
        summaries = [*history.get("reading_state", {}).get("segment_summaries", []),
                     {"range": interval, "summary": understood["summary"], "model_attempt_id": model.receipt.attempt_id}]
        synthesis = history.get("reading_state", {}).get("synthesis")
        synthesized = False
        if len(segments) > 1 and (complete or len(segments) % 8 == 0):
            try:
                summary_model, synthesis = await self._video_result(self._video_request(action, attempt,
                    [ModelPart("text", text=canonical({"previous_synthesis": synthesis,
                        "segments": summaries, "unresolved_questions": unresolved,
                        "instruction": "Synthesize the observed timeline; preserve uncertainties and modality gaps. These summaries are derivative evidence, not new observations. Do not claim full detail understanding."}))],
                    (source,), suffix=":synthesis", system="Synthesize only the supplied source-backed segment observations in temporal order."))
                evidence.append({"kind": "cross_segment_synthesis", "model_receipt": wire(summary_model.receipt),
                                 "derived_from": [item["model_attempt_id"] for item in summaries]})
                summaries = []
                synthesized = True
            except (Fault, AppError) as error:
                evidence.append({"kind": "synthesis_unavailable", "code": error.code})
        text = synthesis if synthesized else understood["summary"] + (
            "\nEarlier segment synthesis (before this interval): " + synthesis if synthesis else "")
        if reread:
            text += "\nRevisited interval: " + reread["summary"]
        analysis = await self.artifacts.put(action.context, text.encode(), "text/plain", source_refs=(source,),
            metadata={"kind": "video_analysis", "model_attempt_id": model.receipt.attempt_id})
        stop = "requested_window_processed" if complete else "checkpoint"
        omitted = list(dict.fromkeys([*(item for item in history.get("omitted", [])
            if item != "cross_segment_synthesis_unavailable" or not synthesized), *inputs["omitted"]]))
        if any(item["kind"] == "synthesis_unavailable" for item in evidence):
            omitted.append("cross_segment_synthesis_unavailable")
        receipt = ConsumptionReceipt(source=source, requested=original_request,
            modalities=tuple(inputs["modalities"]), ranges=(interval,),
            frame_times=tuple(inputs["frame_times"]), sampling=inputs["sampling"],
            omitted=tuple(omitted), next_position=next_position, analysis_completed=True,
            analysis_artifact_id=analysis.artifact_id, model_attempt_id=model.receipt.attempt_id,
            media=media, audio_ranges=tuple(inputs["audio_ranges"]),
            subtitle_ranges=tuple(decoded.get("subtitle_ranges", [])) if "video" not in inputs["model_input_modalities"] else (), ocr_ranges=(),
            processed_segments=tuple(segments), model_input_modalities=tuple(inputs["model_input_modalities"]),
            provider_sampling=inputs["provider_sampling"], evidence=tuple(evidence),
            unresolved_questions=tuple(unresolved), stop_reason=stop, requested_completed=complete)
        next_part = None
        if complete and original_metadata.get("parts") and full_part:
            part = next((item for item in original_metadata["parts"]
                         if item["index"] > original_metadata["part_index"]), None)
            if part:
                next_part = {"capability_id": "video.observe", "arguments": {
                    **acquisition, "url": original_metadata["url"], "cid": int(part["cid"]), "start": 0,
                    "question": args.get("question", ""), "content_key": args.get("content_key", "")}}
        state = {"consumption_id": attempt.attempt_id, "segment_summaries": summaries,
                 "synthesis": synthesis, "next_part": next_part,
                 "full_part": full_part, "acquisition": acquisition,
                 "budget": {"segments_processed": len(segments), "step_seconds": 30,
                     "max_revisits_per_step": 1, "model_calls_this_step": len(evidence)}}
        body = {**wire(receipt), "reading_state": state}
        async with self.database.transaction() as conn:
            from ..withdrawal import validate_sources

            if not await validate_sources(conn, action.context, (source,), self.artifacts.sources):
                raise Fault("source_revoked", 410)
            await conn.execute("INSERT INTO capabilities.consumptions(id,action_id,source_id,source_revision,body) "
                "VALUES(%s,%s,%s,%s,%s::jsonb) ON CONFLICT(id) DO NOTHING",
                (attempt.attempt_id, action.action_id, source.source_id, source.revision, canonical(body)))
        return self._consumption_receipt(action, attempt, receipt=wire(receipt), artifact=analysis,
                                         text=text, state=state)

    def _consumption_receipt(self, action, attempt, *, receipt, artifact, text, state):
        from ..content.continuation import continuation_arguments

        result = {"analysis": text, "consumption": receipt, "reading_state": state,
                  "source_refs": [receipt["source"]], "read_basis": "actual_media_model_input",
                  "content_key": action.arguments.get("content_key"),
                  "exploration": {"budget": state.get("budget", {})}}
        following = continuation_arguments("video.observe", action.arguments, result)
        result["continuation"] = {"capability_id": "video.observe", "arguments": following} if following else state.get("next_part")
        return Receipt(str(uuid4()), action.action_id, attempt.attempt_id, "succeeded", "none", "not_requested",
            artifacts=(artifact,), coverage=Coverage(False, missing=tuple(receipt["omitted"]),
                ranges=tuple(receipt["ranges"]), representation=receipt["sampling"]), result=result)

    async def reconcile(self, action, attempt):
        async with self.database.transaction(read_only=True) as conn:
            row = await (await conn.execute("SELECT body FROM capabilities.consumptions WHERE action_id=%s AND id=%s",
                (action.action_id, attempt.attempt_id))).fetchone()
        if not row:
            return Receipt(str(uuid4()), action.action_id, attempt.attempt_id, "unknown", "none", "not_requested",
                           error_code="analysis_result_unknown")
        body = row["body"]
        artifact = await self.artifacts.reference(action.context, "artifact:" + body["analysis_artifact_id"])
        raw, mime = await self.artifacts.get(action.context, artifact.media.media_id)
        if not mime.startswith("text/") or artifact.metadata.get("model_attempt_id") != body["model_attempt_id"]:
            raise Fault("analysis_artifact_mismatch", 409)
        return self._consumption_receipt(action, attempt, receipt={key: value for key, value in body.items() if key != "reading_state"},
            artifact=artifact, text=raw.decode("utf-8"), state=body["reading_state"])

    async def cancel(self, action, attempt):
        return await self.reconcile(action, attempt)
