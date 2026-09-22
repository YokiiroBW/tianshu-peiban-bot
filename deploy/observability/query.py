"""Bounded Loki queries. Saturated timestamp buckets are errors, never false completeness."""

import json
import ssl
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener


class QueryError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise QueryError("redirect_refused")


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
        self.token = token
        context = ssl.create_default_context(cafile=str(ca))
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        if certificate:
            context.load_cert_chain(str(certificate), str(key))
        self.opener = build_opener(HTTPSHandler(context=context), NoRedirect())

    def request(self, path, method="GET", body=None, content_type=None, encoding=None):
        headers = {"X-Scope-OrgID": "tianshu"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if content_type:
            headers["Content-Type"] = content_type
        if encoding:
            headers["Content-Encoding"] = encoding
        request = Request(self.url + path, data=body, method=method, headers=headers)
        try:
            response = self.opener.open(request, timeout=10)
        except HTTPError as exc:
            response = exc
        except Exception:
            raise QueryError("backend_unavailable") from None
        with response:
            data = response.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                raise QueryError("query_response_over_budget")
            return (
                response.status,
                data,
                response.headers.get("Content-Type", "application/json"),
            )

    def range(self, selector, start_ns, end_ns, limit=1000, max_requests=128):
        todo, rows, requests = [(start_ns, end_ns)], [], 0
        while todo:
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
            status, body, _ = self.request("/loki/api/v1/query_range?" + params)
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
        return sorted(rows)
