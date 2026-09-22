"""Bounded HTTPS, no redirects/proxy inheritance/retries; isolation is loopback-only."""

import http.cookiejar
import http.client
import ipaddress
import io
import json
import os
import ssl
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .evidence import canonical, digest
from .inputs import require


class Missing(Exception):
    pass


class Failed(Exception):
    pass


_DEADLINE = ContextVar("dep_d_deadline", default=None)
OUTPUT_LIMIT = 1024 * 1024


def deadline_after(seconds):
    deadline = time.monotonic() + seconds
    parent = _DEADLINE.get()
    return min(deadline, parent) if parent is not None else deadline


@contextmanager
def deadline_scope(deadline):
    """A child may shorten an absolute deadline, never replenish its parent's budget."""
    parent = _DEADLINE.get()
    deadline = min(deadline, parent) if parent is not None else deadline
    token = _DEADLINE.set(deadline)
    try:
        yield deadline
    finally:
        _DEADLINE.reset(token)


def _remaining(deadline):
    value = deadline - time.monotonic()
    if value <= 0:
        raise TimeoutError("deadline_exceeded")
    return value


class _DeadlineReader(io.RawIOBase):
    """Recompute remaining time for every raw receive, including header readlines."""

    def __init__(self, sock, deadline):
        self.sock, self.deadline = sock, deadline
        # Retain socket.makefile's descriptor ownership. urllib closes its socket
        # after returning headers; this reference keeps only the response alive.
        self.raw = sock.makefile("rb", buffering=0)

    def readable(self):
        return True

    def readinto(self, buffer):
        self.sock.settimeout(_remaining(self.deadline))
        count = self.raw.readinto(buffer)
        _remaining(self.deadline)
        return count

    def close(self):
        try:
            self.raw.close()
        finally:
            super().close()


class _DeadlineSocket:
    def __init__(self, sock, deadline):
        self.sock, self.deadline = sock, deadline

    def __getattr__(self, name):
        return getattr(self.sock, name)

    def sendall(self, data):
        self.sock.settimeout(_remaining(self.deadline))
        self.sock.sendall(data)
        _remaining(self.deadline)

    def makefile(self, mode):
        if mode != "rb":
            raise ValueError("unsupported_socket_file_mode")
        return io.BufferedReader(_DeadlineReader(self.sock, self.deadline))


class _DeadlineConnection(http.client.HTTPConnection):
    def __init__(self, host, timeout, *, context=None, connect_host=None):
        super().__init__(host, timeout=timeout)
        self.context, self.connect_host = context, connect_host

    def connect(self):
        deadline = _DEADLINE.get()
        target = ipaddress.ip_address(self.connect_host or self.host)
        sock = socket.socket(socket.AF_INET6 if target.version == 6 else socket.AF_INET)
        try:
            sock.settimeout(_remaining(deadline))
            sock.connect((str(target), self.port))  # Literal IP; no DNS or proxy.
            if self.context is not None:
                sock.settimeout(_remaining(deadline))
                sock = self.context.wrap_socket(sock, server_hostname=self.host)
            _remaining(deadline)
            self.sock = _DeadlineSocket(sock, deadline)
        except BaseException:
            sock.close()
            raise


def _command_output(command, payload, cwd, env, deadline, cancel_event):
    """Single-threaded nonblocking pipes (Windows requires Python 3.12+).

    Never accumulate more than OUTPUT_LIMIT+1 bytes. The extra byte establishes
    overflow, then only this invocation is killed/reaped and its pipes closed.
    """
    check(len(payload) <= OUTPUT_LIMIT, "adapter_request_budget_exceeded")
    _remaining(deadline)
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
        cwd=cwd,
        env=env,
        shell=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        os.set_blocking(process.stdin.fileno(), False)
        os.set_blocking(process.stdout.fileno(), False)
        output, written, eof = bytearray(), 0, False
        while True:
            _remaining(deadline)
            if cancel_event is not None and cancel_event.is_set():
                raise Failed("adapter_cancelled")
            progressed = False
            if not process.stdin.closed:
                try:
                    written += os.write(
                        process.stdin.fileno(),
                        memoryview(payload)[written : written + 65536],
                    )
                    progressed = True
                except BlockingIOError:
                    pass
                except BrokenPipeError:
                    process.stdin.close()
                if written == len(payload):
                    process.stdin.close()
            if not eof:
                try:
                    chunk = os.read(
                        process.stdout.fileno(),
                        min(65536, OUTPUT_LIMIT + 1 - len(output)),
                    )
                except BlockingIOError:
                    chunk = None
                if chunk is not None:
                    progressed = True
                    if chunk:
                        output.extend(chunk)
                        check(
                            len(output) <= OUTPUT_LIMIT,
                            "adapter_output_budget_exceeded",
                        )
                    else:
                        eof = True
            code = process.poll()
            if code is not None and eof:
                check(code == 0, "adapter_command_failed")
                _remaining(deadline)
                return bytes(output)
            if not progressed:
                time.sleep(min(0.01, _remaining(deadline)))
    finally:
        # No reader/writer threads and no communicate() drain into unbounded RAM.
        # Closing our pipes also handles a child that keeps writing after refusal.
        process.stdin.close()
        process.stdout.close()
        if process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            raise Failed("adapter_cleanup_failed") from None


def check(condition, code):
    if not condition:
        raise Failed(code)


def endpoint(url, allow_http=False, connect_host=None):
    parsed = urllib.parse.urlsplit(url)
    require(
        not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment,
        "unsafe_endpoint",
    )
    # Literal loopback prevents DNS rebinding and accidental NAS access. Use local
    # published ports for container mode; internal containers keep their own TLS names.
    try:
        require(
            ipaddress.ip_address(connect_host or parsed.hostname).is_loopback,
            "loopback_required",
        )
    except (ValueError, TypeError):
        raise ValueError("literal_loopback_required") from None
    require(
        parsed.scheme == "https" or (allow_http and parsed.scheme == "http"),
        "https_required",
    )
    require(
        parsed.port is not None and parsed.path in ("", "/"),
        "explicit_port_and_origin_required",
    )
    require(
        parsed.hostname is not None and not any(c.isspace() for c in parsed.hostname),
        "invalid_tls_hostname",
    )
    require(not connect_host or parsed.scheme == "https", "pinned_host_requires_tls")
    return url.rstrip("/")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, config, synthetic=False):
        self.url = endpoint(config["url"], synthetic, config.get("connect_host"))
        self.jar = http.cookiejar.CookieJar()
        self.timeout = config.get("timeout_seconds", 5)
        require(
            isinstance(self.timeout, (int, float)) and 0 < self.timeout <= 30,
            "invalid_timeout",
        )
        handlers = [
            urllib.request.ProxyHandler({}),
            NoRedirect(),
            urllib.request.HTTPCookieProcessor(self.jar),
        ]
        context = None
        if self.url.startswith("https:"):
            # Do not honor SSLKEYLOGFILE and accidentally persist secrets.
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_verify_locations(cafile=config["ca_file"])
            if config.get("cert_file"):
                context.load_cert_chain(config["cert_file"], config["key_file"])

        def connection(host, timeout):
            return _DeadlineConnection(
                host, timeout, context=context, connect_host=config.get("connect_host")
            )

        class DeadlineHTTP(urllib.request.HTTPHandler):
            def http_open(self, request):
                return self.do_open(connection, request)

        class DeadlineHTTPS(urllib.request.HTTPSHandler):
            def https_open(self, request):
                return self.do_open(connection, request)

        handlers.extend([DeadlineHTTP(), DeadlineHTTPS(context=context)])
        self.opener = urllib.request.build_opener(*handlers)
        self.csrf = None

    def request(self, method, path, body=None, headers=None):
        require(
            path.startswith("/") and not path.startswith("//") and ":" not in path,
            "invalid_request_path",
        )
        supplied = {"Accept": "application/json", **(headers or {})}
        data = None if body is None else canonical(body)
        if data is not None:
            supplied["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.url + path, data=data, headers=supplied, method=method
        )
        deadline = deadline_after(self.timeout)
        try:
            with deadline_scope(deadline):
                try:
                    response = self.opener.open(request, timeout=_remaining(deadline))
                except urllib.error.HTTPError as exc:
                    response = exc
                with response:
                    raw = response.read(OUTPUT_LIMIT + 1)
                    check(len(raw) <= OUTPUT_LIMIT, "response_budget_exceeded")
                    _remaining(deadline)
                    try:
                        value = json.loads(
                            raw,
                            parse_constant=lambda _: (_ for _ in ()).throw(
                                ValueError()
                            ),
                        )
                    except (ValueError, UnicodeError):
                        raise Failed("non_json_response") from None
                    _remaining(deadline)
                    return response.code, value
        except (OSError, urllib.error.URLError, http.client.HTTPException) as exc:
            # A socket timeout can land between coarse monotonic clock ticks on
            # Windows. Keep its identity instead of mislabelling it as TLS failure.
            cause = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            code = (
                "transport_deadline_exceeded"
                if isinstance(cause, TimeoutError) or time.monotonic() >= deadline
                else "transport_or_tls_failure"
            )
            raise Failed(code) from None

    def post(self, path, body, correlation=None, csrf=True, origin=None):
        headers = {"Origin": origin or self.url}
        if csrf and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        if correlation:
            headers["X-Tianshu-Correlation-Id"] = correlation
        return self.request("POST", path, body, headers)


class Adapter:
    """Test harness boundary; implementations call package public commands/ports.

    Returned facts remain adapter-attested in evidence. They are not product HTTP
    receipts or independent proof of a running image's identity.
    """

    def __init__(self, config, synthetic):
        self.config = config
        self.client = (
            Client(config["http"], synthetic) if config and "http" in config else None
        )

    def call(self, operation, *, cancel_event=None, **arguments):
        if not self.config:
            raise Missing("control_adapter_missing")
        request = {"adapter_version": "dep-d/1", "operation": operation, **arguments}
        if self.client:
            status, value = self.client.request("POST", "/control", request)
            check(status == 200, "adapter_http_failed")
        else:
            command = self.config.get("command")
            if not command:
                raise Missing("control_adapter_missing")
            require(
                isinstance(command, list) and all(isinstance(x, str) for x in command),
                "argv_required",
            )
            # Commands are explicit trusted test configuration, never shell text.
            # No inherited product credentials; only named variables can be forwarded.
            env = {
                k: v
                for k, v in os.environ.items()
                if k in ("SystemRoot", "WINDIR", "PATH", "TEMP", "TMP")
            }
            env.update(PYTHONDONTWRITEBYTECODE="1")
            for name in self.config.get("env_names", []):
                if name not in os.environ:
                    raise Missing("adapter_environment_missing")
                env[name] = os.environ[name]
            try:
                raw = _command_output(
                    command,
                    canonical(request),
                    Path(self.config["cwd"]),
                    env,
                    deadline_after(30),
                    cancel_event,
                )
            except FileNotFoundError:
                raise Missing("adapter_command_missing") from None
            except TimeoutError:
                raise Failed("adapter_timeout") from None
            except OSError:
                raise Failed("adapter_io_failed") from None
            try:
                value = json.loads(raw)
            except (ValueError, UnicodeError):
                raise Failed("adapter_response_invalid") from None
        if value.get("status") == "unsupported":
            raise Missing("adapter_operation_unsupported")
        check(value.get("adapter_version") == "dep-d/1", "adapter_version_mismatch")
        return value

    def fingerprint(self):
        return digest(canonical(self.config)) if self.config else None
