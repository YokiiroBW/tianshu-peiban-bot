
import unittest
import json
import shutil
from pathlib import Path
from tianshu.runtime.ui_release import (
    build_release_manifest, validate_release_manifest, 
    verify_release_directory, install_release_directory,
    switch_current_release, BACKEND_UI_CONTRACT
)

class TestUiStaticRelease(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("test_ui_dir")
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        self.test_dir.mkdir()
        self.dist_dir = self.test_dir / "dist"
        self.dist_dir.mkdir()
        (self.dist_dir / "index.html").write_text("<html><body></body></html>", encoding="utf-8")
        (self.dist_dir / "assets").mkdir()
        (self.dist_dir / "assets/main.js").write_text("console.log('test')", encoding="utf-8")
        self.ui_root = self.test_dir / "ui_root"
        self.ui_root.mkdir()
        (self.ui_root / "releases").mkdir()

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_manifest_creation(self):
        manifest = build_release_manifest(self.dist_dir, build_id="ui-20261108T000000Z-a" * 4, source_sha="0"*40)
        self.assertEqual(manifest["build_id"], "ui-20261108T000000Z-aaaaaaaaaaaaaaaa")
        self.assertIn("index.html", manifest["files"])
        self.assertIn("assets/main.js", manifest["files"])
        validate_release_manifest(manifest)

    def test_install_and_switch(self):
        manifest = build_release_manifest(self.dist_dir, build_id="ui-test-1", source_sha="0"*40)
        with open(self.dist_dir / "release.json", "w") as f:
            json.dump(manifest, f)
            
        install_info = install_release_directory(self.ui_root, self.dist_dir)
        self.assertTrue(install_info["package_installed"])
        
        switch_info = switch_current_release(self.ui_root, "ui-test-1")
        self.assertTrue(switch_info["pointer_switched"])
        self.assertEqual(switch_info["current_build_id"], "ui-test-1")

if __name__ == "__main__":
    unittest.main()
