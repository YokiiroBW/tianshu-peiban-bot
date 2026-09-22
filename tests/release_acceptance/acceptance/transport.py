"""Bounded HTTPS, no redirects/proxy inheritance/retries; isolation is loopback-only."""

import http.cookiejar
import http.client
import ipaddress
import json
import os
import ssl
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .evidence import canonical, digest
from .inputs import require


class Missing(Exception):
    pass


class Failed(Exception):
    pass


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
        if self.url.startswith("https:"):
            # Do not honor SSLKEYLOGFILE and accidentally persist secrets.
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_verify_locations(cafile=config["ca_file"])
            if config.get("cert_file"):
                context.load_cert_chain(config["cert_file"], config["key_file"])
            connect_host = config.get("connect_host")
            if connect_host:

                class PinnedConnection(http.client.HTTPSConnection):
                    def connect(self):
                        self.sock = socket.create_connection(
                            (connect_host, self.port), self.timeout
                        )
                        try:
                            self.sock = self._context.wrap_socket(
                                self.sock, server_hostname=self.host
                            )
                        except BaseException:
                            self.sock.close()
                            raise

                class PinnedHTTPS(urllib.request.HTTPSHandler):
                    def https_open(self, request):
                        return self.do_open(PinnedConnection, request, context=context)

                handlers.append(PinnedHTTPS(context=context))
            else:
                handlers.append(urllib.request.HTTPSHandler(context=context))
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
        try:
            response = self.opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        except (OSError, urllib.error.URLError, TimeoutError):
            raise Failed("transport_or_tls_failure") from None
        with response:
            raw = response.read(1024 * 1024 + 1)
            check(len(raw) <= 1024 * 1024, "response_budget_exceeded")
            try:
                value = json.loads(
                    raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError())
                )
            except (ValueError, UnicodeError):
                raise Failed("non_json_response") from None
            return response.code, value

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

    def call(self, operation, **arguments):
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
                result = subprocess.run(
                    command,
                    input=canonical(request),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    cwd=Path(self.config["cwd"]),
                    env=env,
                    timeout=30,
                    shell=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except FileNotFoundError:
                raise Missing("adapter_command_missing") from None
            except subprocess.TimeoutExpired:
                raise Failed("adapter_timeout") from None
            check(
                result.returncode == 0 and len(result.stdout) <= 1024 * 1024,
                "adapter_command_failed",
            )
            value = json.loads(result.stdout)
        if value.get("status") == "unsupported":
            raise Missing("adapter_operation_unsupported")
        check(value.get("adapter_version") == "dep-d/1", "adapter_version_mismatch")
        return value

    def fingerprint(self):
        return digest(canonical(self.config)) if self.config else None
