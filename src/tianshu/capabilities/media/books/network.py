"""Bounded book-source HTTP with verified TLS and pinned resolved addresses."""

import gzip
import http.client
import io
import ipaddress
import re
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import quote, urljoin, urlsplit

from ...contracts import Fault, require

MAX_RESPONSE = 8 * 1024 * 1024


def origin(url):
    require(isinstance(url, str), "book_invalid_url", 400)
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise Fault("book_invalid_url", 400) from None
    require(parsed.scheme in {"http", "https"} and parsed.hostname, "book_invalid_url", 400)
    require(not parsed.username and not parsed.password, "book_invalid_url", 400)
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        raise Fault("book_invalid_url", 400) from None
    return parsed.scheme, parsed.hostname.lower(), port


@dataclass
class Response:
    content: bytes
    url: str
    headers: dict
    history: tuple
    status_code: int = 200

    @property
    def text(self):
        content_type = self.headers.get("content-type", "")
        match = re.search(r"charset\s*=\s*[\"']?([\w-]+)", content_type, re.I)
        if not match:
            match = re.search(
                r"charset\s*=\s*[\"']?([\w-]+)", self.content[:2048].decode("ascii", "ignore"), re.I
            )
        encoding = match[1] if match else "utf-8-sig"
        if encoding.lower() in {"gbk", "gb2312"}:
            encoding = "gb18030"
        try:
            return self.content.decode(encoding)
        except (UnicodeError, LookupError):
            try:
                return self.content.decode("gb18030")
            except UnicodeError:
                return self.content.decode("utf-8", "replace")


class PinnedConnection(http.client.HTTPConnection):
    def __init__(self, host, port, address, secure, timeout):
        super().__init__(host, port, timeout=timeout)
        self.address, self.secure = address, secure

    def connect(self):
        stream = socket.create_connection((self.address, self.port), self.timeout)
        if self.secure:
            stream = ssl.create_default_context().wrap_socket(stream, server_hostname=self.host)
        self.sock = stream


class Network:
    def __init__(
        self, *, allowed_private_origins=(), timeout=12, max_bytes=MAX_RESPONSE, before_request=None
    ):
        self.allowed_private_origins = {origin(url) for url in allowed_private_origins}
        self.timeout, self.max_bytes = timeout, max_bytes
        self.before_request = before_request

    def fetch(self, url, *, method="GET", headers=None, body=None, max_bytes=None, timeout=None):
        max_bytes = self.max_bytes if max_bytes is None else max_bytes
        timeout = self.timeout if timeout is None else timeout
        require(isinstance(url, str) and len(url) <= 16384, "book_invalid_url", 400)
        require(method in {"GET", "POST", "HEAD"}, "book_request_unsupported", 400)
        current = url
        history = []
        supplied = dict(headers or {})
        for _ in range(6):
            scheme, host, port = origin(current)
            require(port > 0, "book_invalid_url", 400)
            try:
                addresses = list(
                    dict.fromkeys(
                        row[4][0] for row in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
                    )
                )
            except OSError:
                raise Fault("book_source_unavailable", 502) from None
            require(addresses, "book_source_unavailable", 502)
            if (scheme, host, port) not in self.allowed_private_origins:
                require(
                    all(ipaddress.ip_address(address).is_global for address in addresses),
                    "book_source_address_denied",
                    400,
                )
            request_headers = {"User-Agent": "TianshuBooks/1.0", "Accept-Encoding": "identity"}
            for key, value in supplied.items():
                if key.lower() not in {
                    "host",
                    "connection",
                    "content-length",
                    "transfer-encoding",
                    "proxy-authorization",
                    "accept-encoding",
                }:
                    require(
                        isinstance(value, str) and "\r" not in value and "\n" not in value,
                        "book_invalid_headers",
                        400,
                    )
                    request_headers[key] = value
            parsed = urlsplit(current)
            target = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
            if parsed.query:
                target += "?" + quote(parsed.query, safe="=&%+;:/?@,!$'()*-._~")
            connection = PinnedConnection(host, port, addresses[0], scheme == "https", timeout)
            try:
                if self.before_request:
                    self.before_request()
                connection.request(method, target, body=body, headers=request_headers)
                response = connection.getresponse()
                response_headers = {key.lower(): value for key, value in response.getheaders()}
                if response.status in {301, 302, 303, 307, 308}:
                    location = response_headers.get("location")
                    require(location, "book_source_unavailable", 502)
                    next_url = urljoin(current, location)
                    if origin(next_url) != origin(current):
                        supplied = {
                            key: value
                            for key, value in supplied.items()
                            if key.lower() not in {"authorization", "cookie"}
                        }
                    history.append(current)
                    current = next_url
                    if response.status == 303 or response.status in {301, 302} and method == "POST":
                        method, body = "GET", None
                    continue
                if response.status in {401, 403}:
                    raise Fault("book_source_auth_required", 409)
                if response.status == 429:
                    raise Fault("book_source_rate_limited", 429)
                require(200 <= response.status < 300, "book_source_unavailable", 502)
                content = response.read(max_bytes + 1)
                require(len(content) <= max_bytes, "book_response_too_large", 413)
                if response_headers.get("content-encoding", "").lower() == "gzip":
                    with gzip.GzipFile(fileobj=io.BytesIO(content)) as compressed:
                        content = compressed.read(max_bytes + 1)
                    require(len(content) <= max_bytes, "book_response_too_large", 413)
                if re.search(r"/(?:login|signin)(?:[./]|$)", parsed.path, re.I) and re.search(
                    rb"type\s*=\s*['\"]?password\b", content, re.I
                ):
                    raise Fault("book_source_auth_required", 409)
                return Response(content, current, response_headers, tuple(history), response.status)
            except (OSError, http.client.HTTPException, ValueError):
                raise Fault("book_source_unavailable", 502) from None
            finally:
                connection.close()
        raise Fault("book_too_many_redirects", 502)
