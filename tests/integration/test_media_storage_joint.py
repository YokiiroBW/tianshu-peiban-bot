"""Actual Platform HTTP and temporary storage; only Bili and downloads are synthetic.

Run with the existing Platform test interpreter, without installing dependencies:
  python -B tests/integration/test_media_storage_joint.py --platform <checkout>
    --contracts <LF contracts/text-dialogue/v1> --ffmpeg <executable>
    --ffprobe <executable> --runtime <owned local runtime directory>

Reuse the existing login, recorded Bilibili HTTP and FFmpeg fixtures. The actual
Media lifecycle, job worker, renderer, repository and local publisher run here.
No AssetLibrary fixture, NAS mount, paid upstream or production configuration.
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree


def configure_sources():
    if __name__ != "__main__" and "TS_MEDIA_PLATFORM" not in os.environ:
        raise unittest.SkipTest(
            "Run the media joint with explicit isolated source paths"
        )
    if __name__ == "__main__":
        parser = argparse.ArgumentParser(description=__doc__)
        for name in ("platform", "contracts", "ffmpeg", "ffprobe", "runtime"):
            parser.add_argument("--" + name, type=Path, required=True)
        for name, path in vars(parser.parse_args()).items():
            os.environ["TS_MEDIA_" + name.upper()] = str(path.resolve())
    paths = {
        name: Path(os.environ["TS_MEDIA_" + name.upper()]).resolve()
        for name in ("platform", "contracts", "ffmpeg", "ffprobe", "runtime")
    }
    paths["runtime"].mkdir(parents=True, exist_ok=True)
    os.environ["TS012_CONTRACT_DIR"] = str(paths["contracts"])
    sys.path[:0] = [str(paths["platform"]), str(paths["platform"] / "tests/backend")]
    return paths


PATHS = configure_sources()

import aiohttp  # noqa: E402

from fixtures import ENV, start_http  # noqa: E402
from media_runtime_fixtures import (  # noqa: E402
    BV,
    SyntheticEngine,
    Upstream,
    settings,
    subscription,
)
from services.platform.media.bilibili import Bilibili  # noqa: E402
from services.platform.server import create_app  # noqa: E402
from services.platform.service import Platform  # noqa: E402
from web_fixtures import PASSWORD  # noqa: E402


class MediaStorageJoint(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="media-joint-", dir=PATHS["runtime"]
        )
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.static = self.directory / "static"
        self.static.mkdir()
        (self.static / "index.html").write_text(
            "<!doctype html><title>Media joint</title>"
        )
        self.root = self.directory / "mounted-local-storage"
        self.root.mkdir()
        self.environment = patch.dict(os.environ, ENV)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.upstream = await Upstream().start()
        self.addAsyncCleanup(self.upstream.runner.cleanup)
        self.runner = self.platform = self.client = None
        self.downloads = []
        self.addAsyncCleanup(self.shutdown)
        await self.boot()

    async def boot(self):
        config = settings(
            self.directory,
            {
                "python": sys.executable,
                "ffmpeg": str(PATHS["ffmpeg"]),
                "ffprobe": str(PATHS["ffprobe"]),
            },
            "http://127.0.0.1:1",
            static=self.static,
        )
        # This scenario proves an installation with no AssetLibrary registration.
        config["media"]["targets"] = []
        self.platform = Platform(config)
        self.platform.media.connector = Bilibili(
            api=self.upstream.url, passport=self.upstream.url
        )
        self.engine = SyntheticEngine(self.platform.media.config["engine"])
        self.platform.media.engine = self.engine
        self.runner, self.url = await start_http(create_app(self.platform))
        config["web"]["origin"] = self.url
        self.client = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        async with self.client.get(self.url + "/api/web/session") as response:
            csrf = (await response.json())["csrf"]
        async with self.client.post(
            self.url + "/api/web/login",
            json={"username": "synthetic-admin", "password": PASSWORD},
            headers={"Origin": self.url, "X-CSRF-Token": csrf},
        ) as response:
            value = await response.json()
            self.assertEqual(response.status, 200, value)
            self.csrf = value["csrf"]

    async def shutdown(self):
        if self.client is not None:
            await self.client.close()
            self.client = None
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None
        if self.platform is not None:
            self.downloads.extend(self.engine.calls)
            self.platform.close()
            self.platform = None

    async def restart(self):
        await self.shutdown()
        await self.boot()

    async def post(self, operation, body, expected=200):
        async with self.client.post(
            self.url + "/api/web/media/" + operation,
            json=body,
            headers={"Origin": self.url, "X-CSRF-Token": self.csrf},
        ) as response:
            value = await response.json()
            self.assertEqual(response.status, expected, value)
            return value

    async def view(self):
        return await self.post("view", {})

    async def scan(self, identity, *, recompute=False):
        body = {
            "subscription_id": identity,
            "recompute": recompute,
            "client_id": str(uuid.uuid4()),
        }
        return await self.post("subscriptions/scan", body)

    async def published(self, count, *, timeout=25):
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            view = await self.view()
            jobs = view["jobs"]
            self.assertFalse(
                any(
                    job["state"]
                    in {"failed", "unknown", "waiting_metadata", "auth_required"}
                    for job in jobs
                ),
                jobs,
            )
            if len(jobs) == count and all(job["state"] == "published" for job in jobs):
                return jobs
            self.assertLess(asyncio.get_running_loop().time(), deadline, jobs)
            await asyncio.sleep(0.05)

    def verify_tree(self, job, media_directory, cids):
        receipt = job["asset_receipt"]
        self.assertIsNotNone(receipt)
        target = media_directory / receipt["target_relative_path"]
        self.assertTrue(target.is_dir(), target)
        owner = json.loads((target / ".tianshu-owner.json").read_text(encoding="utf-8"))
        self.assertIsInstance(owner, dict)
        self.assertTrue(owner)
        records = {record["path"]: record for record in receipt["files"]}
        observed = {
            str(path.relative_to(target).as_posix())
            for path in target.rglob("*")
            if path.is_file() and path != target / ".tianshu-owner.json"
        }
        self.assertEqual(observed, set(records))
        for relative, record in records.items():
            raw = (target / relative).read_bytes()
            self.assertEqual(len(raw), record["size_bytes"], relative)
            self.assertEqual(
                hashlib.sha256(raw).hexdigest(), record["sha256"], relative
            )
        videos = {
            record["cid"]: record
            for record in records.values()
            if record["kind"] == "video"
        }
        self.assertEqual(set(videos), set(cids))
        source = json.loads((target / "source.json").read_text(encoding="utf-8"))
        self.assertEqual(source["selected_cids"], cids)
        self.assertEqual(
            dict(source["episode_numbers"]),
            {cid: index + 1 for index, cid in enumerate(cids)},
        )
        self.assertEqual(
            ElementTree.parse(target / "tvshow.nfo").getroot().tag, "tvshow"
        )
        for cid, record in videos.items():
            nfo = ElementTree.parse(
                (target / record["path"]).with_suffix(".nfo")
            ).getroot()
            self.assertEqual(nfo.tag, "episodedetails")
            self.assertEqual(int(nfo.findtext("episode")), cids.index(cid) + 1)
        return target, videos

    async def register_destination(self):
        storage_body = {
            "storage_id": None,
            "expected_revision": 0,
            "label": "临时已挂载本地存储",
            "kind": "local",
            "root_path": str(self.root),
            "client_id": str(uuid.uuid4()),
        }
        storage = (await self.post("storages/save", storage_body))["storage"]
        self.assertEqual(storage["state"], "available")
        self.assertGreater(storage["capacity"]["free_bytes"], 0)
        await self.post(
            "storages/mkdir",
            {
                "storage_id": storage["storage_id"],
                "path": "网络视频",
                "expected_revision": storage["revision"],
                "client_id": str(uuid.uuid4()),
            },
        )
        browse = await self.post(
            "storages/browse",
            {
                "storage_id": storage["storage_id"],
                "path": "",
                "limit": 100,
                "after": None,
            },
        )
        self.assertTrue(
            any(
                entry["path"] == "网络视频" and entry["selectable"]
                for entry in browse["entries"]
            )
        )
        directory_body = {
            "directory_id": None,
            "expected_revision": 0,
            "storage_id": storage["storage_id"],
            "label": "网络视频成品",
            "path": "网络视频",
            "purpose": "both",
            "client_id": str(uuid.uuid4()),
        }
        directory = (await self.post("directories/save", directory_body))["directory"]
        self.assertTrue(directory["target_id"].startswith("directory:"))
        return directory, directory_body

    async def test_monitor_directory_publish_restart_and_new_part(self):
        directory, directory_body = await self.register_destination()
        body = {**subscription(), "mode": "monitor", "target_id": None}
        saved = (await self.post("subscriptions/save", body))["subscription"]
        identity = saved["subscription_id"]
        monitor = await self.scan(identity)
        self.assertEqual(monitor["queued"], 0)
        self.assertEqual(monitor["subscription"]["counts"]["members"], 1)
        self.assertEqual((await self.view())["jobs"], [])
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(list((self.root / "网络视频").iterdir()), [])

        body.update(
            subscription_id=identity,
            expected_revision=monitor["subscription"]["revision"],
            mode="download",
            target_id=directory["target_id"],
            download_directory_id=directory["directory_id"],
            client_id=str(uuid.uuid4()),
        )
        enabled = (await self.post("subscriptions/save", body))["subscription"]
        self.assertEqual(enabled["mode"], "download")
        self.assertEqual((await self.scan(identity))["queued"], 0)
        self.assertEqual((await self.scan(identity, recompute=True))["queued"], 1)
        jobs = await self.published(1)
        original = jobs[0]
        cids = list(self.upstream.parts[BV])
        target, before = self.verify_tree(original, self.root / "网络视频", cids)
        self.assertCountEqual(self.engine.calls, cids)

        await self.restart()
        replay = await self.post("directories/save", directory_body)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["directory"]["directory_id"], directory["directory_id"])
        self.assertEqual((await self.scan(identity, recompute=True))["queued"], 0)
        persisted = (await self.published(1))[0]
        self.assertEqual(persisted["asset_receipt"], original["asset_receipt"])
        self.assertEqual(self.engine.calls, [])

        renamed = await self.post(
            "directories/save",
            {
                **directory_body,
                "directory_id": directory["directory_id"],
                "expected_revision": directory["revision"],
                "label": "网络视频成品（改标签）",
                "client_id": str(uuid.uuid4()),
            },
        )
        self.assertEqual(renamed["directory"]["target_id"], directory["target_id"])

        added = "444444444"
        self.upstream.parts[BV].append(added)
        self.assertEqual((await self.scan(identity))["queued"], 1)
        jobs = await self.published(2)
        latest = next(job for job in jobs if job["job_id"] != original["job_id"])
        final, after = self.verify_tree(latest, self.root / "网络视频", cids + [added])
        self.assertEqual(final, target)
        self.assertEqual(self.engine.calls, [added])
        for cid in cids:
            self.assertEqual(before[cid]["path"], after[cid]["path"])
            self.assertEqual(before[cid]["sha256"], after[cid]["sha256"])
        self.assertEqual((await self.scan(identity, recompute=True))["queued"], 0)
        self.assertEqual(len((await self.view())["jobs"]), 2)
        print(
            json.dumps(
                {
                    "scenario": "monitor_directory_publish_restart_new_part",
                    "actual_platform_http": True,
                    "actual_local_publisher": True,
                    "synthetic_bilibili_and_ffmpeg_download": True,
                    "monitor_jobs": 0,
                    "published_versions": 2,
                    "final_cids": cids + [added],
                    "restart_receipt_preserved": True,
                    "same_directory_append": True,
                    "label_edit_preserves_owned_base": True,
                    "old_videos_unchanged": True,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )

    async def test_published_files_before_worker_receipt_survive_restart(self):
        directory, _ = await self.register_destination()
        committed = asyncio.Event()
        operation = None
        original_publish = self.platform.media.local_publisher.publish

        async def withhold_worker_response(target, prepared):
            nonlocal operation
            # The real publisher commits its journal and actual files. Hold only
            # its return to the worker, leaving the worker's last receipt stale.
            operation = await original_publish(target, prepared)
            committed.set()
            await asyncio.Event().wait()

        self.platform.media.local_publisher.publish = withhold_worker_response
        body = {
            **subscription(),
            "mode": "download",
            "target_id": directory["target_id"],
            "download_directory_id": directory["directory_id"],
        }
        saved = (await self.post("subscriptions/save", body))["subscription"]
        self.assertEqual((await self.scan(saved["subscription_id"]))["queued"], 1)
        await asyncio.wait_for(committed.wait(), 25)
        self.assertEqual(operation["status"], "indexed")
        pending = (await self.view())["jobs"][0]
        self.assertEqual(pending["state"], "publishing")
        self.assertIsNone(pending["asset_receipt"])
        cids = list(self.upstream.parts[BV])
        self.verify_tree(
            {"asset_receipt": operation["receipt"]}, self.root / "网络视频", cids
        )

        await self.restart()
        # Preserve the normal thirty-second worker retry instead of changing a
        # production clock, lease or database row to make this test faster.
        recovered = (await self.published(1, timeout=45))[0]
        self.assertEqual(recovered["job_id"], pending["job_id"])
        self.assertEqual(recovered["asset_receipt"], operation["receipt"])
        self.assertCountEqual(self.downloads, cids)
        self.assertEqual(self.engine.calls, [])
        self.verify_tree(recovered, self.root / "网络视频", cids)
        self.assertEqual(
            (await self.scan(saved["subscription_id"], recompute=True))["queued"], 0
        )
        print(
            json.dumps(
                {
                    "scenario": "published_files_before_worker_receipt_restart",
                    "actual_platform_http": True,
                    "actual_local_publisher": True,
                    "real_service_lifecycle_interruption": True,
                    "same_operation_receipt": True,
                    "no_redownload": True,
                    "normal_retry_lease_preserved": True,
                },
                sort_keys=True,
            ),
            flush=True,
        )


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
