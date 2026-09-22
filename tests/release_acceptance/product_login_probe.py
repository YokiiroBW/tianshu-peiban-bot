"""Optional pinned Platform-only HTTPS compatibility probe; never product initialization.

Uses the exported product's explicit synthetic test settings. No Core, Memory,
Gateway, real model, browser rendering, or deployability claim is made.
"""

import argparse
import asyncio
import importlib.util
import os
import socket
import ssl
import sys
import tempfile
from pathlib import Path

from acceptance.evidence import Report, digest
from acceptance.inputs import verify_snapshot
from acceptance.suite import Suite


async def probe(args):
    snapshots = Path(args.snapshots).resolve()
    marker = verify_snapshot(snapshots)
    # Load our fixture certificate helper under a unique name; the product also
    # has a tests/backend/fixtures.py module. Neither is production initialization.
    spec = importlib.util.spec_from_file_location(
        "dep_d_cert_fixture", Path(__file__).with_name("fixtures.py")
    )
    local_fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(local_fixture)
    sys.path[:0] = [
        str(snapshots / "platform"),
        str(snapshots / "platform/tests/backend"),
    ]
    os.environ["TS012_CONTRACT_DIR"] = str(snapshots / "contracts/text-dialogue/v1")
    from aiohttp import web
    from fixtures import ENV
    from services.platform.server import create_app
    from services.platform.service import Platform
    from web_fixtures import PASSWORD, web_settings

    os.environ.update(ENV)
    os.environ.update(DEP_D_PROBE_USER="synthetic-admin", DEP_D_PROBE_PASSWORD=PASSWORD)
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = Report("local", marker["binding"], "product")
    report.data["kind"] = "platform_component_probe"
    report.data["coverage"] = "one_product_synthetic_identity"
    report.data["requirements_sha256"] = digest(
        (snapshots / "platform/requirements-dev.txt").read_bytes()
    )
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        ca, key = local_fixture.certificates(temporary)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(ca, key)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        url = f"https://127.0.0.1:{sock.getsockname()[1]}"
        config = web_settings(temporary, origin=url)
        config["mode"] = "service_https"
        platform = Platform(config)
        runner = web.AppRunner(create_app(platform), access_log=None)
        try:
            await runner.setup()
            await web.SockSite(runner, sock, ssl_context=context).start()
            suite_config = {
                "runtime_kind": "product",
                "mode": "local",
                "model_kind": "recorded",
                "endpoints": {"platform": {"url": url, "ca_file": str(ca)}},
                "web": {
                    "username_env": "DEP_D_PROBE_USER",
                    "password_env": "DEP_D_PROBE_PASSWORD",
                },
            }
            suite = Suite(suite_config, marker["binding"], report)
            facts = await asyncio.to_thread(suite.web_login_csrf)
            report.add("web_login_csrf", "pass", "assertions_satisfied", facts)
            report.add("four_product_chain", "not_run", "platform_component_probe_only")
        except Exception:
            report.add("web_login_csrf", "fail", "product_probe_failed")
            raise
        finally:
            await runner.cleanup()
            sock.close()
            report.save(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    asyncio.run(probe(args))


if __name__ == "__main__":
    main()
