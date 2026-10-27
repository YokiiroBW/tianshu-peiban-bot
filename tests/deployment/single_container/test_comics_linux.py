"""Explicit isolated-image smoke: real helper code, Linux identities and IPC."""

import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

from deploy.single_container.comics_control import ComicsController


CLIENT = r"""
import http.client,json,socket,sys
class Connection(http.client.HTTPConnection):
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(sys.argv[1])
c=Connection('isolated-comic-test',timeout=15)
c.request(sys.argv[2],sys.argv[3],body=sys.argv[4],headers={'Content-Type':'application/json'})
r=c.getresponse()
print(json.dumps({'status':r.status,'body':json.loads(r.read())}))
c.close()
"""


@unittest.skipUnless(
    sys.platform == "linux" and os.environ.get("TIANSHU_RUN_COMICS_LINUX_SMOKE") == "1",
    "explicit isolated Linux image comic smoke required",
)
class ComicsLinuxTests(unittest.TestCase):
    @staticmethod
    def cleanup_media(root):
        # The supervisor has no DAC_OVERRIDE/CHOWN. Its synthetic media trees
        # belong to the original media UID/GID and must be removed by that owner.
        # addCleanup runs before TemporaryDirectory.cleanup and records any cleanup
        # failure separately, preserving the original test assertion/exception.
        subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                "import shutil,sys; from pathlib import Path; "
                "[(shutil.rmtree(p) if p.exists() else None) "
                "for p in (Path(sys.argv[1])/'inputs',Path(sys.argv[1])/'output')]",
                str(root),
            ],
            user=1026,
            group=1654,
            extra_groups=[10001],
            check=True,
            timeout=10,
        )

    def request(self, path, method, route, body=None, *, uid=10001):
        raw = subprocess.check_output(
            [
                sys.executable,
                "-B",
                "-c",
                CLIENT,
                path,
                method,
                route,
                json.dumps(body) if body is not None else "",
            ],
            user=uid,
            group=10001,
            extra_groups=[],
            timeout=20,
            text=True,
        )
        return json.loads(raw)

    def until(self, controller, state):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            value = controller.status()
            if value["state"] == state:
                return value
            if value["state"] == "failed":
                self.fail("real comic helpers failed startup")
            time.sleep(0.02)
        self.fail("real comic helper lifecycle timed out")

    def test_real_helpers_control_peer_identity_aliases_stop_and_restart(self):
        self.assertEqual((os.geteuid(), os.getegid()), (0, 10001))
        ipc = Path("/run/tianshu/comics")
        self.assertFalse(
            ipc.exists(), "run only in an isolated image with fresh IPC tmpfs"
        )
        for name, role in (("ComicInputs", "inputs"), ("ComicPublisher", "publisher")):
            self.assertEqual(
                (Path("/media") / name).readlink().as_posix(), str(ipc / role)
            )
        temporary = tempfile.TemporaryDirectory(prefix="ts352-comics-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.addCleanup(self.cleanup_media, root)
        root.chmod(0o770)
        subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                "from pathlib import Path; import sys; "
                "[(Path(sys.argv[1])/p).mkdir(mode=0o750) for p in ('inputs','output')]",
                str(root),
            ],
            user=1026,
            group=1654,
            extra_groups=[10001],
            check=True,
            timeout=5,
        )
        specifications = []
        for role, module in (
            ("inputs", "source_service"),
            ("publisher", "publisher_service"),
        ):
            settings = {
                "socket": str(ipc / role / "service.sock"),
                "client_uid": 10001,
                **(
                    {"directory": str(root / "inputs")}
                    if role == "inputs"
                    else {"targets": {"comic-main": str(root / "output")}}
                ),
            }
            path = root / (role + ".json")
            path.write_text(json.dumps(settings))
            path.chmod(0o640)
            specifications.append(
                {
                    "name": "comic-" + role,
                    "argv": [
                        sys.executable,
                        "-B",
                        "-m",
                        "services.platform.media.comics." + module,
                        "--settings",
                        str(path),
                    ],
                    "uid": 1026,
                    "gid": 1654,
                    "supplementary_gids": [10001],
                    "health": {
                        "type": "unix",
                        "path": settings["socket"],
                        "interval_seconds": 0.05,
                        "timeout_seconds": 0.2,
                    },
                    "startup_timeout_seconds": 15,
                    "stop_timeout_seconds": 5,
                }
            )
        controller = ComicsController(
            {
                "socket": str(ipc / "control.sock"),
                "client_uid": 10001,
                "processes": specifications,
            }
        )
        stop_tick = threading.Event()

        def tick():
            while not stop_tick.wait(0.01):
                controller.tick()

        controller.open()
        ticker = threading.Thread(target=tick, daemon=True)
        ticker.start()
        try:
            owner = str(uuid.uuid4())
            denied = self.request(
                str(ipc / "control.sock"),
                "POST",
                "/v1/comics/start",
                {"owner": owner},
                uid=0,
            )
            self.assertEqual(denied["status"], 403)
            self.assertEqual(controller.live_pids(), set())
            all_pids = set()
            for attempt in range(2):
                result = self.request(
                    str(ipc / "control.sock"),
                    "POST",
                    "/v1/comics/start",
                    {"owner": owner},
                )
                self.assertEqual(result["status"], 200)
                self.until(controller, "ready")
                pids = controller.live_pids()
                self.assertEqual(len(pids), 2)
                self.assertFalse(pids & all_pids)
                all_pids.update(pids)
                for pid in pids:
                    fields = dict(
                        line.split(":", 1)
                        for line in Path(f"/proc/{pid}/status").read_text().splitlines()
                        if ":" in line
                    )
                    self.assertEqual(fields["Uid"].split(), ["1026"] * 4)
                    self.assertEqual(fields["Gid"].split(), ["1654"] * 4)
                    self.assertEqual(fields["Groups"].split(), ["10001"])
                for role in ("inputs", "publisher"):
                    entry = (ipc / role / "service.sock").stat()
                    self.assertEqual(
                        (entry.st_uid, entry.st_gid, stat.S_IMODE(entry.st_mode)),
                        (1026, 10001, 0o660),
                    )
                for name, method, route, body in (
                    (
                        "ComicInputs",
                        "POST",
                        "/comics/v1/inputs/probe",
                        {"root": str(root / "inputs")},
                    ),
                    ("ComicPublisher", "GET", "/comics/v1/transfers/health", None),
                ):
                    alias = f"/media/{name}/service.sock"
                    reply = self.request(alias, method, route, body)
                    self.assertEqual(reply["status"], 200)
                    self.assertEqual(reply["body"]["status"], "alive")
                    self.assertEqual(
                        self.request(alias, method, route, body, uid=0)["status"],
                        403,
                    )
                stopped = self.request(
                    str(ipc / "control.sock"),
                    "POST",
                    "/v1/comics/stop",
                    {"owner": owner},
                )
                self.assertEqual(
                    stopped["body"],
                    {
                        "state": "stopped",
                        "roles": {"inputs": "stopped", "publisher": "stopped"},
                    },
                )
                self.assertEqual(controller.live_pids(), set())
                self.assertTrue(all(not Path(f"/proc/{pid}").exists() for pid in pids))
                for role in ("inputs", "publisher"):
                    with socket.socket(socket.AF_UNIX) as check:
                        check.settimeout(0.2)
                        with self.assertRaises(
                            (ConnectionRefusedError, FileNotFoundError)
                        ):
                            check.connect(str(ipc / role / "service.sock"))
                if attempt == 0:
                    owner = str(uuid.uuid4())
        finally:
            stop_tick.set()
            ticker.join(timeout=2)
            self.assertTrue(controller.close())
        self.assertFalse((ipc / "control.sock").exists())


if __name__ == "__main__":
    unittest.main()
