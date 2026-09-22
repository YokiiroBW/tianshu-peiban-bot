"""One-thread nonblocking TLS/HTTP; every IO and parser step shares one budget.

No DNS, proxy, redirects, retry/replay or background workers. Closing the owned
socket never waits for a peer TLS close_notify. Limits include chunk framing.
"""

import errno
import re
import select
import socket
import ssl
import time
from urllib.parse import urlsplit

from .safety import RecoveryError, require

HEADER_LIMIT = 32 * 1024
BODY_LIMIT = 256 * 1024
FIELD = re.compile(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


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
                        require(
                            self.socket.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                            == 0,
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


def get(url, token, tls, budget, expected_status):
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
    path = (target.path or "/") + ("?" + target.query if target.query else "")
    require(
        all(32 < ord(c) < 127 for c in path) and len(path) <= 4096,
        "drill_request_invalid",
    )
    require(
        token and all(32 < ord(c) < 127 for c in token) and len(token) <= 4096,
        "drill_token_invalid",
    )
    request = (
        f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{target.port}\r\nAuthorization: Bearer {token}\r\n"
        "Accept: application/json\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n"
    ).encode("ascii")
    try:
        with Connection(tls, target.port, budget) as connection:
            connection.send(request)
            reader = Reader(connection)
            status = reader.line(limit=1024, header=True)
            require(
                re.fullmatch(rb"HTTP/1\.[01] [0-9]{3}(?: [^\r\n]*)?\r\n", status),
                "drill_http_status_invalid",
            )
            require(
                int(status.split(b" ", 2)[1]) == expected_status,
                "drill_assertion_status_mismatch",
            )
            data = reader.body(reader.headers())
            budget.check()
            return data
    except OSError:
        budget.check()
        raise RecoveryError("drill_tls_or_transport_failed") from None
