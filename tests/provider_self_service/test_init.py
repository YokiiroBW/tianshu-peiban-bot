"""First install is explicit and a damaged backup cannot be silently replaced."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PRODUCT = Path(os.environ["TIANSHU_WORKSPACE"]) / "worktrees/PROVIDER-P1/tianshu-platform"
sys.path.insert(0, str(PRODUCT))

from services.platform.contracts import Fault  # noqa: E402
from services.platform.provider_catalog import ProviderCatalog  # noqa: E402


class InitializationTests(unittest.TestCase):
    def test_cli_init_is_once_only_and_preflight_detects_damage(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            directory = root / "private"
            settings = root / "settings.json"
            settings.write_text(
                json.dumps({
                    "provider_self_service": {
                        "directory": str(directory),
                        "gateway_url": "http://127.0.0.1:9",
                        "gateway_token_env": "TEST_PROVIDER_GATEWAY_TOKEN",
                    }
                }),
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["TEST_PROVIDER_GATEWAY_TOKEN"] = "isolated-fixture-token-1234567890"
            env["PYTHONPATH"] = str(PRODUCT)
            command = [sys.executable, "-m", "services.platform", "--settings", str(settings), "providers-init"]
            first = subprocess.run(command, cwd=PRODUCT, env=env, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertEqual(json.loads(first.stdout), {"initialized": True})
            ProviderCatalog.verify_existing(directory)
            before = (directory / "providers.key").read_bytes()
            again = subprocess.run(command, cwd=PRODUCT, env=env, capture_output=True, text=True)
            self.assertEqual(again.returncode, 1)
            self.assertEqual((directory / "providers.key").read_bytes(), before)
            (directory / "providers.key").write_bytes(b"0" * len(before))
            with self.assertRaises(Fault):
                ProviderCatalog.verify_existing(directory)


if __name__ == "__main__":
    unittest.main()
