"""Real service probes and the container's fresh, aggregate health verdict."""

import argparse
import json
import math
import os
from pathlib import Path
import socket
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit


def positive_number(value, name, maximum=3600):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= maximum:
        raise ValueError("invalid " + name)
    return float(value)


def validate_probe(raw):
    if not isinstance(raw, dict):
        raise ValueError("a real health probe is required")
    kind = raw.get("type")
    common = {"type", "timeout_seconds", "interval_seconds"}
    if kind == "http":
        allowed = common | {"url", "ca_file", "headers", "bearer_token_env", "bearer_token_file",
                            "client_certfile", "client_keyfile", "expected_status"}
        if not isinstance(raw.get("url"), str):
            raise ValueError("invalid health URL")
        url = urlsplit(raw["url"])
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError("invalid health URL")
        headers = raw.get("headers", {})
        if not isinstance(headers, dict) or not all(
            isinstance(k, str) and isinstance(v, str) and "\n" not in k + v and "\r" not in k + v
            for k, v in headers.items()
        ):
            raise ValueError("invalid health headers")
        status = raw.get("expected_status", 200)
        if type(status) is not int or not 100 <= status <= 599:
            raise ValueError("invalid health status")
        if ("client_certfile" in raw) != ("client_keyfile" in raw):
            raise ValueError("both health client certificate and key are required")
        if "bearer_token_env" in raw and "bearer_token_file" in raw:
            raise ValueError("only one health bearer source is supported")
        for key in ("ca_file", "bearer_token_env", "bearer_token_file", "client_certfile", "client_keyfile"):
            if key in raw and (not isinstance(raw[key], str) or not raw[key]):
                raise ValueError("invalid health " + key)
    elif kind == "tcp":
        allowed = common | {"host", "port"}
        if not isinstance(raw.get("host"), str) or not raw["host"]:
            raise ValueError("invalid health host")
        if type(raw.get("port")) is not int or not 0 < raw["port"] < 65536:
            raise ValueError("invalid health port")
    elif kind == "unix":
        allowed = common | {"path"}
        if not isinstance(raw.get("path"), str) or not Path(raw["path"]).is_absolute():
            raise ValueError("invalid health socket")
    else:
        raise ValueError("unsupported health probe")
    if set(raw) - allowed:
        raise ValueError("unknown health field")
    result = dict(raw)
    result["timeout_seconds"] = positive_number(raw.get("timeout_seconds", 2), "probe timeout", 30)
    result["interval_seconds"] = positive_number(raw.get("interval_seconds", 5), "probe interval")
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(check, environment):
    """Never include response bodies, request URLs, headers or exceptions in status."""
    timeout = check["timeout_seconds"]
    try:
        if check["type"] == "tcp":
            with socket.create_connection((check["host"], check["port"]), timeout=timeout):
                return {"ok": True, "code": "connected"}
        if check["type"] == "unix":
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(timeout)
                connection.connect(check["path"])
            return {"ok": True, "code": "connected"}
        headers = dict(check.get("headers", {}))
        token_name = check.get("bearer_token_env")
        token_file = check.get("bearer_token_file")
        if token_name or token_file:
            token = environment.get(token_name) if token_name else Path(token_file).read_text(encoding="utf-8").strip()
            if not token:
                return {"ok": False, "code": "missing_token"}
            headers["Authorization"] = "Bearer " + token
        handlers = [urllib.request.ProxyHandler({}), NoRedirect()]
        if urlsplit(check["url"]).scheme == "https":
            context = ssl.create_default_context(cafile=check.get("ca_file"))
            if check.get("client_certfile"):
                context.load_cert_chain(check["client_certfile"], check["client_keyfile"])
            handlers.append(urllib.request.HTTPSHandler(context=context))
        opener = urllib.request.build_opener(*handlers)
        request = urllib.request.Request(check["url"], headers=headers, method="GET")
        try:
            with opener.open(request, timeout=timeout) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
        ok = status == check.get("expected_status", 200)
        return {"ok": ok, "code": "http_" + str(status)}
    except (OSError, ValueError, urllib.error.URLError):
        return {"ok": False, "code": "unavailable"}


def process_alive(pid):
    if os.name != "nt":
        os.kill(pid, 0)
        return True
    # Windows treats signal 0 as CTRL_C_EVENT, not the POSIX existence probe.
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.OpenProcess(0x100000, False, pid)
    if not handle:
        return False
    try:
        return api.WaitForSingleObject(handle, 0) == 258
    finally:
        api.CloseHandle(handle)


def check_state(path, *, max_age=10, now=None):
    """A recorded PID alone never makes a container healthy."""
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
        now = time.time() if now is None else now
        age = now - state["updated_at"]
        if not 0 <= age <= max_age or state.get("version") != 1 or state.get("status") != "ready":
            return False
        if type(state.get("pid")) is not int or state["pid"] <= 0:
            return False
        if not process_alive(state["pid"]):
            return False
        processes = state["processes"]
        return bool(processes) and all(
            value.get("state") == "ready" and value.get("health", {}).get("ok") is True
            for value in processes.values()
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True)
    parser.add_argument("--max-age", type=float, default=10)
    args = parser.parse_args(argv)
    positive_number(args.max_age, "state age")
    return 0 if check_state(args.state, max_age=args.max_age) else 1


if __name__ == "__main__":
    raise SystemExit(main())
