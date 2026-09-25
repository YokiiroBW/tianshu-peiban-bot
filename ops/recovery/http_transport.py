"""One-thread nonblocking TLS/HTTP; every IO and parser step shares one budget.

No DNS, proxy, redirects, retry/replay or background workers. Closing the owned
socket never waits for a peer TLS close_notify. Limits include chunk framing.
"""

import errno
import json
import re
import select
import socket
import ssl
import time
from urllib.parse import urlsplit

from .safety import DrillDiagnosticError, RecoveryError, require

HEADER_LIMIT = 32 * 1024
BODY_LIMIT = 256 * 1024
POST_BODY_LIMIT = 16 * 1024
FIELD = re.compile(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
SAFE_RESPONSE_KEYS = frozenset(
    {"status", "code", "selected_units", "omissions", "history", "turns", "request_id"}
)
SAFE_PRODUCT_CODES = frozenset(
    {
        "invalid_host",
        "unauthorized",
        "forbidden",
        "invalid_input",
        "no_match",
        "dependency_unavailable",
    }
)


def response_structure(data):
    """Summarize bounded JSON shape without persisting any returned content."""
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError):
        return {"kind": "invalid_json"}
    if isinstance(value, dict):
        summary = {
            "kind": "object",
            "key_count": len(value),
            "known_keys": sorted(set(value) & SAFE_RESPONSE_KEYS),
        }
        if isinstance(value.get("code"), str) and value["code"] in SAFE_PRODUCT_CODES:
            summary["product_code"] = value["code"]
        return summary
    if isinstance(value, list):
        return {"kind": "array", "item_count": len(value)}
    return {"kind": type(value).__name__}

# These POST routes are documented read boundaries in the product contracts. Keep this
# list closed: adding a path requires reviewing that product handler for side effects.
READ_ONLY_POST_ENDPOINTS = frozenset(
    {
        ("companion", "/internal/v1/source-facts/read"),
        ("companion", "/internal/v1/conversation/web-snapshot"),
        ("companion", "/internal/v1/life-read/actors"),
        ("companion", "/internal/v1/life-read/snapshot"),
        ("companion", "/internal/v1/life-read/diaries"),
        ("companion", "/internal/v1/life-read/revision"),
        ("memory", "/internal/v1/memory/select"),
        ("memory", "/internal/v1/memory/profiles/select"),
        ("memory", "/internal/v1/memory/source-sync/check"),
        ("platform", "/internal/v1/source-access/read"),
        ("platform", "/internal/v1/model-config/snapshot"),
    }
)


def encode_post_body(body):
    require(type(body) is dict, "drill_request_invalid")
    try:
        encoded = json.dumps(
            body,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, RecursionError):
        raise RecoveryError("drill_request_invalid") from None
    require(len(encoded) <= POST_BODY_LIMIT, "drill_request_limit")
    return encoded


class Connection:
    def __init__(self, tls, port, budget):
        self.tls, self.port, self.budget = tls, port, budget
        self.socket = None

    def interval(self):
        self.budget.check()
        return max(0, min(0.025, self.budget.ends - time.monotonic()))

    def retry(self, operation, *, writing=False):
        while True:
            self.budget.check()
            try:
                result = operation()
            except ssl.SSLWantWriteError:
                select.select([], [self.socket], [], self.interval())
            except ssl.SSLWantReadError:
                select.select([self.socket], [], [], self.interval())
            except BlockingIOError:
                select.select(
                    [] if writing else [self.socket],
                    [self.socket] if writing else [],
                    [],
                    self.interval(),
                )
            else:
                self.budget.check()
                return result

    def __enter__(self):
        self.budget.check()
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setblocking(False)
        try:
            status = self.socket.connect_ex(("127.0.0.1", self.port))
            if status == errno.ECONNREFUSED:
                raise RecoveryError("drill_connection_refused")
            require(
                status in {0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY},
                "drill_connection_failed",
            )
            if status:
                while True:
                    _, writable, errors = select.select(
                        [], [self.socket], [self.socket], self.interval()
                    )
                    self.budget.check()
                    if writable or errors:
                        connected = self.socket.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                        if connected == errno.ECONNREFUSED:
                            raise RecoveryError("drill_connection_refused")
                        require(
                            connected == 0,
                            "drill_connection_failed",
                        )
                        break
            self.socket = self.tls.wrap_socket(
                self.socket, server_hostname="127.0.0.1", do_handshake_on_connect=False
            )
            self.socket.setblocking(False)
            self.retry(self.socket.do_handshake)
            return self
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.socket is not None:
            self.socket.close()
            self.socket = None

    def __exit__(self, *_):
        self.close()

    def send(self, data):
        view = memoryview(data)
        while view:
            count = self.retry(lambda: self.socket.send(view), writing=True)
            require(count > 0, "drill_connection_closed")
            view = view[count:]

    def receive(self):
        return self.retry(lambda: self.socket.recv(4096))


class Reader:
    def __init__(self, connection):
        self.connection = connection
        self.buffer = bytearray()
        self.header_bytes = 0
        self.fields = 0

    def line(self, *, limit=8192, header=False):
        while True:
            self.connection.budget.check()
            end = self.buffer.find(b"\r\n")
            require(
                (end + 2 if end >= 0 else len(self.buffer)) <= limit,
                "drill_header_limit",
            )
            if end >= 0:
                line = bytes(self.buffer[: end + 2])
                del self.buffer[: end + 2]
                if header:
                    self.header_bytes += len(line)
                    self.fields += 1
                    require(
                        self.header_bytes <= HEADER_LIMIT and self.fields <= 102,
                        "drill_header_limit",
                    )
                return line
            require(
                not header or self.header_bytes + len(self.buffer) <= HEADER_LIMIT,
                "drill_header_limit",
            )
            part = self.connection.receive()
            require(part, "drill_http_truncated")
            self.buffer.extend(part)

    def exact(self, count):
        result = bytearray()
        while len(result) < count:
            self.connection.budget.check()
            if not self.buffer:
                self.buffer.extend(self.connection.receive())
                require(self.buffer, "drill_http_truncated")
            size = min(count - len(result), len(self.buffer))
            result.extend(self.buffer[:size])
            del self.buffer[:size]
        return bytes(result)

    def headers(self):
        result = {}
        while True:
            line = self.line(header=True)
            if line == b"\r\n":
                return result
            name, separator, value = line[:-2].partition(b":")
            require(
                separator
                and FIELD.fullmatch(name)
                and b"\r" not in value
                and b"\n" not in value,
                "drill_http_header_invalid",
            )
            name = name.lower()
            require(
                name not in result
                or name not in {b"content-length", b"transfer-encoding"},
                "drill_http_framing_invalid",
            )
            result[name] = value.strip()

    def body(self, headers):
        length, coding = (
            headers.get(b"content-length"),
            headers.get(b"transfer-encoding"),
        )
        require(
            headers.get(b"content-encoding", b"identity").lower() == b"identity",
            "drill_http_encoding_unsupported",
        )
        require(
            not (length is not None and coding is not None),
            "drill_http_framing_invalid",
        )
        if length is not None:
            require(re.fullmatch(rb"[0-9]{1,10}", length), "drill_http_framing_invalid")
            require(int(length) <= BODY_LIMIT, "drill_response_limit")
            return self.exact(int(length))
        data = bytearray()
        if coding is not None:
            require(coding.lower() == b"chunked", "drill_http_encoding_unsupported")
            while True:
                size = self.line(header=True)[:-2].split(b";", 1)[0]
                require(
                    re.fullmatch(rb"[0-9A-Fa-f]{1,8}", size),
                    "drill_http_framing_invalid",
                )
                size = int(size, 16)
                if size == 0:
                    self.headers()
                    return bytes(data)
                require(len(data) + size <= BODY_LIMIT, "drill_response_limit")
                data.extend(self.exact(size))
                require(self.exact(2) == b"\r\n", "drill_http_framing_invalid")
        data.extend(self.buffer)
        self.buffer.clear()
        while True:
            require(len(data) <= BODY_LIMIT, "drill_response_limit")
            part = self.connection.receive()
            if not part:
                return bytes(data)
            data.extend(part)


def _request(
    url,
    token,
    tls,
    budget,
    expected_status,
    *,
    method,
    service=None,
    body=None,
    return_status=False,
    authority=None,
):
    target = urlsplit(url)
    require(
        target.scheme == "https"
        and target.hostname == "127.0.0.1"
        and target.port is not None
        and not target.username
        and not target.password
        and not target.fragment,
        "drill_assertion_endpoint_forbidden",
    )
    path = target.path or "/"
    if method == "GET":
        require(service is None and body is None, "drill_request_invalid")
        path += "?" + target.query if target.query else ""
    else:
        require(
            method == "POST"
            and service is not None
            and not target.query
            and (service, target.path) in READ_ONLY_POST_ENDPOINTS,
            "drill_assertion_endpoint_forbidden",
        )
        body = encode_post_body(body)
    require(
        all(32 < ord(c) < 127 for c in path) and len(path) <= 4096,
        "drill_request_invalid",
    )
    require(
        token and all(32 < ord(c) < 127 for c in token) and len(token) <= 4096,
        "drill_token_invalid",
    )
    require(
        expected_status == "readiness"
        or (type(expected_status) is int and 200 <= expected_status <= 599),
        "drill_assertion_invalid",
    )
    # Only the already-validated Memory deployment authority may override Host.
    # The connection and TLS peer remain the explicitly pinned loopback endpoint.
    require(
        authority is None or (
            method == "POST" and service == "memory" and authority == "memory.internal:8130"
        ),
        "drill_assertion_authority_forbidden",
    )
    host = authority or f"127.0.0.1:{target.port}"
    headers = [
        f"{method} {path} HTTP/1.1",
        f"Host: {host}",
        f"Authorization: Bearer {token}",
        "Accept: application/json",
        "Accept-Encoding: identity",
    ]
    if method == "POST":
        headers.extend(
            ("Content-Type: application/json", f"Content-Length: {len(body)}")
        )
    request = ("\r\n".join(headers) + "\r\nConnection: close\r\n\r\n").encode("ascii")
    stage = "connect"
    actual_status = None
    structure = None
    try:
        with Connection(tls, target.port, budget) as connection:
            stage = "send_headers"
            connection.send(request)
            if method == "POST":
                stage = "send_body"
                connection.send(body)
            reader = Reader(connection)
            stage = "read_status"
            status = reader.line(limit=1024, header=True)
            require(
                re.fullmatch(rb"HTTP/1\.[01] [0-9]{3}(?: [^\r\n]*)?\r\n", status),
                "drill_http_status_invalid",
            )
            actual_status = int(status.split(b" ", 2)[1])
            allowed_statuses = (
                {200, 503}
                if expected_status == "readiness"
                else {expected_status}
            )
            stage = "read_headers"
            response_headers = reader.headers()
            stage = "read_body"
            data = reader.body(response_headers)
            structure = response_structure(data)
            budget.check()
            stage = "match_status"
            require(actual_status in allowed_statuses, "drill_assertion_status_mismatch")
            return (actual_status, data) if return_status else data
    except DrillDiagnosticError:
        raise
    except RecoveryError as error:
        raise DrillDiagnosticError(
            str(error), stage=stage, actual_status=actual_status,
            response_structure=structure,
        ) from None
    except ssl.SSLError:
        budget.check()
        raise DrillDiagnosticError(
            "drill_tls_handshake_failed" if stage == "connect" else "drill_tls_transport_failed",
            stage=stage, actual_status=actual_status,
        ) from None
    except OSError as error:
        budget.check()
        code = (
            "drill_connection_refused"
            if stage == "connect" and error.errno == errno.ECONNREFUSED
            else "drill_tls_or_transport_failed"
        )
        raise DrillDiagnosticError(code, stage=stage, actual_status=actual_status) from None


def get(url, token, tls, budget, expected_status):
    return _request(url, token, tls, budget, expected_status, method="GET")


def get_readiness(url, token, tls, budget):
    """Read Companion readiness, allowing its normal 503 startup state."""
    return _request(
        url,
        token,
        tls,
        budget,
        "readiness",
        method="GET",
        return_status=True,
    )


def post_readonly(url, token, service, body, tls, budget, expected_status, *, authority=None):
    return _request(
        url,
        token,
        tls,
        budget,
        expected_status,
        method="POST",
        service=service,
        body=body,
        authority=authority,
    )
