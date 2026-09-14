"""Real owner restart and old complete database snapshots; no SQL fabrication of watermarks."""

import asyncio
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from services.platform.service import Platform
from tianshu_companion.contracts import Fault as CoreFault
from tianshu_memory.app import configured_app
from tianshu_memory.domain import Fault as MemoryFault
from ts050_source_support import SourceChain, digest, reserve


class SourceRecoveryChain(SourceChain):
    async def test_core_restart_old_owner_snapshot_and_missing_memory_guard(self):
        original_path = Path(self.core_config["database_path"])
        older = self.directory / "core-before-input.sqlite"
        port = self.core_socket.getsockname()[1]
        await self.core_server.close()
        shutil.copyfile(
            original_path, older
        )  # Closed owner's complete checkpoint, never table edits.
        self.core_socket = reserve(port)
        await self.start_core()
        fanout = await self.submit(self.physical("合成恢复验证咖啡偏好。", "recovery-input"))
        commit = (await self.wait_commits(1))[0]
        admission = fanout["outcomes"][0]["admission"]
        await self.commit_draft(commit, ["上午不喝咖啡。"])
        before = await self.facts(head=True)
        await self.restart_core()
        restarted = await self.facts(head=True)
        self.assertEqual(restarted["head"], before["head"])
        good = await self.select(admission, "咖啡")
        self.assertEqual(good.status_code, 200, good.text)
        self.assertEqual(len(good.json()["selected_units"]), 1)
        restored_path = self.directory / "isolated-old-core.sqlite"
        shutil.copyfile(older, restored_path)
        await self.restart_core(database_path=restored_path)
        rolled_back = await self.facts(head=True)
        self.assertEqual(rolled_back["head"]["generation"], before["head"]["generation"])
        self.assertLess(rolled_back["head"]["sequence"], before["head"]["sequence"])
        failed = await self.select(admission, "咖啡")
        self.assertEqual(failed.status_code, 503, failed.text)
        self.assertNotIn("selected_units", failed.json())
        self.assertEqual(len(self.model_requests), 1)
        guard = Path(self.memory_config["source_sync"]["recovery_path"])
        retained = self.directory / "retained/guard-before-missing.json"
        await self.memory_server.close()
        guard_hash = digest(guard.read_bytes())
        guard.rename(retained)  # Fault injection preserves exact current guard for diagnosis.
        with self.assertRaises(MemoryFault) as refusal:
            configured_app()
        self.assertEqual(refusal.exception.status, 503)
        self.assertFalse(guard.exists(), "Factory must not recreate a missing authority checkpoint")
        self.assertEqual(digest(retained.read_bytes()), guard_hash)
        self.check(
            "normal_Core_restart_then_actual_older_owner_refused",
            before=before["head"],
            normal_restart=restarted["head"],
            restored_owner=rolled_back["head"],
            memory_status=503,
            restored_database="complete closed-owner pre-input copy in a separate test path",
        )
        self.check(
            "missing_Memory_guard_refuses_initialization",
            status=503,
            retained_sha256=guard_hash,
            recreated=False,
            restored_or_unlocked=False,
        )

    async def test_platform_old_allowed_snapshot_cannot_revive_revoked_evidence(self):
        fanout = await self.submit(self.physical("合成撤权恢复咖啡偏好。", "platform-recovery"))
        commit = (await self.wait_commits(1))[0]
        admission = fanout["outcomes"][0]["admission"]
        await self.commit_draft(commit, ["上午不喝咖啡。"])
        allowed = await self.current([admission])
        self.assertEqual(allowed["grants"][0]["state"], "allowed")
        original_path = Path(self.settings["database_path"])
        older = self.directory / "platform-before-revocation.sqlite"
        port = urlsplit(self.platform_url).port
        await self.platform_runner.cleanup()
        shutil.copyfile(original_path, older)
        self.platform = Platform(self.settings)
        app = self.make_platform_app()
        self.platform_url = await self.start_aio(app, port=port)
        self.platform_runner = self.runners[id(app)]
        self.platform.origins.revoke(self.bearer("ADMIN"), "entry", "self_private:actor:a")
        denied = await self.current([admission])
        self.assertEqual(denied["grants"][0]["state"], "denied")
        turn = self.core.store.list("turns")[0]
        with self.assertRaises(CoreFault):
            await self.core.memory.check_sources(turn, commit["request"]["sources"])
        failed = await self.select(admission, "咖啡")
        self.assertEqual(failed.status_code, 403, failed.text)
        old_jobs = await asyncio.to_thread(self.workflow.jobs)
        retained_guard = Path(self.memory_config["source_sync"]["recovery_path"]).read_bytes()
        await self.platform_runner.cleanup()
        restored_path = self.directory / "isolated-old-platform.sqlite"
        shutil.copyfile(older, restored_path)
        self.settings["database_path"] = str(restored_path)
        self.platform = Platform(self.settings)
        app = self.make_platform_app()
        self.platform_url = await self.start_aio(app, port=port)
        self.platform_runner = self.runners[id(app)]
        restored = await self.current([admission])
        self.assertEqual(restored["grants"][0]["state"], "allowed")
        self.assertEqual(restored["head"]["generation"], denied["head"]["generation"])
        self.assertLessEqual(restored["head"]["sequence"], denied["head"]["sequence"])
        failed = await self.select(admission, "咖啡")
        self.assertEqual(failed.status_code, 503, failed.text)
        self.assertNotIn("selected_units", failed.json())
        self.assertEqual(await asyncio.to_thread(self.workflow.jobs), old_jobs)
        self.assertEqual(
            Path(self.memory_config["source_sync"]["recovery_path"]).read_bytes(), retained_guard
        )
        self.check(
            "old_Platform_allowed_cannot_revive_after_real_denial",
            allowed_head=allowed["head"],
            denied_head=denied["head"],
            restored_head=restored["head"],
            restored_grant=restored["grants"][0]["state"],
            memory_status=503,
            source_guard_unchanged=True,
            candidate_jobs=old_jobs,
            restoration="complete closed-owner snapshot at separate path; no head/permission SQL",
        )
