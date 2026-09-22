"""Bounded Loki queries. Saturated timestamp buckets are errors, never false completeness."""

import json
from http.client import HTTPConnection
import ssl
import threading
from urllib.parse import urlencode, urlsplit

from transport import Deadline, DeadlineExceeded, connect_tls


class QueryError(RuntimeError):
    pass


class LokiClient:
    def __init__(self, url, ca, token=None, certificate=None, key=None):
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.query
            or parts.fragment
            or parts.path not in ("", "/")
        ):
            raise ValueError("explicit_https_origin_required")
        self.url = url.rstrip("/")
        self.host, self.port = parts.hostname, parts.port or 443
        self.token = token
        context = ssl.create_default_context(cafile=str(ca))
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        if certificate:
            context.load_cert_chain(str(certificate), str(key))
        self.context = context
        self.closed = threading.Event()
        self.lock = threading.Lock()
        self.active = set()

    def cancel(self):
        """Signal cancellation without disposing resources owned by active callers."""
        self.closed.set()

    def close(self):
        self.cancel()
        with self.lock:
            connections = list(self.active)
        for connection in connections:
            connection.abort()

    def request(
        self,
        path,
        method="GET",
        body=None,
        content_type=None,
        encoding=None,
        *,
        deadline=None,
    ):
        deadline = Deadline(parent=deadline, cancel=self.closed)
        headers = {"X-Scope-OrgID": "tianshu"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if content_type:
            headers["Content-Type"] = content_type
        if encoding:
            headers["Content-Encoding"] = encoding
        connection, http = None, None
        try:
            connection = connect_tls(self.host, self.port, self.context, deadline)
            with self.lock:
                self.active.add(connection)
            deadline.remaining()
            # DNS, connect and TLS already completed using nonblocking deadline I/O.
            # HTTPConnection is only the framing/parser; it never resolves/connects here.
            http = HTTPConnection(self.host, self.port)
            http.sock = connection
            http.request(method, path, body=body, headers=headers)
            with http.getresponse() as response:
                data = response.read(8 * 1024 * 1024 + 1)
                deadline.remaining()
                if len(data) > 8 * 1024 * 1024:
                    raise QueryError("query_response_over_budget")
                if 300 <= response.status < 400:
                    raise QueryError("redirect_refused")
                return (
                    response.status,
                    data,
                    response.headers.get("Content-Type", "application/json"),
                )
        except DeadlineExceeded:
            raise QueryError("query_deadline_exceeded") from None
        except QueryError:
            raise
        except Exception:
            raise QueryError("backend_unavailable") from None
        finally:
            if http:
                http.close()
            if connection:
                connection.abort()
                with self.lock:
                    self.active.discard(connection)

    def range(
        self, selector, start_ns, end_ns, limit=1000, max_requests=128, *, deadline=None
    ):
        deadline = Deadline(parent=deadline)
        todo, rows, requests = [(start_ns, end_ns)], [], 0
        while todo:
            try:
                deadline.remaining()
            except DeadlineExceeded:
                raise QueryError("query_deadline_exceeded") from None
            start, end = todo.pop()
            requests += 1
            if requests > max_requests:
                raise QueryError("query_request_budget_exceeded")
            params = urlencode(
                {
                    "query": selector,
                    "start": str(start),
                    "end": str(end),
                    "direction": "forward",
                    "limit": limit,
                }
            )
            status, body, _ = self.request(
                "/loki/api/v1/query_range?" + params, deadline=deadline
            )
            if status != 200:
                raise QueryError("query_failed")
            try:
                result = json.loads(body)
                if (
                    result["status"] != "success"
                    or result["data"]["resultType"] != "streams"
                ):
                    raise ValueError()
                values = [
                    (int(t), line)
                    for stream in result["data"]["result"]
                    for t, line in stream["values"]
                ]
                if any(
                    t < start or t > end or not isinstance(line, str)
                    for t, line in values
                ):
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                raise QueryError("invalid_query_response") from None
            if len(values) >= limit:
                if start >= end:
                    raise QueryError("timestamp_bucket_saturated")
                mid = (start + end) // 2
                todo.extend([(mid + 1, end), (start, mid)])
            else:
                rows.extend(values)
        result = sorted(rows)
        try:
            deadline.remaining()
        except DeadlineExceeded:
            raise QueryError("query_deadline_exceeded") from None
        return result
