"""Private Linux file-service socket identity and lifetime, shared by reader and writer."""

import os
import socket
import stat
import struct
from pathlib import Path

from aiohttp import web

from ...contracts import Fault, require


def peer_uid(request):
    connection = request.transport.get_extra_info("socket") if request.transport else None
    require(
        connection is not None and hasattr(socket, "SO_PEERCRED"),
        "comic_file_service_forbidden",
        403,
    )
    try:
        _, uid, _ = struct.unpack(
            "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
    except OSError:
        raise Fault("comic_file_service_forbidden", 403) from None
    return uid


def boundary(client_uid):
    require(type(client_uid) is int and client_uid > 0, "invalid_input", 400)

    @web.middleware
    async def authorize(request, handler):
        try:
            require(peer_uid(request) == client_uid, "comic_file_service_forbidden", 403)
            return await handler(request)
        except Fault as error:
            return web.json_response({"code": error.code}, status=error.status)
        except (ValueError, TypeError, UnicodeError):
            return web.json_response({"code": "invalid_input"}, status=400)
        except OSError:
            return web.json_response({"code": "comic_storage_unavailable"}, status=503)

    return authorize


def serve(app, socket_path):
    require(
        os.name == "posix" and hasattr(socket, "SO_PEERCRED"),
        "comic_file_service_linux_required",
        409,
    )
    path = Path(socket_path)
    require(path.is_absolute() and path.parent.is_dir(), "invalid_input", 400)
    for parent in (path.parent, *path.parent.parents):
        require(not parent.is_symlink(), "unsafe_library_path", 409)
    if path.exists() or path.is_symlink():
        require(
            stat.S_ISSOCK(path.lstat().st_mode) and path.lstat().st_uid == os.getuid(),
            "unsafe_library_path",
            409,
        )
    os.umask(0o007)
    import fcntl

    lock_path = path.with_suffix(".lock")
    require(not lock_path.is_symlink(), "unsafe_library_path", 409)
    with lock_path.open("ab") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        path.unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(path))
            os.chmod(path, 0o660)
            listener.listen(128)
            web.run_app(app, sock=listener, access_log=None, print=None)
