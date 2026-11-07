"""Fit model image copies without changing the source or frozen request.

Runs inside the compiler's existing preparation thread, after media ownership
checks. Prepared bytes live only for this call; the original FileStore is read
only. Full resolution and lossless encoding take priority over smaller inputs.
"""

from collections import Counter
from dataclasses import dataclass, replace
from hashlib import sha256
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from tianshu.runtime.errors import AppError

from .budgets import IMAGE_PREPARATION_POLICY, MAX_INLINE_MEDIA_BYTES, MAX_INLINE_MEDIA_PARTS

_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
_QUALITIES = (94, 90, 86)
_SCALES = (0.9, 0.8)


@dataclass(frozen=True)
class _ImageCopy:
    data: bytes
    mime_type: str
    width: int
    height: int
    lossless: bool
    quality: int | None
    scale: float


class _PreparedFiles:
    def __init__(self, original, copies):
        self.original, self.copies = original, copies

    def read_bytes(self, media):
        value = self.copies.get(media.sha256)
        return value if value is not None else self.original.read_bytes(media)


def _key(media):
    return media.media_id, media.sha256, media.uri


def _encoded(image, *, lossless, quality=None):
    output = BytesIO()
    options = {"lossless": lossless, "method": 1, "exact": True}
    if quality is not None:
        options["quality"] = quality
    # A converted CMYK profile would no longer describe RGB pixels correctly.
    profile = image.info.get("icc_profile")
    if profile:
        options["icc_profile"] = profile
    try:
        image.save(output, format="WEBP", **options)
        return output.getvalue(), "image/webp"
    except (OSError, ValueError):
        # Very long screenshots can exceed WebP's dimension range even though
        # their pixel count is valid. Preserve full geometry in PNG/JPEG first.
        output = BytesIO()
        options = {"icc_profile": profile} if profile else {}
        if lossless:
            image.save(output, format="PNG", optimize=True, **options)
            return output.getvalue(), "image/png"
        if image.mode != "RGB":
            raise
        image.save(output, format="JPEG", quality=quality, subsampling=0, optimize=True, **options)
        return output.getvalue(), "image/jpeg"


def _image_copy(files, media, *, scale, quality):
    """Prepare one batch-wide quality stage, never a flattened animation."""
    raw = files.read_bytes(media)
    try:
        with Image.open(BytesIO(raw)) as source:
            if getattr(source, "is_animated", False):
                return None
            if scale < 1 and max(source.size) * scale < 2048:
                return None
            oriented = ImageOps.exif_transpose(source)
            try:
                alpha = "A" in oriented.getbands() or "transparency" in oriented.info
                image = oriented.convert("RGBA" if alpha else "RGB")
                if source.mode == "CMYK":
                    image.info.pop("icc_profile", None)
                try:
                    if scale < 1:
                        resized = image.resize(
                            tuple(max(1, round(side * scale)) for side in image.size),
                            Image.Resampling.LANCZOS,
                        )
                        image.close()
                        image = resized
                    data, mime = _encoded(image, lossless=quality is None, quality=quality)
                    if len(data) < len(raw):
                        return _ImageCopy(data, mime, *image.size, quality is None, quality, scale)
                finally:
                    image.close()
            finally:
                oriented.close()
    except (UnidentifiedImageError, OSError, ValueError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        # Other images can still supply enough savings. Never omit this image
        # or quietly lower the quality floor to make a request appear to fit.
        return None
    return None


def prepare_inline_images(files, request, *, byte_limit=MAX_INLINE_MEDIA_BYTES):
    """Return a compiler-only request/files view and bounded preparation facts.

    Native envelopes and signed parts retain their exact bytes. Every original
    media occurrence counts toward the wire budget, even when copies are reused.
    """
    if request.native_payload is not None:
        return request, files, None
    parts = [part for message in request.messages for part in message.parts if part.media]
    if not parts or not any(part.kind == "image" for part in parts):
        return request, files, None
    # Broker resolves actual metadata before compilation. Keep the standalone
    # compiler's external-URL behavior for media which has no local byte size.
    if any(type(part.media.size_bytes) is not int or part.media.size_bytes < 0 for part in parts):
        return request, files, None
    if len(parts) > MAX_INLINE_MEDIA_PARTS:
        raise AppError("model_inline_media_budget_exceeded", status=413)
    before = total = sum(part.media.size_bytes for part in parts)
    if total <= byte_limit:
        return request, files, None
    images, counts = {}, Counter()
    for message in request.messages:
        for part in message.parts:
            media = part.media
            if (part.kind == "image" and media and media.sha256 and not part.native
                    and media.mime_type.partition(";")[0].strip().lower() in _IMAGE_TYPES):
                key = _key(media)
                images.setdefault(key, media)
                counts[key] += 1
    order = sorted(images, key=lambda key: -images[key].size_bytes)
    prepared = {}
    # Complete each quality stage for the whole batch before lowering quality.
    # Savings from another image may make any lossy conversion unnecessary.
    for scale in (1.0, *_SCALES):
        for quality in (None, *_QUALITIES):
            for key in order:
                if total <= byte_limit:
                    break
                media = images[key]
                current_size = len(prepared[key].data) if key in prepared else media.size_bytes
                candidate = _image_copy(files, media, scale=scale, quality=quality)
                if candidate is not None and len(candidate.data) < current_size:
                    prepared[key] = candidate
                    total -= (current_size - len(candidate.data)) * counts[key]
            if total <= byte_limit:
                break
        if total <= byte_limit:
            break
    if total > byte_limit:
        raise AppError("model_inline_media_budget_exceeded", status=413, details={
            "image_preparation": IMAGE_PREPARATION_POLICY,
            "original_bytes": before, "prepared_bytes": total, "limit": byte_limit,
            "minimum_quality": min(_QUALITIES), "minimum_scale": min(_SCALES),
        })
    replacements, copies, facts = {}, {}, []
    for key, image in prepared.items():
        original = images[key]
        identity = sha256(image.data).hexdigest()
        copies[identity] = image.data
        replacements[key] = replace(original, uri="sha256:" + identity,
            sha256=identity, size_bytes=len(image.data), mime_type=image.mime_type,
            width=image.width, height=image.height)
        facts.append({"media_id": original.media_id, "source_sha256": original.sha256,
            "prepared_sha256": identity, "original_bytes": original.size_bytes,
            "prepared_bytes": len(image.data), "width": image.width, "height": image.height,
            "lossless_encoding": image.lossless, "quality": image.quality,
            "scale": image.scale, "occurrences": counts[key]})
    messages = tuple(replace(message, parts=tuple(
        replace(part, media=replacements[_key(part.media)])
        if part.kind == "image" and part.media and _key(part.media) in replacements and not part.native else part
        for part in message.parts)) for message in request.messages)
    facts = {"policy": IMAGE_PREPARATION_POLICY, "original_bytes": before,
             "prepared_bytes": total, "images": facts}
    return replace(request, messages=messages), _PreparedFiles(files, copies), facts
