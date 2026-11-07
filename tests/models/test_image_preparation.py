"""Source-preserving model image fitting; run by the integration owner."""

import base64
import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from unittest.mock import AsyncMock

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageOps, ImageStat

from tianshu.models.broker import ModelBroker
from tianshu.models.budgets import MAX_INLINE_MEDIA_BYTES
from tianshu.models.catalog import RoutePlan
from tianshu.models.image_preparation import prepare_inline_images
from tianshu.models.protocols import NativeCompiler
from tianshu.models.types import ModelMessage, ModelPart, ModelRequest
from tianshu.runtime.errors import AppError
from tianshu.runtime.files import FileStore
from tianshu.types import OriginKind, RequestBudget, ServiceClass, SourceRef, TrustedContext


def request(parts, **options):
    context = TrustedContext("principal", "actor", "person", "conversation",
        ("scope",), ("scope",), 1, OriginKind.HUMAN)
    return ModelRequest("intent", "attempt", "chat", ServiceClass.INTERACTIVE, context,
        RequestBudget(datetime.now(UTC) + timedelta(seconds=30)),
        (ModelMessage("user", tuple(parts)),), source_refs=(SourceRef("original", 2),), **options)


def picture(files, *, size=(768, 768), alpha=False, exif=None):
    image = Image.new("RGBA" if alpha else "RGB", size,
                      (255, 255, 255, 0) if alpha else "white")
    draw = ImageDraw.Draw(image)
    for y in range(8, size[1], 24):
        draw.text((8, y), "Small text ABC 0123456789: keep fine strokes", fill="black")
        draw.line((8, y + 13, size[0] - 8, y + 13), fill="red", width=1)
    output = BytesIO()
    options = {"compress_level": 0}
    if exif is not None:
        options["exif"] = exif
    image.save(output, format="PNG", **options)
    image.close()
    raw = files.put_bytes(output.getvalue(), media_type="image/png")
    # A source reference remains the source owner even though compilation uses
    # an ephemeral view of its bytes.
    return replace(raw, media_id="source:original:2", width=size[0], height=size[1])


def test_images_that_already_fit_are_not_decoded_or_reencoded(tmp_path):
    files = FileStore(tmp_path)
    media = files.put_bytes(b"small opaque fixture", media_type="image/png")
    original = request((ModelPart("image", media=media),))
    prepared, selected_files, facts = prepare_inline_images(files, original)
    assert prepared is original and selected_files is files and facts is None


@pytest.mark.parametrize("alpha", [False, True])
def test_lossless_full_resolution_copy_retains_small_text_pixels_and_alpha(tmp_path, alpha):
    files = FileStore(tmp_path)
    media = picture(files, alpha=alpha)
    before = files.read_bytes(media)
    original = request((ModelPart("image", media=media),))
    prepared, selected_files, facts = prepare_inline_images(files, original,
        byte_limit=media.size_bytes // 2)
    copy = prepared.messages[0].parts[0].media
    assert facts["prepared_bytes"] <= media.size_bytes // 2
    assert facts["images"][0]["lossless_encoding"] is True
    assert facts["images"][0]["scale"] == 1
    with Image.open(BytesIO(before)) as source, Image.open(BytesIO(selected_files.read_bytes(copy))) as output:
        assert source.size == output.size == (768, 768)
        assert source.convert("RGBA").tobytes() == output.convert("RGBA").tobytes()
    assert files.read_bytes(media) == before
    assert original.messages[0].parts[0].media is media
    assert prepared.intent_id == original.intent_id and prepared.attempt_id == original.attempt_id
    assert prepared.source_refs == original.source_refs
    assert copy.media_id == media.media_id and copy.sha256 != media.sha256


def test_exif_orientation_is_applied_to_the_model_copy_without_altering_the_original(tmp_path):
    files = FileStore(tmp_path)
    exif = Image.Exif()
    exif[274] = 6
    media = picture(files, size=(640, 480), exif=exif)
    original = files.read_bytes(media)
    prepared, selected_files, _ = prepare_inline_images(files,
        request((ModelPart("image", media=media),)), byte_limit=media.size_bytes // 2)
    with Image.open(BytesIO(original)) as source, Image.open(BytesIO(
            selected_files.read_bytes(prepared.messages[0].parts[0].media))) as output:
        expected = ImageOps.exif_transpose(source)
        assert output.size == (480, 640)
        assert ImageChops.difference(expected.convert("RGB"), output.convert("RGB")).getbbox() is None
    assert files.read_bytes(media) == original


def test_full_resolution_high_quality_encoding_precedes_any_resizing(tmp_path):
    files = FileStore(tmp_path)
    # Spatially detailed grayscale texture has no convenient lossless palette
    # reduction, and keeps color subsampling out of the distortion measurement.
    image = Image.frombytes("L", (768, 768), random.Random(731).randbytes(768 * 768)).convert("RGB")
    output = BytesIO()
    image.save(output, format="PNG", compress_level=0)
    media = files.put_bytes(output.getvalue(), media_type="image/png")
    prepared, selected_files, facts = prepare_inline_images(files,
        request((ModelPart("image", media=media),)), byte_limit=media.size_bytes // 4)
    item = facts["images"][0]
    assert not item["lossless_encoding"] and item["quality"] >= 86 and item["scale"] == 1
    with Image.open(BytesIO(selected_files.read_bytes(prepared.messages[0].parts[0].media))) as decoded:
        assert decoded.size == image.size
        assert max(ImageStat.Stat(ImageChops.difference(image, decoded.convert("RGB"))).rms) < 12
    image.close()


def test_unrealistic_target_does_not_turn_small_text_into_a_tiny_thumbnail(tmp_path):
    files = FileStore(tmp_path)
    media = picture(files)
    original = files.read_bytes(media)
    with pytest.raises(AppError, match="model_inline_media_budget_exceeded") as failure:
        prepare_inline_images(files, request((ModelPart("image", media=media),)), byte_limit=256)
    assert failure.value.details["minimum_quality"] == 86
    assert failure.value.details["minimum_scale"] == 0.8
    assert files.read_bytes(media) == original


def test_long_screenshot_keeps_full_geometry_when_webp_dimensions_do_not_fit(tmp_path):
    files = FileStore(tmp_path)
    media = picture(files, size=(64, 17000))
    prepared, selected_files, facts = prepare_inline_images(files,
        request((ModelPart("image", media=media),)), byte_limit=media.size_bytes // 2)
    copy = prepared.messages[0].parts[0].media
    assert facts["images"][0]["scale"] == 1
    with Image.open(BytesIO(selected_files.read_bytes(copy))) as decoded:
        assert decoded.size == (64, 17000)


@pytest.mark.parametrize("protocol", ["gemini.generate", "openai.chat", "openai.responses", "anthropic.messages"])
def test_oversize_batch_is_compressed_in_the_actual_native_payload(tmp_path, protocol):
    files = FileStore(tmp_path)
    media = picture(files)
    original = request((ModelPart("image", media=media),) * 26)
    assert media.size_bytes * 26 > MAX_INLINE_MEDIA_BYTES
    plan = RoutePlan("route", 1, "provider", "vision-model", protocol,
        "https://provider.test/v1", "key", "quota", frozenset({"text", "vision"}), {})
    call = NativeCompiler(files, None).compile(plan, original, stream=False, api_key="fixture-key")
    if protocol == "gemini.generate":
        blocks = call.body["contents"][0]["parts"]
        payloads = [(part["inlineData"]["mimeType"], base64.b64decode(part["inlineData"]["data"])) for part in blocks]
    elif protocol == "anthropic.messages":
        blocks = call.body["messages"][0]["content"]
        payloads = [(part["source"]["media_type"], base64.b64decode(part["source"]["data"])) for part in blocks]
    else:
        blocks = call.body["messages"][0]["content"] if protocol == "openai.chat" else call.body["input"][0]["content"]
        urls = [part["image_url"]["url"] if protocol == "openai.chat" else part["image_url"] for part in blocks]
        payloads = [(uri.split(";", 1)[0][5:], base64.b64decode(uri.split(",", 1)[1])) for uri in urls]
    assert len(payloads) == 26
    assert all(mime == "image/webp" for mime, _ in payloads)
    assert sum(len(data) for _, data in payloads) <= MAX_INLINE_MEDIA_BYTES
    assert call.image_preparation["images"][0]["occurrences"] == 26
    assert original.messages[0].parts[0].media is media
    with Image.open(BytesIO(payloads[0][1])) as decoded:
        assert decoded.size == (768, 768)


def test_other_media_remains_byte_exact_and_counts_toward_the_same_limit(tmp_path):
    files = FileStore(tmp_path)
    media = picture(files)
    audio = files.put_bytes(b"audio" * 100, media_type="audio/wav")
    original = request((ModelPart("image", media=media), ModelPart("audio", media=audio)))
    prepared, selected_files, facts = prepare_inline_images(files, original,
        byte_limit=media.size_bytes // 2)
    assert prepared.messages[0].parts[1] is original.messages[0].parts[1]
    assert selected_files.read_bytes(audio) == b"audio" * 100
    assert facts["prepared_bytes"] == prepared.messages[0].parts[0].media.size_bytes + audio.size_bytes
    assert facts["prepared_bytes"] <= media.size_bytes // 2


def test_animation_is_not_silently_replaced_with_its_first_frame(tmp_path):
    files = FileStore(tmp_path)
    first, second = Image.new("RGB", (64, 64), "red"), Image.new("RGB", (64, 64), "blue")
    output = BytesIO()
    first.save(output, format="GIF", save_all=True, append_images=[second], duration=150, loop=0)
    media = files.put_bytes(output.getvalue(), media_type="image/gif")
    original = request((ModelPart("image", media=media),))
    with pytest.raises(AppError, match="model_inline_media_budget_exceeded"):
        prepare_inline_images(files, original, byte_limit=16)
    with Image.open(BytesIO(files.read_bytes(media))) as image:
        assert image.n_frames == 2


def test_native_payload_and_signed_image_parts_are_not_rewritten(tmp_path):
    files = FileStore(tmp_path)
    media = picture(files)
    native = {"contents": [{"parts": [{"text": "opaque signed continuation"}]}]}
    original = request((ModelPart("image", media=media),), native_payload=native)
    prepared, selected_files, facts = prepare_inline_images(files, original, byte_limit=16)
    assert prepared is original and prepared.native_payload is native
    assert selected_files is files and facts is None
    signed = request((ModelPart("image", media=media, native={"thoughtSignature": "opaque"}),))
    with pytest.raises(AppError, match="model_inline_media_budget_exceeded"):
        prepare_inline_images(files, signed, byte_limit=16)
    assert signed.messages[0].parts[0].native == {"thoughtSignature": "opaque"}


@pytest.mark.asyncio
async def test_broker_resolves_original_authority_before_allowing_oversize_image_preparation():
    from tianshu.types import MediaRef

    media = MediaRef("source:original:2", "sha256:" + "f" * 64,
                     "image/png", MAX_INLINE_MEDIA_BYTES + 1, "f" * 64)
    resolver = AsyncMock(return_value=(media, (SourceRef("original", 2),)))
    broker = ModelBroker(None, None, None, media_resolver=resolver)
    original = request((ModelPart("image", media=media),))
    prepared = await broker.prepare_media(original)
    resolver.assert_awaited_once_with(original.context, media)
    assert prepared.messages[0].parts[0].media is media
    resolver.side_effect = AppError("source_unavailable", status=403)
    with pytest.raises(AppError, match="source_unavailable"):
        await broker.prepare_media(original)
