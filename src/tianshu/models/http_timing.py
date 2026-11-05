"""Observable client-side HTTP phases, without guessing provider compute time."""

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import time

import aiohttp


class HttpTiming:
    def __init__(self, queued_at):
        self.origin = time.monotonic()
        self.origin_at = datetime.now(UTC)
        self.queued_at = queued_at
        self.points = {}
        self.connection_reused = None
        self.send_signals_available = False
        self.http_phase = "unknown"

    def mark(self, name):
        self.points.setdefault(name, round((time.monotonic() - self.origin) * 1000, 3))

    def record(self):
        names = ("scheduler_admitted", "local_slot_wait_start", "local_slot_wait_end", "execution_started",
                 "request_start", "pool_wait_start", "pool_wait_end", "dns_start", "dns_end",
                 "connect_start", "connect_end", "headers_sent", "body_sent", "response_headers",
                 "first_body_byte", "first_valid_output", "read_complete")
        return {
            "origin_at": self.origin_at.isoformat(),
            "queued_at": self.queued_at.isoformat() if self.queued_at else None,
            "connection_reused": self.connection_reused,
            "points_ms": {name: self.points.get(name) for name in names},
            "notes": {"dns_nested_in_connection": True, "tcp_tls_separable": False,
                      "provider_queue_and_compute": "unknown"},
        }


def trace_config():
    trace = aiohttp.TraceConfig()
    send_signals_available = all(getattr(trace, name, None) is not None for name in
                                 ("on_request_headers_sent", "on_request_chunk_sent"))

    def callback(point, *, reuse=None):
        async def record(_session, context, _params):
            timing = context.trace_request_ctx
            if not isinstance(timing, HttpTiming):
                return
            if reuse is not None:
                timing.connection_reused = reuse
            if point == "connect_start":
                timing.send_signals_available = send_signals_available
            timing.mark(point)
        return record

    for signal, point, reuse in (
        ("on_request_start", "request_start", None),
        ("on_connection_queued_start", "pool_wait_start", None),
        ("on_connection_queued_end", "pool_wait_end", None),
        ("on_dns_resolvehost_start", "dns_start", None),
        ("on_dns_resolvehost_end", "dns_end", None),
        ("on_connection_create_start", "connect_start", False),
        ("on_connection_create_end", "connect_end", None),
        ("on_connection_reuseconn", "request_start", True),
        ("on_request_headers_sent", "headers_sent", None),
        ("on_request_chunk_sent", "body_sent", None),
    ):
        event = getattr(trace, signal, None)
        if event is not None:
            event.append(callback(point, reuse=reuse))
    return trace


class TimedResponse:
    """Distinguish a failed request/body from an error closing its context."""

    def __init__(self, context, timing):
        self.context, self.timing = context, timing

    async def __aenter__(self):
        self.timing.http_phase = "request"
        response = await self.context.__aenter__()
        self.timing.http_phase = "response"
        return response

    async def __aexit__(self, exc_type, exc, traceback):
        try:
            return await self.context.__aexit__(exc_type, exc, traceback)
        except BaseException as close_error:
            if close_error is not exc:
                self.timing.http_phase = "close"
            raise


def retry_after(value, *, now=None):
    """Return the upstream delay as an absolute deadline, or None if invalid."""
    if not value:
        return None
    now = now or datetime.now(UTC)
    try:
        from datetime import timedelta
        seconds = int(value.strip())
        return now + timedelta(seconds=max(0, seconds))
    except (ValueError, OverflowError):
        try:
            deadline = parsedate_to_datetime(value)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=UTC)
            return max(now, deadline.astimezone(UTC))
        except (ValueError, TypeError, OverflowError):
            return None
