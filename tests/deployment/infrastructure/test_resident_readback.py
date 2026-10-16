"""Exercise generated completion/rollback adapters without production services."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from deploy.infrastructure.resident_readback import prepare


class ResidentReadbackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.stage = self.base / "original"
        self.stage.mkdir()
        self.origin = self.stage / "resume-existing-origin"
        self.origin.mkdir()
        self.origin_record = {"gateway_id_after": "g" * 64, "same_existing_entry_owner_and_route": True,
                              "original_origin_preserved": True, "new_permissions": 0, "new_service_credentials": 0}
        (self.origin / "gateway-origin-result.json").write_text(json.dumps(self.origin_record), encoding="utf-8")
        self.before = [{"id": "a" * 64, "status": "running", "mounts": [["bind", "/one", "/target-one", True], ["bind", "/two", "/target-two", False]]}]
        self.after = [{**self.before[0], "mounts": list(reversed(self.before[0]["mounts"]))}, {"id": "b" * 64, "status": "exited", "mounts": []}]
        raw = ("import json\nfrom pathlib import Path\nS=Path(" + repr(str(self.stage)) + ")\n"
               + "BEFORE=" + repr(self.before) + "\nAFTER=" + repr(self.after) + "\n" + '''
def require(ok, code):
 if not ok: raise RuntimeError(code)
def read(path): return json.loads(Path(path).read_bytes())
def save(path,value):
 with Path(path).open('x',encoding='utf-8') as stream: json.dump(value,stream)
def atomic_bytes(path,raw): Path(path).write_bytes(raw)
def metadata_inventory(guard, ids): return AFTER
def release(): raise RuntimeError('original release must never execute')
def rollback_release(parent):
 global S
 S=parent/'rollback-test';S.mkdir()
 non_targets=BEFORE
 require(metadata_inventory(None,set())==non_targets,'non_target_container_changed')
 save(S/'result.json',{'status':'rolled_back'})
try:
 release()
except Exception:
 raise
''').encode()
        self.worker = self.stage / "resident-update.py"
        self.worker.write_bytes(raw)
        self.failure = b'{"status":"requires_inspection"}\n'
        (self.stage / "failure.json").write_bytes(self.failure)
        self.output = self.base / "resume-readback.py"
        self.receipt = prepare(stage=str(self.stage), worker_sha256=hashlib.sha256(raw).hexdigest(),
                               image_id="sha256:" + "c" * 64, platform_id="p" * 64, gateway_id="g" * 64,
                               state_dir=str(self.stage / "capacity-state-test"), guard_commit="d" * 40,
                               memory_limit=1073741824, resume_origin_stage=str(self.origin), output=self.output)

    def load(self, path):
        scope = {"__name__": "synthetic_adapter"}
        exec(compile(path.read_bytes(), str(path), "exec"), scope)
        return scope

    def test_completion_loads_only_pinned_definitions_and_never_runs_release_or_overwrites_evidence(self):
        scope = self.load(self.output)
        scope["load_original"]()
        scope["save"](self.stage / "result.json", {"status": "synthetic_complete"})
        self.assertFalse((self.stage / "result.json").exists())
        self.assertEqual(json.loads((self.stage / "resume-result.json").read_bytes())["status"], "synthetic_complete")
        self.assertEqual((self.stage / "failure.json").read_bytes(), self.failure)
        with self.assertRaisesRegex(RuntimeError, "configuration_write_forbidden"):
            scope["atomic_bytes"](self.stage / "production-config", b"must not write")
        self.assertFalse((self.stage / "production-config").exists())

    def test_changed_original_source_is_rejected_before_loading_or_writing(self):
        scope = self.load(self.output)
        self.worker.write_bytes(self.worker.read_bytes() + b"\n# unexpected\n")
        with self.assertRaisesRegex(AssertionError, "original_worker_changed"):
            scope["load_original"]()
        self.assertFalse((self.stage / "resume-result.json").exists())

    def test_new_rollback_wrapper_injects_canonical_subset_comparison_and_records_addition(self):
        path = self.base / "rollback-canonical-metadata.py"
        scope = self.load(path)
        scope["main"]()
        report = json.loads((self.stage / "rollback-test/non-targets-after.json").read_bytes())
        self.assertTrue(report["preserved"])
        self.assertEqual([row["id"] for row in report["added"]], ["b" * 64])
        self.assertEqual((self.stage / "failure.json").read_bytes(), self.failure)
        self.assertFalse((self.stage / "result.json").exists())


if __name__ == "__main__":
    unittest.main()
