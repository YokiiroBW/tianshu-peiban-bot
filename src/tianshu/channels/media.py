"""Authorized originals, provider path mappings and bounded native media payloads."""

import asyncio
import base64
import mimetypes
from dataclasses import replace
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
import aiohttp
from tianshu.types import MediaRef, SourceRef, OriginKind
from tianshu.runtime.serialization import wire
from tianshu.runtime.recovery_policy import RecoveryWait
from tianshu.conversation.types import InputPart
from .contracts import ChannelError


class Media:
    def __init__(self, artifacts, sources, files):
        self.artifacts, self.sources, self.files = artifacts, sources, files

    def local_mapping(self, connection, remote):
        if not isinstance(remote, str) or "\x00" in remote:
            raise ChannelError("provider_media_path", 400)
        normalized = remote.replace("\\", "/")
        for mapping in connection.get("media_path_mappings", []):
            prefix = mapping["remote_root"].replace("\\", "/").rstrip("/") + "/"
            if not normalized.startswith(prefix):
                continue
            relative = PurePosixPath(normalized[len(prefix) :])
            if any(part in {".", ".."} for part in relative.parts) or relative.is_absolute():
                raise ChannelError("provider_media_path", 400)
            root = Path(mapping["local_root"]).resolve()
            candidate = (root / Path(*relative.parts)).resolve()
            if not candidate.is_relative_to(root) or not candidate.is_file():
                raise ChannelError("provider_media_path_unavailable", 404)
            return candidate
        raise ChannelError("provider_media_mapping_required", 503)

    async def download(self, connection, url, *, mime_type=None):
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        allowed = connection.get(
            "allowed_media_hosts", ["qpic.cn", "qq.com", "qq.com.cn", "telegram.org"]
        )
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.username
            or parsed.password
            or not any(host == entry or host.endswith("." + entry) for entry in allowed)
        ):
            raise ChannelError("provider_media_origin_not_allowed", 403)
        maximum = min(connection.get("max_media_bytes", 128 * 1024 * 1024), 512 * 1024 * 1024)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as session:
            async with session.get(url, allow_redirects=False) as response:
                if response.status != 200:
                    raise ChannelError("provider_media_unavailable", 502)
                if response.content_length is not None and response.content_length > maximum:
                    raise ChannelError("channel_media_budget", 413)
                first = await response.content.read(65536)
                detected = sniff(first, mime_type or response.content_type)

                async def chunks():
                    yield first
                    async for chunk in response.content.iter_chunked(1024 * 1024):
                        yield chunk

                return await self.files.put_async(chunks(), media_type=detected, max_bytes=maximum)

    async def provider_file(self, connection, descriptor, *, fallback=None, mime_type=None):
        url = descriptor.get("url")
        if url:
            return await self.download(connection, url, mime_type=mime_type)
        path = self.local_mapping(connection, descriptor.get("file", fallback))

        def store():
            with path.open("rb") as reader:
                prefix = reader.read(64)
                reader.seek(0)
                return self.files.put_stream(
                    reader,
                    media_type=sniff(prefix, mime_type or mimetypes.guess_type(path.name)[0]),
                    max_bytes=connection.get("max_media_bytes", 128 * 1024 * 1024),
                )

        return await asyncio.to_thread(store)

    async def receive(
        self, context, connection, part, call_api, *, event_id, part_index, revision=1,
        source_support=(), supplement_revision=None, producer=None, deployment_generation=None,
    ):
        if supplement_revision is not None and not source_support:
            raise ChannelError("supplement_original_source_required", 400)
        if supplement_revision is not None and deployment_generation is None:
            raise ChannelError("supplement_generation_required", 409)
        metadata = dict(part.metadata)
        try:
            raw_ref = None
            if metadata.get("channel") == "telegram":
                descriptor = await call_api("getFile", {"file_id": metadata["file_id"]})
                path = descriptor.get("file_path")
                if not isinstance(path, str) or ".." in PurePosixPath(path).parts:
                    raise ChannelError("telegram_file_path", 502)
                if path.startswith("/"):
                    raw_ref = await self.provider_file(
                        connection, {"file": path}, mime_type=metadata.get("mime_type")
                    )
                # The Bot API token stays in this transient URL, never source metadata/logs.
                token = await call_api("__token__", {})
                base = connection.get("endpoint", "https://api.telegram.org").rstrip("/")
                transient = {**connection, "allowed_media_hosts": [urlsplit(base).hostname]}
                if raw_ref is None:
                    raw_ref = await self.download(
                        transient,
                        base + "/file/bot" + token + "/" + path,
                        mime_type=metadata.get("mime_type") or mimetypes.guess_type(path)[0],
                    )
            else:
                segment = metadata["segment"]
                data = segment["data"]
                descriptor = {}
                url = data.get("url")
                if not url:
                    name = (
                        "get_record"
                        if segment["type"] == "record"
                        else "get_image"
                        if segment["type"] == "image"
                        else None
                    )
                    if name:
                        descriptor = await call_api(
                            name,
                            {
                                "file": data["file"],
                                **({"out_format": "wav"} if name == "get_record" else {}),
                            },
                        )
                    url = descriptor.get("url")
                if url:
                    raw_ref = await self.download(
                        connection, url, mime_type=descriptor.get("mime_type")
                    )
                else:
                    raw_ref = await self.provider_file(
                        connection, descriptor, fallback=data.get("file")
                    )
                if segment["type"] == "record" and descriptor:
                    metadata.update(representation="provider_transcode", original_unavailable=True)
            if supplement_revision is not None:
                source = await self.sources.register_media_supplement(
                    context, raw_ref, original_ref=source_support[0],
                    producer=producer or connection["namespace"] + ":" + connection["id"],
                    event_id=event_id, event_revision=revision, part_index=part_index,
                    supplement_revision=supplement_revision, generation=deployment_generation)
            else:
                source = await self.sources.register_media(
                    context,
                    raw_ref,
                    locator={
                    "kind": "channel_original"
                    if not metadata.get("original_unavailable")
                    else "channel_media_representation",
                    "representation": metadata.get("representation", "original"),
                    "connection_id": connection["id"],
                    "event_id": event_id,
                    "revision": revision,
                    "evidence_unit": connection["namespace"]
                    + ":"
                    + connection["id"]
                    + ":"
                    + event_id,
                    "part_index": part_index,
                    "channel": metadata["channel"],
                    },
                    origin_kind=OriginKind.HUMAN,
                    **({"generation": deployment_generation} if deployment_generation is not None else {}),
                )
            registered = await self.sources.read(
                context, source, {"position": 0, "max_characters": 1}
            )
            media = MediaRef(**registered["media"])
            metadata.update(
                original_pending=False, analysis_completed=False, source_ref=wire(source)
            )
            if metadata.get("channel") == "onebot.v11":
                segment = metadata.pop("segment", {})
                metadata["native_segment"] = {
                    "type": segment.get("type"),
                    "data": {
                        k: v for k, v in segment.get("data", {}).items() if k not in {"url", "file"}
                    },
                }
                if part.kind == "audio" and media.mime_type in {
                    "audio/silk",
                    "audio/amr",
                    "application/octet-stream",
                }:
                    try:
                        descriptor = await call_api(
                            "get_record", {"file": segment["data"]["file"], "out_format": "wav"}
                        )
                        converted = await self.provider_file(
                            connection, descriptor, mime_type="audio/wav"
                        )
                        artifact = await self.artifacts.record(
                            context,
                            converted,
                            source_refs=(source,),
                            metadata={
                                "kind": "channel_audio_transcode",
                                "original_media_id": media.media_id,
                                "representation": "provider_transcode",
                            },
                        )
                        metadata.update(
                            analysis_media=wire(artifact.media), original_media=wire(media)
                        )
                    except asyncio.CancelledError:
                        raise
                    except RecoveryWait:
                        raise
                    except Exception as error:
                        metadata["analysis_unavailable"] = getattr(
                            error, "code", "audio_transcode_unavailable"
                        )
            return replace(part, media=media, metadata=metadata), source
        except asyncio.CancelledError:
            raise
        except RecoveryWait:
            raise
        except Exception as error:
            native = metadata.pop("segment", None)
            if native:
                metadata["native_segment"] = {
                    "type": native.get("type"),
                    "data": {
                        k: v for k, v in native.get("data", {}).items() if k not in {"url", "file"}
                    },
                }
            metadata.update(
                original_pending=False,
                media_unavailable=True,
                error_code=getattr(error, "code", "channel_media_unavailable"),
            )
            return replace(part, metadata=metadata), None

    async def outbound_onebot(self, context, connection, media):
        maximum = connection.get("max_inline_bytes", 16 * 1024 * 1024)
        async with self.artifacts.open_media(context, media) as reader:
            if media.size_bytes is not None and media.size_bytes > maximum:
                actual = Path(reader.name).resolve()
                for mapping in connection.get("media_path_mappings", []):
                    local = Path(mapping["local_root"]).resolve()
                    if actual.is_relative_to(local):
                        return str(
                            PurePosixPath(mapping["remote_root"])
                            / actual.relative_to(local).as_posix()
                        )
                raise ChannelError("channel_large_media_mapping_required", 503)
            raw = bytearray()
            while True:
                chunk = await asyncio.to_thread(reader.read, 1024 * 1024)
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > maximum:
                    raise ChannelError("channel_media_inline_budget", 413)
        return "base64://" + base64.b64encode(raw).decode("ascii")

    async def references(self, context, parts):
        from tianshu.sources.dependencies import unique_refs

        refs = []

        def supplied_media(value):
            if isinstance(value, dict):
                if isinstance(value.get("media"), dict):
                    yield value["media"]
                for key, item in value.items():
                    if key != "media":
                        yield from supplied_media(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    yield from supplied_media(item)

        for supplied in supplied_media(parts):
            media = MediaRef(**supplied)
            if media.media_id.startswith("artifact:"):
                refs.extend((await self.artifacts.reference(context, media.media_id)).source_refs)
            elif media.media_id.startswith("source:"):
                identifier, revision = media.media_id[7:].rsplit(":", 1)
                refs.append(SourceRef(identifier, int(revision)))
            else:
                raise ChannelError("media_owner_required", 400)
        return unique_refs(tuple(refs))


def sniff(raw, fallback=None):
    if raw.startswith(b"GIF8"):
        return "image/gif"
    if raw.startswith(b"\x89PNG\r\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw.startswith(b"RIFF") and raw[8:12] == b"WAVE":
        return "audio/wav"
    if raw.startswith(b"#!AMR"):
        return "audio/amr"
    if b"#!SILK_V3" in raw[:16]:
        return "audio/silk"
    if raw.startswith(b"OggS"):
        return "audio/ogg"
    if raw.startswith(b"ID3") or len(raw) > 1 and raw[0] == 255 and raw[1] & 224 == 224:
        return "audio/mpeg"
    if len(raw) > 8 and raw[4:8] == b"ftyp":
        return "video/mp4"
    if raw.startswith(b"\x1aE\xdf\xa3"):
        return "video/webm"
    return fallback or "application/octet-stream"
