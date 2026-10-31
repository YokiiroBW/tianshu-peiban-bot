"""Actual synthetic media and intake measurements; never invoke providers."""

import asyncio
from dataclasses import replace
from io import BytesIO
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, skipUnless
from unittest.mock import AsyncMock, patch
import wave

from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from tianshu.capabilities.api import register_routes
from tianshu.capabilities.contracts import Fault
from tianshu.capabilities.media.engine import run_process
from tianshu.capabilities.media.inspection import inspect_intake
from tianshu.runtime.files import FileStore
from tianshu.runtime.serialization import wire
from tianshu.types import ArtifactRef, Attempt, OriginKind, SourceRef, TrustedContext


def wav_bytes(seconds=8):
    output = BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\x00\x00" * 16000 * seconds)
    return output.getvalue()


class MediaInspection(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.files = FileStore(Path(self.directory.name) / "files")
        self.slots = asyncio.Semaphore(1)

    async def local(self, operation, *args, **kwargs):
        async with self.slots:
            return await asyncio.to_thread(operation, *args, **kwargs)

    async def inspect(self, reference):
        return await inspect_intake(self.files, reference, local=self.local, slots=self.slots)

    async def test_actual_red_image_size_is_attested_without_changing_original(self):
        output = BytesIO()
        Image.new("RGB", (16, 16), (255, 0, 0)).save(output, "PNG")
        original = self.files.put_bytes(output.getvalue(), media_type="image/png")
        measured, observation = await self.inspect(original)
        self.assertTrue(self.files.was_written(measured))
        self.assertEqual((measured.width, measured.height), (16, 16))
        self.assertEqual(measured.sha256, original.sha256)
        self.assertEqual(measured.uri, original.uri)
        self.assertEqual(measured.size_bytes, original.size_bytes)
        self.assertIsNone(measured.duration_ms)
        self.assertEqual(observation["state"], "known")

    async def test_http_upload_registers_measured_intake_before_returning_canonical_media(self):
        from tianshu.capabilities.service import Capabilities

        context = TrustedContext(
            "operator",
            "actor",
            "person",
            "conversation",
            ("scope",),
            ("person",),
            1,
            OriginKind.HUMAN,
            frozenset({"*"}),
        )
        source = SourceRef("uploaded", 1)
        registered = {}

        async def register(context, media, *, locator, origin_kind):
            self.assertTrue(self.files.was_written(media))
            registered.update(media=media, locator=locator)
            return source

        async def read(context, ref, requested):
            return {
                "media": wire(replace(registered["media"], media_id="source:uploaded:1")),
                "locator": registered["locator"],
            }

        async def artifact(context, media, *, source_refs, metadata):
            return ArtifactRef(
                "artifact",
                replace(media, media_id="artifact:artifact"),
                source_refs,
                metadata=metadata,
            )

        owner = SimpleNamespace(
            ready=lambda: None,
            settings={},
            file_slots=self.slots,
            local=self.local,
            runtime=SimpleNamespace(files=self.files),
            authority=SimpleNamespace(check=AsyncMock()),
            sources=SimpleNamespace(register_media=register, read=read),
            artifacts=SimpleNamespace(record=artifact),
        )

        async def execute(action, **kwargs):
            return await Capabilities._operate(
                owner,
                "capabilities",
                "upload",
                action.arguments,
                action.context,
                action,
                Attempt("attempt", action.action_id),
            )

        owner.actions = SimpleNamespace(
            handler=AsyncMock(
                return_value=SimpleNamespace(definition=SimpleNamespace(long_running=False))
            ),
            invoke=execute,
        )

        @web.middleware
        async def identity(request, handler):
            request["trusted_context"] = context
            return await handler(request)

        app = web.Application(middlewares=[identity])
        register_routes(app, owner)
        output = BytesIO()
        Image.new("RGB", (16, 16), "red").save(output, "PNG")
        form = FormData()
        form.add_field("client_request_id", "metadata-request")
        form.add_field(
            "file", output.getvalue(), filename="not-a-duration.bin", content_type="image/png"
        )
        async with TestClient(TestServer(app)) as client:
            response = await client.post("/api/v1/capabilities/upload", data=form)
            self.assertEqual(response.status, 200)
            answer = await response.json()
        canonical = answer["data"]["media"]
        produced = answer["data"]["artifact"]["media"]
        self.assertEqual(canonical["media_id"], "source:uploaded:1")
        for field in ("sha256", "size_bytes", "duration_ms", "width", "height"):
            self.assertEqual(canonical[field], getattr(registered["media"], field))
            self.assertEqual(canonical[field], produced[field])
        self.assertEqual(canonical["width"], 16)
        self.assertEqual(answer["data"]["media_inspection"]["state"], "known")

    @skipUnless(shutil.which("ffprobe"), "local ffprobe is not installed")
    async def test_actual_eight_second_wav_has_measured_duration(self):
        original = self.files.put_bytes(wav_bytes(), media_type="audio/wav")
        measured, observation = await self.inspect(original)
        self.assertEqual(measured.duration_ms, 8000)
        self.assertTrue(self.files.was_written(measured))
        self.assertEqual(observation["missing"], [])
        self.assertEqual(observation["kind"], "audio")

    @skipUnless(
        shutil.which("ffprobe") and shutil.which("ffmpeg"), "local ffmpeg tools are not installed"
    )
    async def test_actual_four_second_video_is_read_from_extensionless_intake(self):
        video = Path(self.directory.name) / "fixture-without-extension"
        status, _, _ = await run_process(
            [
                shutil.which("ffmpeg"),
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=red:s=16x16:r=2:d=4",
                "-c:v",
                "mpeg4",
                "-an",
                "-f",
                "mp4",
                str(video),
            ],
            timeout=15,
        )
        self.assertEqual(status, 0)
        with video.open("rb") as reader:
            original = self.files.put_stream(reader, media_type="video/mp4")
        measured, observation = await self.inspect(original)
        self.assertEqual(measured.duration_ms, 4000)
        self.assertEqual((measured.width, measured.height), (16, 16))
        self.assertEqual(observation["state"], "known")

    async def test_missing_tool_keeps_unknown_duration_instead_of_zero(self):
        original = self.files.put_bytes(wav_bytes(), media_type="audio/wav")
        with patch(
            "tianshu.capabilities.media.inspection.executable_path",
            side_effect=Fault("engine_unavailable", 503),
        ):
            measured, observation = await self.inspect(original)
        self.assertIsNone(measured.duration_ms)
        self.assertEqual(observation["state"], "unavailable")
        self.assertIn("duration_ms", observation["missing"])
        self.assertEqual(observation["error_code"], "engine_unavailable")

    async def test_cancel_is_not_downgraded_to_successful_unknown_metadata(self):
        original = self.files.put_bytes(wav_bytes(), media_type="audio/wav")
        started, stopped = asyncio.Event(), asyncio.Event()

        async def waiting_process(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        with (
            patch("tianshu.capabilities.media.inspection.executable_path", return_value="ffprobe"),
            patch("tianshu.capabilities.media.inspection.run_process", side_effect=waiting_process),
        ):
            task = asyncio.create_task(self.inspect(original))
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(stopped.is_set())
        self.assertIsNone(original.duration_ms)
