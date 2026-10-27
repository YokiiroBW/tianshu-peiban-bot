import json
import tempfile
import unittest
from pathlib import Path

from deploy.single_container.frontend import (
    digest,
    inventory,
    package_frontend,
    rows_hash,
)


class FrontendBundleTests(unittest.TestCase):
    def fixture(self, temporary):
        root = Path(temporary)
        source, bundle = root / "source", root / "dist"
        (source / "apps/web/src").mkdir(parents=True)
        (source / "apps/web/src/Flow.tsx").write_text("export const Flow = 1\n")
        for name in ("package.json", "package-lock.json"):
            (source / name).write_text("{}\n")
        bundle.mkdir()
        (bundle / "index.html").write_text('<script src="flow.js"></script>')
        (bundle / "flow.js").write_text('console.log("two graph views");')
        inputs = inventory(source / "apps/web", prefix="apps/web/")
        inputs.extend(
            {
                "path": name,
                "size": (source / name).stat().st_size,
                "sha256": digest((source / name).read_bytes()),
            }
            for name in ("package.json", "package-lock.json")
        )
        inputs.sort(key=lambda row: row["path"])
        files = inventory(bundle)
        manifest = {
            "schema_version": "1.0.0",
            "source_clean": True,
            "source_commit": "a" * 40,
            "node_version": "v24.19.0",
            "npm_version": "11.6.2",
            "inputs": inputs,
            "files": files,
            "inputs_sha256": rows_hash(inputs),
            "files_sha256": rows_hash(files),
            "lock_sha256": digest((source / "package-lock.json").read_bytes()),
        }
        path = root / "bundle.json"
        path.write_text(json.dumps(manifest))
        return source, bundle, path, root / "output"

    def test_backend_integration_can_change_commit_with_identical_frontend_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, bundle, manifest, output = self.fixture(temporary)
            (source / "backend.py").write_text("# independently integrated backend\n")
            receipt = package_frontend(bundle, manifest, source, output, "b" * 40)
            self.assertTrue(receipt["source_inputs_match"])
            self.assertEqual(receipt["producer_source_commit"], "a" * 40)
            self.assertEqual(receipt["release_platform_commit"], "b" * 40)
            self.assertEqual(inventory(bundle), inventory(output))
            self.assertEqual(receipt["target"], "/srv/tianshu/web")

    def test_changed_source_or_stale_bundle_is_rejected_before_copy(self):
        for changed in ("source", "bundle", "missing-input", "unlisted-input"):
            with (
                self.subTest(changed=changed),
                tempfile.TemporaryDirectory() as temporary,
            ):
                source, bundle, manifest, output = self.fixture(temporary)
                if changed == "source":
                    (source / "apps/web/src/Flow.tsx").write_text(
                        "export const Flow = 2\n"
                    )
                elif changed == "bundle":
                    (bundle / "flow.js").write_text("stale bytes")
                elif changed == "missing-input":
                    (source / "apps/web/src/Flow.tsx").unlink()
                else:
                    (source / "apps/web/src/Extra.tsx").write_text(
                        "export const Extra = 1\n"
                    )
                with self.assertRaises(ValueError):
                    package_frontend(bundle, manifest, source, output, "b" * 40)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
