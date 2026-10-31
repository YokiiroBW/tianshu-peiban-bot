"""Bounded local music tools; no credentials in argv, logs, or inherited service secrets."""

import asyncio
import json
import os
import shutil
import subprocess

from ...contracts import Fault, require
from .request_control import Limited


def executable(name):
    override = os.environ.get("TIANSHU_" + name.upper().replace("-", "_"))
    return override or shutil.which("tianshu-" + name)


async def invoke(name, request, *, timeout=120, meter=None):
    program = executable(name)
    require(program is not None, "music_tool_unavailable", 503)
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        in {
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "TEMP",
            "TMP",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
        }
    }
    process = await asyncio.create_subprocess_exec(
        program,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=environment,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    try:
        raw, _ = await asyncio.wait_for(
            process.communicate(json.dumps(request, ensure_ascii=False).encode("utf-8")), timeout
        )
        require(process.returncode == 0 and len(raw) <= 32 * 1024**2, "music_tool_failed", 502)
        value = json.loads(raw)
        require(isinstance(value, dict), "music_upstream_invalid", 502)
        if meter is not None and type(value.get("requests_used")) is int:
            meter["used"] = value["requests_used"]
        if not value.get("ok"):
            code = value.get("code")
            if code == "music_rate_limited":
                seconds = value.get("retry_after_seconds", 0)
                raise Limited(
                    seconds if type(seconds) in (int, float) and 0 <= seconds <= 2592000 else 0
                )
            allowed = {
                "music_quality_unavailable",
                "music_download_forbidden",
                "music_auth_required",
                "music_upstream_invalid",
                "music_upstream_unavailable",
                "music_decode_failed",
                "music_decode_key_required",
                "music_decode_unsupported",
                "invalid_input",
            }
            raise Fault(code if code in allowed else "music_tool_failed", 409)
        return value["value"]
    except (ValueError, KeyError):
        raise Fault("music_upstream_invalid", 502) from None
    except TimeoutError:
        raise Fault("music_upstream_unavailable", 502) from None
    finally:
        if process.returncode is None:
            process.kill()
            await process.communicate()
