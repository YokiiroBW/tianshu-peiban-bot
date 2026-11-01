"""Direct multimodal input and actual interval analysis with honest consumption evidence."""

import io
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from PIL import Image
from tianshu.types import SourceRef, RequestBudget
from tianshu.models.types import ModelRequest, ModelMessage, ModelPart
from tianshu.models.completion import content_completion
from tianshu.runtime.serialization import wire
from ..catalog import CapabilityDefinition, Availability
from ..contracts import Fault, canonical
from ..external import ProviderError, execution_service_class
from .video import VideoObservation


class Perception(VideoObservation):
    definition = CapabilityDefinition(
        "video.observe",
        "观看片段",
        "读取真实音画并记录实际已处理范围。",
        {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "cid": {"type": "integer"},
                "account_id": {"type": "string"},
                "quality": {"type": "object"},
                "media": {"type": "object"},
                "source": {"type": "object"},
                "start": {"type": "number", "minimum": 0},
                "end": {"type": "number", "exclusiveMinimum": 0},
                "question": {"type": "string"},
                "observation_mode": {"enum": ["auto", "native", "sampled"]},
                "reading_state": {"type": "object", "properties": {
                    "consumption_id": {"type": "string"}}},
                "revisit": {"type": "boolean"},
                "content_key": {"type": "string"},
            },
            "required": ["start"],
            "oneOf": [{"required": ["media", "source", "end"]}, {"required": ["url"]}],
        },
        {"type": "object"},
        "read",
    )

    def __init__(self, broker, artifacts, authority, database):
        self.broker, self.artifacts, self.authority, self.database = (
            broker,
            artifacts,
            authority,
            database,
        )

    async def availability(self, context):
        return Availability(True, True)

    async def image_parts(self, context, media, source_refs=(), *, maximum_frames=8):
        raw, mime = await self.artifacts.resolve(context, media)
        if mime != "image/gif":
            return (ModelPart("image", media=media),), {
                "sampling": "original",
                "frame_times": [],
                "omitted": [],
            }
        frames = []
        times = []
        with Image.open(io.BytesIO(raw)) as image:
            count = getattr(image, "n_frames", 1)
            indices = sorted(
                {
                    round(i * (count - 1) / max(1, maximum_frames - 1))
                    for i in range(min(count, maximum_frames))
                }
            )
            at = 0.0
            for index in range(count):
                image.seek(index)
                duration = float(image.info.get("duration", 100)) / 1000
                if index in indices:
                    output = io.BytesIO()
                    image.convert("RGB").save(output, format="JPEG")
                    artifact = await self.artifacts.put(
                        context,
                        output.getvalue(),
                        "image/jpeg",
                        source_refs=source_refs,
                        metadata={
                            "original_media_id": media.media_id,
                            "at_seconds": at,
                            "frame_index": index,
                        },
                    )
                    frames.extend(
                        [
                            ModelPart("text", text=f"GIF frame {index} at {at:.3f}s"),
                            ModelPart("image", media=artifact.media),
                        ]
                    )
                    times.append(at)
                at += duration
        return tuple(frames), {
            "sampling": "uniform_including_first_and_last",
            "frame_times": times,
            "omitted": ["frames_sampled"] if count > len(indices) else [],
        }

    async def direct_parts(self, context, media, source_refs=()):
        if media.mime_type.startswith("image/"):
            return await self.image_parts(context, media, source_refs)
        if media.mime_type.startswith("audio/"):
            return (ModelPart("audio", media=media),), {"sampling": "original", "omitted": []}
        if media.mime_type.startswith("video/"):
            return (ModelPart("video", media=media),), {"sampling": "original", "omitted": []}
        raise Fault("media_not_supported", 415)

    async def describe(
        self, context, media, *, question, function="vision", source_refs=(), budget=None
    ):
        if media.media_id.startswith("artifact:"):
            artifact = await self.artifacts.reference(context, media.media_id)
            source_refs = tuple(
                {canonical(wire(x)): x for x in (*source_refs, *artifact.source_refs)}.values()
            )
        elif media.media_id.startswith("source:"):
            identifier, revision = media.media_id[7:].rsplit(":", 1)
            source_refs = tuple(
                {
                    canonical(wire(x)): x
                    for x in (*source_refs, SourceRef(identifier, int(revision)))
                }.values()
            )
        await self.authority.check(context, source_refs)
        parts, coverage = await self.direct_parts(context, media, source_refs)
        result = await self.broker.generate(
            ModelRequest(
                intent_id=str(uuid4()),
                attempt_id=str(uuid4()),
                function=function,
                service_class=execution_service_class(context),
                context=context,
                budget=budget or RequestBudget(datetime.now(UTC) + timedelta(minutes=3)),
                messages=(ModelMessage("user", (ModelPart("text", text=question), *parts)),),
                required_capabilities=frozenset(
                    {
                        "vision" if part.kind == "image" else part.kind
                        for part in parts
                        if part.kind != "text"
                    }
                ),
                max_output_tokens=1800,
                source_refs=source_refs,
            )
        )
        text = "".join(part.text or "" for part in result.parts if part.kind == "text")
        completion = content_completion(result)
        if result.receipt.status != "succeeded" or completion.state == "incomplete" or not text.strip():
            raise ProviderError("media_analysis_incomplete", 502,
                effect="none" if result.receipt.status in {"succeeded", "failed", "cancelled"} else "unknown")
        return {
            "text": text,
            "coverage": coverage,
            "model_receipt": wire(result.receipt),
        }
