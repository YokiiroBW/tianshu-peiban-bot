"""Private lifecycle port for the existing input and atomic publisher file services."""

import asyncio
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import stat
import struct
import time
from uuid import UUID

from aiohttp import web


ROLES = {"inputs": "source_service", "publisher": "publisher_service"}


class ComicsController:
    def __init__(self, supervisor, config):
        self.supervisor, self.config = supervisor, config
        self.owner = None
        self.processes = {}
        self.started_at = 0
        self.lock = asyncio.Lock()
        self.runner = None
        self.lease = None
        self.socket = Path(config["socket"])
        if self.socket != Path("/run/tianshu/comics/control.sock"):
            raise ValueError("fixed_comic_control_socket_required")
        self.specs = {}
        for role, module in ROLES.items():
            spec = config[role]
            document = json.loads(Path(spec["settings"]).read_text(encoding="utf-8"))
            expected = Path("/run/tianshu/comics") / role / "service.sock"
            if document["client_uid"] != supervisor.uid or Path(document["socket"]) != expected:
                raise ValueError("comic_file_identity_mismatch")
            if int(spec["uid"]) <= 0 or int(spec["gid"]) <= 0:
                raise ValueError("comic_file_unprivileged_identity_required")
            self.specs[role] = {**spec, "socket": expected, "module": module}

    async def status(self):
        if not self.processes:
            return {"state": "stopped", "roles": {role: "stopped" for role in ROLES}}
        roles = {
            role: "failed" if child.returncode is not None else "running"
            for role, child in self.processes.items()
        }

        def current_socket(spec):
            try:
                metadata = spec["socket"].lstat()
                return (
                    stat.S_ISSOCK(metadata.st_mode)
                    and metadata.st_uid == spec["uid"]
                    and metadata.st_ctime_ns >= self.started_at
                )
            except FileNotFoundError:
                return False

        ready = all(current_socket(spec) for spec in self.specs.values())
        return {
            "state": "failed" if "failed" in roles.values() else "ready" if ready else "starting",
            "roles": roles,
        }

    async def stop(self):
        for role in self.processes:
            self.supervisor.terminate("comic-" + role, signal.SIGTERM)
        try:
            async with asyncio.timeout(10):
                await asyncio.gather(*(child.wait() for child in self.processes.values()))
        except TimeoutError:
            for role in self.processes:
                self.supervisor.terminate("comic-" + role, signal.SIGKILL)
            await asyncio.gather(*(child.wait() for child in self.processes.values()))
        self.processes = {}
        self.owner = None

    async def request(self, request):
        peer = request.transport.get_extra_info("socket") if request.transport else None
        uid = (
            struct.unpack("3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]
            if peer
            else None
        )
        if uid != self.supervisor.uid:
            raise web.HTTPForbidden()
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {"owner"}:
            raise web.HTTPBadRequest()
        try:
            if str(UUID(body["owner"])) != body["owner"]:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise web.HTTPBadRequest() from None
        async with self.lock:
            if self.owner is not None and self.owner != body["owner"]:
                raise web.HTTPConflict()
            operation = request.match_info["operation"]
            if operation == "start" and not self.processes:
                self.owner = body["owner"]
                self.started_at = time.time_ns()
                try:
                    for role, spec in self.specs.items():
                        self.processes[role] = await self.supervisor.spawn(
                            "comic-" + role,
                            [
                                os.sys.executable,
                                "-m",
                                "tianshu.capabilities.media.comics." + spec["module"],
                                "--settings",
                                spec["settings"],
                            ],
                            uid=spec["uid"],
                            gid=spec["gid"],
                            critical=False,
                        )
                except BaseException:
                    await self.stop()
                    raise
            elif operation == "stop":
                await self.stop()
            elif operation not in {"start", "status"}:
                raise web.HTTPNotFound()
            return web.json_response(await self.status())

    async def open(self):
        root = self.socket.parent
        root.mkdir(parents=True, exist_ok=True)
        for parent in (root, *root.parents):
            if parent.is_symlink():
                raise ValueError("unsafe_comic_socket_directory")
        os.chown(root, 0, self.supervisor.gid)
        root.chmod(0o755)
        for spec in self.specs.values():
            parent = spec["socket"].parent
            parent.mkdir(exist_ok=True)
            if parent.is_symlink():
                raise ValueError("unsafe_comic_socket_directory")
            os.chown(parent, 0, self.supervisor.gid)
            parent.chmod(0o3770)
        lease = root / "control.lock"
        if lease.is_symlink():
            raise ValueError("unsafe_comic_socket_lease")
        self.lease = lease.open("ab")
        fcntl.flock(self.lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if self.socket.exists() or self.socket.is_symlink():
            metadata = self.socket.lstat()
            if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != self.supervisor.uid:
                raise ValueError("foreign_comic_control_socket")
            self.socket.unlink()
        app = web.Application(client_max_size=4096)
        app.router.add_post("/v1/comics/{operation}", self.request)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.UnixSite(self.runner, str(self.socket)).start()
        os.chown(self.socket, self.supervisor.uid, self.supervisor.gid)
        self.socket.chmod(0o600)

    async def close(self):
        if self.runner:
            await self.runner.cleanup()
        async with self.lock:
            await self.stop()
        if self.lease:
            self.lease.close()
