"""Conservative interpretation of Models' original, authority-owned receipts."""

from collections.abc import Mapping
from datetime import datetime
import math
import re

from tianshu.models.types import ModelReceipt


# These are the concrete names emitted for aiohttp's connection-establishment
# exceptions, not error messages and not post-send disconnect exceptions.
_CONNECTOR_PHASES = {
    "ClientConnectorError": "connect", "ClientConnectorDNSError": "dns",
    "ClientConnectorSSLError": "tls", "ClientConnectorCertificateError": "tls",
}
_ABSENT_POINTS = (
    "connect_end", "headers_sent", "body_sent", "response_headers",
    "first_body_byte", "first_valid_output", "read_complete",
)


def _no_send_timing(receipt):
    timing = receipt.http_timing
    if not isinstance(timing, Mapping) or timing.get("connection_reused") is not False:
        return False
    points = timing.get("points_ms")
    if not isinstance(points, Mapping) or not all(key in points for key in ("connect_start", *_ABSENT_POINTS)):
        return False
    start = points["connect_start"]
    return (type(start) in (int, float) and 0 <= start <= 10**12 and math.isfinite(start)
            and all(points[key] is None for key in _ABSENT_POINTS))


def _safe_transport(value):
    if not isinstance(value, Mapping):
        return False
    required = {"schema_version", "phase", "error_class", "os_errno", "tls_reason", "host", "port",
                "headers_sent", "body_sent", "evidence"}
    if not required <= value.keys():
        return False
    errno, reason, host, port = (value[key] for key in ("os_errno", "tls_reason", "host", "port"))
    return (value["schema_version"] == "rbh.transport.v1"
            and isinstance(value["error_class"], str) and value["error_class"] in _CONNECTOR_PHASES
            and value["phase"] == _CONNECTOR_PHASES[value["error_class"]]
            and value["evidence"] == "confirmed_not_sent"
            and value["headers_sent"] is False and value["body_sent"] is False
            and (errno is None or type(errno) is int and -(2**31) <= errno < 2**31)
            and (reason is None or isinstance(reason, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", reason))
            and (host is None or isinstance(host, str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,253}", host))
            and (port is None or type(port) is int and 1 <= port <= 65535))


def confirmed_not_sent(receipt):
    """Return a safe evidence kind, or None; missing evidence never grants credit."""
    if (not isinstance(receipt, ModelReceipt) or receipt.status != "failed"
            or receipt.dispatch != "not_started" or receipt.error_code != "transport_not_sent"
            or not isinstance(receipt.finished_at, datetime) or receipt.finished_at.utcoffset() is None
            or receipt.cancellation_reason is not None or receipt.first_output_ms is not None
            or receipt.provider_handle is not None or not _no_send_timing(receipt)):
        return None
    diagnostic = receipt.diagnostic
    if (not isinstance(diagnostic, Mapping) or diagnostic.get("category") != "not_sent"
            or type(diagnostic.get("part_count")) is not int or diagnostic["part_count"] != 0
            or diagnostic.get("text_bytes", 0) != 0 or diagnostic.get("part_kinds", []) != []
            or diagnostic.get("native_completed", False) is not False):
        return None
    coverage = receipt.stream_coverage
    if coverage is not None and (not isinstance(coverage, Mapping)
            or coverage.get("recorded_events", 0) != 0 or coverage.get("upstream_complete", False) is not False):
        return None
    if "transport" not in diagnostic:
        return "legacy"
    return "rbh.transport.v1" if _safe_transport(diagnostic["transport"]) else None
