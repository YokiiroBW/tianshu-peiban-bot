"""Local lifecycle endpoint for the existing comic-files plugin's two workers."""

from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import socket
import socketserver
import stat
import struct
import threading
import uuid

from .process import ProcessGroup, validate_process


ROLES = {"inputs": "comic-inputs", "publisher": "comic-publisher"}
IPC_DIRECTORY = Path("/run/tianshu/comics")
CONTROL_SOCKET = "/run/tianshu/comics/control.sock"


def _checked_directory(path, *, group=None, mode=None):
    entry = path.lstat()
    permissions = stat.S_IMODE(entry.st_mode)
    traverse = 0o010 if entry.st_gid == 10001 else 0o001
    if (not stat.S_ISDIR(entry.st_mode) or entry.st_uid != 0
            or not permissions & traverse
            or (group is not None and entry.st_gid != group)
            or (mode is not None and permissions != mode)
            or (mode is None and permissions & 0o022)):
        raise ValueError("unsafe comics IPC directory")


def _prepare_ipc_parent():
    # This fixed tree is the application's tmpfs, never a media/data mount.
    # Its root may have tmpfs's group 0; the newly created children inherit
    # the supervisor's primary group, so CAP_CHOWN is neither used nor needed.
    if os.name != "posix" or os.geteuid() != 0 or os.getegid() != 10001:
        raise ValueError("comics IPC requires supervisor identity 0:10001")
    _checked_directory(IPC_DIRECTORY.parent)
    try:
        IPC_DIRECTORY.mkdir(mode=0o755)
    except FileExistsError:
        pass
    else:
        IPC_DIRECTORY.chmod(0o755)
    _checked_directory(IPC_DIRECTORY, group=10001)


def prepare_worker_directories():
    _prepare_ipc_parent()
    for role in ROLES:
        path = IPC_DIRECTORY / role
        try:
            path.mkdir(mode=0o1770)
        except FileExistsError:
            pass
        else:
            # mkdir's mode is filtered by umask. Set the complete sticky/group
            # mode only on directories just created in this root-owned tree.
            path.chmod(0o1770)
        _checked_directory(path, group=10001, mode=0o1770)


class ControlError(Exception):
    def __init__(self, code, status):
        self.code, self.status = code, status


def validate_config(raw):
    if not isinstance(raw, dict) or set(raw) - {"socket", "client_uid", "processes"}:
        raise ValueError("invalid comics control fields")
    if not isinstance(raw.get("socket"), str) or not Path(raw["socket"]).is_absolute():
        raise ValueError("comics control socket must be absolute")
    uid = raw.get("client_uid", 10001)
    if type(uid) is not int or uid < 0:
        raise ValueError("invalid comics client uid")
    if not isinstance(raw.get("processes"), list):
        raise ValueError("comics processes required")
    specifications = [validate_process(item) for item in raw["processes"]]
    if len(specifications) != 2 or {item["name"] for item in specifications} != set(ROLES.values()):
        raise ValueError("exactly two comic file roles required")
    if any(item["depends_on"] for item in specifications):
        raise ValueError("comic role dependencies are internal")
    return {"socket": raw["socket"], "client_uid": uid, "processes": specifications}


class ComicsController:
    def __init__(self, config):
        self.config = validate_config(config)
        self.lock = threading.RLock()
        self.group = None
        self.owner = None
        self.closed = False
        self.server = None
        self.thread = None

    def status(self):
        with self.lock:
            if self.group is None:
                return {"state": "stopped", "roles": {role: "stopped" for role in ROLES}}
            roles = {}
            for role, name in ROLES.items():
                state = self.group.processes[name].state
                roles[role] = "stopped" if state in {"pending", "stopped"} else "failed" if state in {"failed", "blocked", "unhealthy"} else "running"
            return {"state": self.group.status(), "roles": roles}

    def action(self, operation, owner):
        try:
            if not isinstance(owner, str) or str(uuid.UUID(owner)) != owner:
                raise ValueError()
        except (ValueError, AttributeError):
            raise ControlError("comic_file_invalid_owner", 400) from None
        with self.lock:
            if self.closed:
                raise ControlError("comic_file_control_stopping", 503)
            if self.owner is not None and self.owner != owner:
                raise ControlError("comic_file_owner_conflict", 409)
            if operation == "start":
                if self.group is None:
                    if self.config["socket"] == CONTROL_SOCKET:
                        prepare_worker_directories()
                    self.owner = owner
                    self.group = ProcessGroup(self.config["processes"])
                    self.group.start()
            elif operation == "stop":
                if self.group is not None:
                    if not self.group.stop():
                        raise ControlError("comic_file_stop_unconfirmed", 503)
                    self.group = None
                    self.owner = None
            elif operation != "status":
                raise ControlError("comic_file_unknown_action", 404)
            return self.status()

    def tick(self):
        with self.lock:
            if self.group is not None:
                self.group.tick()

    def live_pids(self):
        with self.lock:
            return set() if self.group is None else {
                item.child.pid for item in self.group.processes.values()
                if item.child is not None and item.child.poll() is None
            }

    def open(self):
        if os.name != "posix" or not hasattr(socket, "SO_PEERCRED"):
            raise ValueError("comics controller requires Linux peer credentials")
        path = Path(self.config["socket"])
        if self.config["socket"] == CONTROL_SOCKET:
            _prepare_ipc_parent()
        else:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        if path.exists():
            if not stat.S_ISSOCK(path.lstat().st_mode):
                raise ValueError("comics control path is not a socket")
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as check:
                    check.settimeout(0.25)
                    check.connect(str(path))
            except ConnectionRefusedError:
                path.unlink()
            else:
                raise ValueError("comics controller already running")
        controller = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                self.request.settimeout(2)
                super().setup()

            def log_message(self, *_):
                pass

            def do_POST(self):
                self.connection.settimeout(2)
                try:
                    peer = self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                    if struct.unpack("3i", peer)[1] != controller.config["client_uid"]:
                        raise ControlError("comic_file_peer_denied", 403)
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 4096 or self.headers.get("Transfer-Encoding"):
                        raise ControlError("comic_file_invalid_request", 400)
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict) or set(body) != {"owner"}:
                        raise ControlError("comic_file_invalid_request", 400)
                    if not self.path.startswith("/v1/comics/"):
                        raise ControlError("comic_file_unknown_action", 404)
                    answer = controller.action(self.path.removeprefix("/v1/comics/"), body["owner"])
                    self.reply(200, answer)
                except ControlError as error:
                    self.reply(error.status, {"code": error.code})
                except (OSError, ValueError, TypeError):
                    self.reply(503, {"code": "comic_file_control_unavailable"})

            def reply(self, status, body):
                raw = json.dumps(body, separators=(",", ":")).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(raw)
                except (OSError, ValueError):
                    pass

        class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

        self.server = Server(str(path), Handler)
        os.chmod(path, 0o660)
        # The supervisor uses the existing file-protocol group (10001). Keep
        # its socket ownership; no CHOWN or DAC_OVERRIDE capability is needed.
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def close(self):
        with self.lock:
            self.closed = True
            stopped = self.group is None or self.group.stop()
            if stopped:
                self.group = None
                self.owner = None
        if self.server is not None:
            if self.thread is not None and self.thread.is_alive():
                self.server.shutdown()
            self.server.server_close()
            Path(self.config["socket"]).unlink(missing_ok=True)
        if self.thread is not None:
            self.thread.join(timeout=2)
        return stopped
