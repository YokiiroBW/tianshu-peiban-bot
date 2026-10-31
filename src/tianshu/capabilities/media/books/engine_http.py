"""Network port used only inside the isolated upstream rule worker."""

import json
import threading
import time

from ...contracts import Fault, require
from .network import Network, origin

network = Network()
request_count = 0
request_bytes = 0
lock = threading.Lock()
rate_limit = 0
rate_period = 0
rate_started_at = 0
rate_requests = 0


def configure_rate(value, state):
    """Reader3 concurrentRate is an interval in ms, or requests/interval in ms."""
    global rate_limit, rate_period, rate_started_at, rate_requests
    rate_limit, rate_period = 0, 0
    try:
        parts = str(value or "0").split("/")
        limit, milliseconds = (1, int(parts[0])) if len(parts) == 1 else map(int, parts)
        if limit > 0 and milliseconds > 0:
            rate_limit, rate_period = limit, milliseconds / 1000
    except (ValueError, TypeError):
        pass
    rate_started_at = state.get("started_at", 0)
    rate_requests = state.get("requests", 0)


def rate_state():
    return {"started_at": rate_started_at, "requests": rate_requests}


def before_request():
    """Pace every HTTP request, including script calls and redirects."""
    global request_count, rate_started_at, rate_requests
    with lock:
        request_count += 1
        require(request_count <= 64, "book_request_limit", 413)
        if not rate_limit:
            return
        now = time.monotonic()
        while rate_requests >= rate_limit and now < rate_started_at + rate_period:
            time.sleep(max(0, rate_started_at + rate_period - now))
            now = time.monotonic()
        if now >= rate_started_at + rate_period:
            rate_started_at, rate_requests = now, 0
        rate_requests += 1


def scoped_headers(headers, source_url, target_url):
    """Inherited source credentials stay on their original HTTP origin."""
    try:
        if origin(source_url) == origin(target_url):
            return headers
    except Fault:
        pass
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in {"authorization", "cookie"}
    }


def req(
    url,
    cellphone_mode=False,
    cookies="",
    header=None,
    method=0,
    post_data_type=1,
    post_data="",
    post_json=None,
    return_cookies="",
    proxy="",
    proxy_type="",
    timeout=10,
    file_name="",
    file_obj=None,
    allow_redirects=True,
):
    del cellphone_mode, proxy_type, timeout, allow_redirects
    global request_bytes
    require(not file_name and not proxy, "book_rule_capability_unsupported", 400)
    headers = dict(header or {})
    if cookies:
        headers["Cookie"] = cookies
    body = post_data if method == 1 else None
    if method == 1 and post_data_type == 2:
        body = json.dumps(post_json or {}).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if isinstance(body, str):
        body = body.encode("utf-8")
    response = network.fetch(
        url, method="POST" if method == 1 else "GET", headers=headers, body=body
    )
    with lock:
        request_bytes += len(response.content)
        require(request_bytes <= 32 * 1024 * 1024, "book_response_too_large", 413)
    if file_obj is not None:
        file_obj.write(response.content)
        return None
    return response.text, return_cookies, response
