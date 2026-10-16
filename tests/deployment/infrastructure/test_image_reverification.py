"""Archive semantics and verify-only orchestration; no real Docker or SSH."""

import copy
import gzip
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from deploy.infrastructure import image_controller as controller


class MusicArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)

    def archive(self, name, *, private=False, changed=None):
        members = [
            {"name": "tools", "type": tarfile.DIRTYPE, "mode": 0o755, "content": None},
            {"name": "tools/source.py", "type": tarfile.REGTYPE, "mode": 0o644, "content": b"source\n"},
            {"name": "tools/run.sh", "type": tarfile.REGTYPE, "mode": 0o755, "content": b"#!/bin/sh\n"},
            {"name": "tools/link", "type": tarfile.SYMTYPE, "mode": 0o777, "link": "run.sh", "content": None},
            {"name": "tools/hardlink", "type": tarfile.LNKTYPE, "mode": 0o755, "link": "tools/run.sh", "content": None},
        ]
        if private:
            for member in members:
                if member["type"] in (tarfile.DIRTYPE, tarfile.REGTYPE):
                    member["mode"] &= 0o700
            members.reverse()
        if changed:
            identity, values = changed
            next(member for member in members if member["name"] == identity).update(values)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:") as archive:
            for values in members:
                member = tarfile.TarInfo(values["name"])
                member.type, member.mode = values["type"], values["mode"]
                member.linkname = values.get("link", "")
                member.mtime = 200 if private else 100
                member.uid, member.gid = (10, 20) if private else (0, 0)
                member.uname = "private" if private else "root"
                raw = values["content"]
                member.size = len(raw) if raw is not None else 0
                archive.addfile(member, io.BytesIO(raw) if raw is not None else None)
        path = self.base / name
        path.write_bytes(gzip.compress(buffer.getvalue(), mtime=300 if private else 100))
        return path

    def test_gzip_times_metadata_order_and_private_modes_preserve_source_semantics(self):
        old, new = self.archive("old.tar.gz"), self.archive("new.tar.gz", private=True)
        self.assertNotEqual(old.read_bytes(), new.read_bytes())
        self.assertEqual(controller.archive_fingerprint(old), controller.archive_fingerprint(new))
        self.assertEqual(controller.archive_fingerprint(new)["members"], 5)
        compile(controller.MUSIC_PROBE, "music-probe", "exec")

    def test_content_paths_kinds_links_and_executable_semantics_remain_exact(self):
        before = controller.archive_fingerprint(self.archive("before.tar.gz"))
        for index, (identity, values) in enumerate((
            ("tools/source.py", {"content": b"changed\n"}),
            ("tools/source.py", {"name": "tools/renamed.py"}),
            ("tools/link", {"type": tarfile.LNKTYPE}),
            ("tools/link", {"link": "other.sh"}),
            ("tools/hardlink", {"link": "tools/other.sh"}),
            ("tools/run.sh", {"mode": 0o644}),
            ("tools", {"mode": 0o644}),
        )):
            with self.subTest(identity=identity, values=values):
                after = self.archive(str(index) + ".tar.gz", changed=(identity, values))
                self.assertNotEqual(before, controller.archive_fingerprint(after))


class ImageReverificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.stage = Path(temporary.name).resolve()
        source = self.stage / "synthetic-source.py"
        source.write_bytes(b"fixed source\n")
        self.base_id, self.image_id = "sha256:" + "a" * 64, "sha256:" + "b" * 64
        self.config = {
            "stage": str(self.stage), "docker": "/synthetic/docker", "base_image": self.base_id,
            "base_revision": "1" * 40, "revision": "2" * 40, "contracts_revision": "3" * 40,
            "node_commit": "4" * 40, "node_package_sha256": "5" * 64, "tag": "synthetic:candidate",
            "inherited_image_fields": ["User", "Entrypoint", "Cmd", "Env", "WorkingDir", "Healthcheck", "Volumes", "ExposedPorts", "StopSignal"],
            "source_hashes": {source.name: hashlib.sha256(source.read_bytes()).hexdigest()},
        }
        (self.stage / "build-inputs.json").write_text(json.dumps(self.config), encoding="utf-8")
        image_config = {"User": "10001:10001", "Entrypoint": ["python"], "Cmd": ["-m", "synthetic"], "Env": ["PATH=/synthetic/venv/bin"], "WorkingDir": "/srv/synthetic", "Healthcheck": {"Test": ["CMD", "health"]}}
        self.base = {"Id": self.base_id, "Architecture": "amd64", "Config": copy.deepcopy(image_config)}
        self.base["Config"]["Labels"] = {"org.opencontainers.image.revision": self.config["base_revision"]}
        self.candidate = {"Id": self.image_id, "Architecture": "amd64", "Config": copy.deepcopy(image_config)}
        self.candidate["Config"]["Labels"] = controller.candidate_labels(self.config)
        self.music = {"binaries": {"bridge": "6" * 64, "decode": "7" * 64}, "source_archive": {"members": 2135, "content_sha256": "8" * 64}, "ffmpeg_sha256": "9" * 64, "ffmpeg": "ffmpeg version synthetic"}
        self.candidate_music = copy.deepcopy(self.music)
        self.linux = {"uid": 10001, "yaml": "6.0.3", "source_hashes_verified": True, "main_venv_preserved": True, "independent_node_venv": True}
        self.prior = {"build-result.json": b'{"status":"failed","reason":"music_runtime_changed"}\n', "controller-error.log": b"original failure evidence\n", "build-controller.py": b"original controller\n", "build.log": b"original build log\n"}
        for name, raw in self.prior.items():
            (self.stage / name).write_bytes(raw)
        self.image_calls = []

    def command(self, arguments, **kwargs):
        self.assertEqual(arguments[:3], [self.config["docker"], "image", "inspect"])
        reference = arguments[3]
        self.assertIn(reference, (self.base_id, self.image_id))
        return SimpleNamespace(stdout=json.dumps([self.base if reference == self.base_id else self.candidate]))

    def in_image(self, config, reference, program, **kwargs):
        self.assertEqual(config, self.config)
        self.image_calls.append((reference, program))
        if program == controller.MUSIC_PROBE:
            value = self.music if reference == self.base_id else self.candidate_music
        else:
            self.assertEqual(reference, self.image_id)
            self.assertEqual(program, "CONFIG=" + repr(self.config) + "\n" + controller.LINUX_CHECKS)
            compile(program, "installed-linux-checks", "exec")
            value = self.linux
        return SimpleNamespace(stdout=json.dumps(value))

    def invoke(self, receipt="reverified.json", *, existing=True):
        with patch.object(controller, "command", side_effect=self.command), patch.object(controller, "in_image", side_effect=self.in_image), patch.object(controller.subprocess, "run", side_effect=AssertionError("no Docker build permitted")), patch.object(controller, "build_candidate", return_value=self.image_id) as build:
            result = controller.run(self.stage, verify_image=self.image_id if existing else None, receipt_name=receipt if existing else None)
            self.assertEqual(build.call_count, 0 if existing else 1)
            return result

    def assert_prior_preserved(self):
        for name, raw in self.prior.items():
            self.assertEqual((self.stage / name).read_bytes(), raw)

    def test_verify_only_runs_all_shared_checks_on_exact_id_and_keeps_original_evidence(self):
        result = self.invoke()
        self.assertEqual(result["status"], "built")
        self.assertEqual(result["image_id"], self.image_id)
        self.assertTrue(result["verification_only"])
        self.assertFalse(result["image_rebuilt"])
        self.assertEqual(result["linux_checks"], self.linux)
        self.assertEqual([reference for reference, _ in self.image_calls], [self.base_id, self.image_id, self.image_id])
        self.assertEqual(result["prior_evidence_sha256"], {name: hashlib.sha256(raw).hexdigest() for name, raw in self.prior.items()})
        self.assertEqual(json.loads((self.stage / "reverified.json").read_bytes()), result)
        self.assert_prior_preserved()
        with patch.object(controller, "command", side_effect=AssertionError("existing evidence must refuse before Docker")):
            with self.assertRaisesRegex(AssertionError, "build_receipt_exists"):
                controller.run(self.stage, verify_image=self.image_id, receipt_name="reverified.json")
        self.assert_prior_preserved()

    def test_binary_ffmpeg_and_source_changes_fail_into_separate_receipts(self):
        for index, key in enumerate(("binaries", "ffmpeg_sha256", "ffmpeg", "source_archive")):
            with self.subTest(changed=key):
                self.candidate_music = copy.deepcopy(self.music)
                self.candidate_music[key] = "different"
                receipt = "changed-" + str(index) + ".json"
                with self.assertRaisesRegex(AssertionError, "music_runtime_changed"):
                    self.invoke(receipt)
                failed = json.loads((self.stage / receipt).read_bytes())
                self.assertEqual(failed["status"], "failed")
                self.assertFalse(failed["image_rebuilt"])
                self.assert_prior_preserved()

    def test_shared_input_and_inherited_config_checks_still_fail(self):
        self.candidate["Config"]["Env"] = ["PATH=/different"]
        with self.assertRaisesRegex(AssertionError, "inherited_image_config_changed"):
            self.invoke("changed-config.json")
        (self.stage / "synthetic-source.py").write_bytes(b"changed source\n")
        with self.assertRaisesRegex(AssertionError, "build_source_changed"):
            self.invoke("changed-source.json")
        self.assert_prior_preserved()

    def test_original_build_path_reuses_same_complete_verification(self):
        for name in self.prior:
            (self.stage / name).unlink()
        result = self.invoke(existing=False)
        self.assertFalse(result["verification_only"])
        self.assertTrue(result["image_rebuilt"])
        self.assertEqual(result["linux_checks"], self.linux)
        self.assertEqual([reference for reference, _ in self.image_calls], [self.base_id, self.image_id, self.image_id])
        self.assertEqual(json.loads((self.stage / "build-result.json").read_bytes()), result)

    def test_verify_requires_immutable_image_and_new_stage_local_receipt(self):
        for image, receipt in (("mutable:tag", "new.json"), (self.image_id, None), (self.image_id, "../outside.json"), (self.image_id, "build-result.json"), (self.image_id, "build-inputs.json")):
            with self.subTest(image=image, receipt=receipt), patch.object(controller, "command", side_effect=AssertionError("invalid input must not reach Docker")):
                with self.assertRaises(AssertionError):
                    controller.run(self.stage, verify_image=image, receipt_name=receipt)
        self.assert_prior_preserved()


if __name__ == "__main__":
    unittest.main()
