"""Lifecycle of the two existing comic file roles inside the Tianshu runtime.

Only the trusted runtime configuration chooses commands, paths and Unix identities.
The plugin owns a pair of fixed roles; it cannot launch an arbitrary local process.
"""

import asyncio
import json
from pathlib import Path, PurePosixPath
import uuid

import aiohttp

from ...contracts import Fault, require

PLUGIN_ID = "tianshu.comics-files"
BUILTIN = "comic-files"
EMPTY = {"type": "object", "additionalProperties": False}
BUILTIN_ROOT = Path(__file__).resolve().parents[2] / "plugins" / "builtin" / BUILTIN
STATUS = json.loads((BUILTIN_ROOT / "contract.json").read_text(encoding="utf-8"))["status"]


def configuration(settings):
    value = settings.get("plugins", {}).get("comic_files")
    if value is None:
        return None
    require(
        isinstance(value, dict)
        and {"runtime_socket"} <= set(value) <= {"runtime_socket", "defaults"}
        and isinstance(value["runtime_socket"], str)
        and PurePosixPath(value["runtime_socket"]).is_absolute()
        and not any(ord(c) < 32 for c in value["runtime_socket"]),
        "invalid_input",
        400,
    )
    if "defaults" in value:
        defaults = value["defaults"]
        require(
            isinstance(defaults, dict)
            and set(defaults) == {"import_directory", "import_socket", "publisher_socket"}
            and all(
                isinstance(path, str)
                and len(path) <= 4096
                and PurePosixPath(path).is_absolute()
                and not any(ord(c) < 32 for c in path)
                for path in defaults.values()
            ),
            "invalid_input",
            400,
        )
        require(
            len({value["runtime_socket"], defaults["import_socket"], defaults["publisher_socket"]})
            == 3,
            "invalid_input",
            400,
        )
    return value


def builtin_package(package):
    """An uploaded package cannot gain the built-in's lifecycle port by copying its id."""
    return package["manifest"]["id"] == PLUGIN_ID and package.get("source") == {
        "type": "directory",
        "path": str(BUILTIN_ROOT),
    }


class ComicFileWorkers:
    def __init__(self, socket_path, *, startup_timeout=10):
        self.socket = socket_path
        self.startup_timeout = startup_timeout

    @classmethod
    def from_settings(cls, settings):
        config = configuration(settings)
        return cls(config["runtime_socket"]) if config is not None else None

    async def request(self, owner, operation):
        require(operation in {"start", "stop", "status"}, "invalid_input", 400)
        try:
            require(str(uuid.UUID(owner)) == owner, "invalid_input", 400)
        except (ValueError, TypeError, AttributeError):
            raise Fault("invalid_input", 400) from None
        try:
            async with aiohttp.ClientSession(
                connector=aiohttp.UnixConnector(path=self.socket),
                timeout=aiohttp.ClientTimeout(total=5),
                cookie_jar=aiohttp.DummyCookieJar(),
                trust_env=False,
            ) as session:
                async with session.post(
                    "http://comic-runtime/v1/comics/" + operation,
                    json={"owner": owner},
                    allow_redirects=False,
                ) as response:
                    raw = bytearray()
                    async for chunk in response.content.iter_chunked(1024):
                        raw.extend(chunk)
                        require(len(raw) <= 4096, "comic_file_runtime_unavailable", 503)
                    require(response.status != 409, "comic_file_owner_conflict", 409)
                    require(response.status == 200, "comic_file_runtime_unavailable", 503)
                    result = json.loads(raw)
            # The fixed response deliberately contains no paths, ids, commands or credentials.
            from ...plugins.schema import check

            check(STATUS, result)
            require(
                result["state"] != "ready"
                or all(value == "running" for value in result["roles"].values()),
                "comic_file_runtime_unavailable",
                503,
            )
            return result
        except (aiohttp.ClientError, TimeoutError, OSError, ValueError, UnicodeError):
            raise Fault("comic_file_runtime_unavailable", 503) from None

    async def start(self, owner):
        try:
            async with asyncio.timeout(self.startup_timeout):
                result = await self.request(owner, "start")
                while result["state"] == "starting":
                    await asyncio.sleep(0.1)
                    result = await self.status(owner)
                require(result["state"] == "ready", "comic_file_runtime_unavailable", 503)
                return result
        except TimeoutError:
            raise Fault("comic_file_runtime_unavailable", 503) from None

    async def stop(self, owner):
        result = await self.request(owner, "stop")
        require(
            result["state"] == "stopped"
            and all(value == "stopped" for value in result["roles"].values()),
            "comic_file_runtime_unavailable",
            503,
        )
        return result

    async def status(self, owner):
        return await self.request(owner, "status")


async def capability(host, context, operation):
    require(
        host.comic_files is not None and builtin_package(context["package"]),
        "permission_denied",
        403,
    )
    # Actions and model tools can read state, but lifecycle changes belong to the
    # existing plugin host's start/stop calls, not to an invocation's write intent.
    require(operation == "status" or context["call"] is None, "permission_denied", 403)
    if operation == "start":
        instance = await host.instance(context["instance"])
        require(instance["desired_state"] == "enabled", "instance_disabled", 409)
    return await getattr(host.comic_files, operation)(context["instance"])
