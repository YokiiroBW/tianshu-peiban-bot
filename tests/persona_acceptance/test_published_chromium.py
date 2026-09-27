"""Test-only published-mode acceptance across the fixed Companion, Platform, and web build.

This test copies the formal v1 package into a disposable directory and applies the publication
metadata only to that copy. It never edits or publishes the coordination contract. The browser
uses the exact B and U archives and talks only to the real HTTPS Platform, which in turn reads the
current fixed Companion producer over HTTPS.
"""

import asyncio
import hashlib
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


WORKSPACE = Path(__file__).resolve().parents[2]
B_ROOT = Path(
    os.environ.get(
        "CONNECT_B_SNAPSHOT", WORKSPACE / ".runtime/connect-a-b-9551871/checkout"
    )
).resolve()
U_ROOT = Path(
    os.environ.get(
        "CONNECT_U_SNAPSHOT", WORKSPACE / ".runtime/connect-a-u-faa5c6/checkout"
    )
).resolve()
VENV = Path(os.environ.get("CONNECT_A_TEST_VENV", WORKSPACE / ".runtime/connect-a-b-env/venv"))
PYTHON = VENV / "Scripts/python.exe"
NODE = Path(
    os.environ.get(
        "CONNECT_A_NODE",
        "C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe",
    )
)
COMPANION_REPO = Path(
    os.environ.get("TS025_COMPANION_REPO", "C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-companion")
).resolve()
CONTRACT_WORKSPACE = Path(os.environ.get("TS012_CONTRACT_DIR", "C:/YOKI/Codex/tianshu-peiban-bot/contracts/text-dialogue/v1")).resolve()
PERSONA_PACKAGE = Path(
    os.environ.get(
        "CONNECT_PERSONA_V1",
        "C:/YOKI/Codex/tianshu-peiban-bot/contracts/persona-management/v1",
    )
).resolve()
PRODUCER_COMMIT = "31677983798ba27b24d57925feab4774c2eec30f"
PERSONA_TOKEN_ENV = "TS025_JOINT_PERSONA"
PERSONA_TOKEN = "synthetic-ts025-joint-persona-credential"
CONNECTION = "characters-current-producer"


for required in (
    B_ROOT / "tests/backend/personas_fixture.py",
    U_ROOT / "apps/web/dist/index.html",
    PYTHON,
    NODE,
    COMPANION_REPO / ".git",
    PERSONA_PACKAGE / "manifest.json",
):
    if not required.exists():
        raise unittest.SkipTest(f"published persona browser acceptance prerequisite missing: {required}")

site_packages = VENV / "Lib/site-packages"
if not (site_packages / "fastapi").is_dir() or not (site_packages / "uvicorn").is_dir():
    raise unittest.SkipTest(f"fixed Companion dependencies missing under {site_packages}")

os.environ["TS012_CONTRACT_DIR"] = str(CONTRACT_WORKSPACE)
os.environ["TS013_TLS_PYTHON"] = str(PYTHON)
os.environ["TS025_COMPANION_REPO"] = str(COMPANION_REPO)
os.environ["TS025_COMPANION_DEPS"] = str(site_packages)
for path in (str(B_ROOT / "tests/backend"), str(B_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import personas_fixture as companion_fixture  # noqa: E402
from aiohttp import web  # noqa: E402
from fixtures import ENV  # noqa: E402
from services.platform import persona_page_config  # noqa: E402
from services.platform.server import create_app  # noqa: E402
from services.platform.service import Platform  # noqa: E402
from web_fixtures import PASSWORD, web_settings  # noqa: E402


def make_published_copy(source: Path, destination: Path) -> str:
    """Make a disposable test fixture that has the exact shape the published loader pins."""
    shutil.copytree(source, destination)
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "published"
    manifest["production_publish_authorized"] = True
    manifest["joint_runtime_acceptance"] = "passed"
    # Recompute only the copy's file hashes; the four contract files are copied byte for byte.
    manifest["sha256"] = {
        name: hashlib.sha256((destination / name).read_bytes()).hexdigest()
        for name in ("examples.json", "README.md", "request.schema.json", "response.schema.json")
    }
    raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    manifest_path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


class PublishedPersonaBrowserAcceptance(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="persona-current-browser-", dir=WORKSPACE / ".runtime")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / "published-package-test-copy"
        self.package_hash = make_published_copy(PERSONA_PACKAGE, self.package)

        # The fixture module provides isolation helpers only. Pin it to the exact producer commit
        # requested by the current release candidate and give its export a per-run runtime.
        companion_fixture.COMPANION_COMMIT = PRODUCER_COMMIT
        companion_fixture._workspace = lambda: (CONTRACT_WORKSPACE.parents[2], "CONNECT-A")
        companion_fixture.RUNTIME = self.root / "companion-runtime"
        companion_fixture.SOURCES = companion_fixture.RUNTIME / "sources"
        companion_fixture.SOURCES.mkdir(parents=True)
        self.env_patch = mock.patch.dict(
            os.environ,
            {
                **ENV,
                PERSONA_TOKEN_ENV: PERSONA_TOKEN,
                "TS012_CONTRACT_DIR": str(CONTRACT_WORKSPACE),
                "TS013_TLS_PYTHON": str(PYTHON),
                "TS025_COMPANION_REPO": str(COMPANION_REPO),
                "TS025_COMPANION_DEPS": str(site_packages),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

        try:
            companion_fixture.companion_source()
        except companion_fixture.Unavailable as reason:
            self.skipTest(f"fixed Companion unavailable: {reason}")

        tls_dir = self.root / "tls"
        tls_dir.mkdir()
        subprocess.run(
            [str(PYTHON), str(B_ROOT / "tests/backend/make_tls_fixture.py"), str(tls_dir)],
            check=True,
            capture_output=True,
            timeout=60,
        )
        self.ca = tls_dir / "localhost.pem"
        self.key = tls_dir / "localhost-key.pem"
        self.core_config_path = self.root / "companion.json"
        self.core_config_path.write_text(
            json.dumps(
                companion_fixture.core_config(
                    self.root,
                    PERSONA_TOKEN_ENV,
                    {
                        "actor:alpha": {
                            "version": 1,
                            "persona": "甲：初始人格",
                            "tone": "平静",
                            "style": "简短",
                            "address": "你",
                        },
                        "actor:beta": {"version": 2, "persona": "乙：另一位角色"},
                        "actor:hidden": {"version": 1, "persona": "不应进入页面的角色"},
                    },
                ),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        os.environ["TIANSHU_COMPANION_CONFIG"] = str(self.core_config_path)
        from tianshu_companion.app import create_app as create_companion_app

        self.core = await companion_fixture.CoreServer.start(
            create_companion_app(), self.ca, self.key
        )
        self.addAsyncCleanup(self.core.close)
        await self.seed_real_companion()

        self.platform_port = companion_fixture.free_port()
        self.origin = f"https://127.0.0.1:{self.platform_port}"
        settings = web_settings(
            self.root / "platform",
            origin=self.origin,
            static=U_ROOT / "apps/web/dist",
        )
        settings["mode"] = "service_https"
        settings["principals"]["admin"]["actions"].append("persona.read")
        settings["persona_connections"] = {
            CONNECTION: {
                "base_url": self.core.url,
                "token_env": PERSONA_TOKEN_ENV,
                "ca_file": str(self.ca),
            }
        }
        settings["web_personas"] = {
            "enabled": True,
            "connection_id": CONNECTION,
            "allowed_subjects": ["actor:alpha", "actor:beta"],
            "published_directory": str(self.package),
        }

        with mock.patch.object(
            persona_page_config, "PUBLISHED_MANIFEST_SHA256", self.package_hash
        ):
            self.platform = Platform(settings)
        self.assertEqual(self.platform.personas["candidate"].manifest["status"], "published")
        self.assertEqual(self.platform.personas["candidate"].manifest_sha256, self.package_hash)

        app = create_app(self.platform)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.ca, self.key)
        site = web.TCPSite(runner, "127.0.0.1", self.platform_port, ssl_context=context)
        await site.start()
        self.addAsyncCleanup(self.platform.close)

    async def seed_real_companion(self):
        """Write synthetic history only through the current producer's real operator API."""

        async def call(document):
            return await companion_fixture.call_core(
                self.core.url, self.ca, PERSONA_TOKEN, document
            )

        initial = (await call({"operation": "get", "subject": "actor:alpha"}))[
            "persona"
        ]
        drafted = await call(
            {
                "operation": "draft",
                "subject": "actor:alpha",
                "request_id": "connect-a-browser-draft-1",
                "content": {
                    "persona": "甲：已发布人格",
                    "tone": "温和",
                    "style": "详细",
                    "address": "你",
                    "synthetic_extension": "fixture-only",
                },
                "operator": "operator:connect-a",
                "expected": initial["version"],
                "reason": "合成联合验收",
                "note": "真实生产者接口写入的隔离夹具",
            }
        )
        revision = drafted["persona"]["draft_revision"]
        approved = await call(
            {
                "operation": "approve",
                "subject": "actor:alpha",
                "request_id": "connect-a-browser-approve-1",
                "revision_id": revision,
                "operator": "operator:connect-a",
                "expected": drafted["persona"]["version"],
                "reason": "合成批准",
            }
        )
        published = await call(
            {
                "operation": "publish",
                "subject": "actor:alpha",
                "request_id": "connect-a-browser-publish-1",
                "revision_id": revision,
                "operator": "operator:connect-a",
                "expected": approved["persona"]["version"],
                "reason": "合成发布",
            }
        )
        await call(
            {
                "operation": "draft",
                "subject": "actor:alpha",
                "request_id": "connect-a-browser-draft-2",
                "content": {
                    "persona": "甲：当前草稿",
                    "tone": "急促",
                    "style": "短句",
                    "address": "您",
                },
                "operator": "operator:connect-a",
                "expected": published["persona"]["version"],
                "reason": "合成联合验收",
                "note": "保留一份未批准草稿用于只读页面验收",
            }
        )

    async def test_browser_reads_current_producer_through_published_mode(self):
        node_script = WORKSPACE / "tests/persona_acceptance/persona_browser.mjs"
        env = {
            **os.environ,
            "PERSONA_ORIGIN": self.origin,
            "PERSONA_TOKEN": PERSONA_TOKEN,
            "PERSONA_CORE_URL": self.core.url,
            "PERSONA_PACKAGE_PATH": str(self.package),
            "PERSONA_CONNECTION": CONNECTION,
            "PERSONA_CA_PATH": str(self.ca),
            "PERSONA_NODE_MODULE_ANCHOR": str(U_ROOT / "apps/web/tests/personas.spec.ts"),
        }
        process = await asyncio.create_subprocess_exec(
            str(NODE),
            str(node_script),
            cwd=U_ROOT,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=120)
        except TimeoutError:
            process.kill()
            await process.wait()
            self.fail("Chromium persona acceptance exceeded 120 seconds")
        result = output.decode("utf-8", errors="replace")
        self.assertEqual(process.returncode, 0, result)
        self.assertIn("PERSONA_BROWSER_RESULT=", result)
        self.assertNotIn(PERSONA_TOKEN, result)
        self.assertIn('"catalogSubjects":2', result)
        self.assertIn('"comparisonFields":4', result)


if __name__ == "__main__":
    unittest.main()
