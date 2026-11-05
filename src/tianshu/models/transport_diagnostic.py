"""Bounded transport facts; never serialize exception text or full URLs."""

import asyncio
import ipaddress
import re
import ssl
from urllib.parse import urlsplit

import aiohttp

from tianshu.runtime.errors import AppError


_CONNECTOR_CLASSES = frozenset({
    aiohttp.ClientConnectorError,
    aiohttp.ClientConnectorDNSError,
    aiohttp.ClientConnectorSSLError,
    aiohttp.ClientConnectorCertificateError,
})
_ENDPOINT_CLASSES = _CONNECTOR_CLASSES | frozenset({aiohttp.ClientProxyConnectionError})
_TLS_CLASSES = frozenset({aiohttp.ClientConnectorSSLError, aiohttp.ClientConnectorCertificateError,
                          aiohttp.ServerFingerprintMismatch})
_KNOWN_ERROR_CLASSES = _CONNECTOR_CLASSES | frozenset({
    aiohttp.ClientConnectionError, aiohttp.ClientOSError, aiohttp.ServerDisconnectedError,
    aiohttp.ClientPayloadError, aiohttp.ClientResponseError, aiohttp.ServerFingerprintMismatch,
    aiohttp.ClientProxyConnectionError, aiohttp.InvalidURL,
    aiohttp.ConnectionTimeoutError, aiohttp.SocketTimeoutError, TimeoutError, OSError,
    ConnectionResetError, ConnectionRefusedError, BrokenPipeError, ssl.SSLError,
    asyncio.CancelledError, GeneratorExit, AppError,
})
_TLS_REASONS = frozenset({
    "CERTIFICATE_VERIFY_FAILED", "WRONG_VERSION_NUMBER", "UNKNOWN_PROTOCOL", "UNSUPPORTED_PROTOCOL",
    "UNEXPECTED_EOF_WHILE_READING", "SSLV3_ALERT_HANDSHAKE_FAILURE", "TLSV1_ALERT_PROTOCOL_VERSION",
    "TLSV1_ALERT_UNKNOWN_CA", "TLSV1_ALERT_INTERNAL_ERROR", "NO_SHARED_CIPHER",
})
_DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")


def _safe_host(host):
    if not isinstance(host, str) or not host or len(host) > 1024 or "%" in host:
        return None
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        try:
            value = host.rstrip(".").encode("idna").decode("ascii").lower()
        except UnicodeError:
            return None
        if not value or len(value) > 253 or any(not _DNS_LABEL.fullmatch(label) for label in value.split(".")):
            return None
        return value


def _endpoint(url, error):
    if not isinstance(url, str) or len(url) > 8192:
        return None, None
    try:
        parsed = urlsplit(url)
        host = _safe_host(parsed.hostname)
        port = parsed.port
        if port is None:
            port = {"http": 80, "https": 443}.get(parsed.scheme.lower())
        if type(port) is not int or not 1 <= port <= 65535:
            port = None
        if type(error) in _ENDPOINT_CLASSES:
            actual_host = _safe_host(error.host)
            actual_port = error.port
            if actual_host is not None:
                if type(actual_port) is not int or not 1 <= actual_port <= 65535:
                    actual_port = port if actual_host == host else None
                return actual_host, actual_port
        if host is None:
            return None, None
        return host, port
    except (TypeError, ValueError, UnicodeError, AttributeError):
        return None, None


def _errno_and_tls_reason(error):
    if type(error) not in _KNOWN_ERROR_CLASSES:
        return None, None
    try:
        original = error.os_error if type(error) in _CONNECTOR_CLASSES else error
        number = getattr(original, "errno", None)
        reason = getattr(original, "reason", None) if type(error) in _TLS_CLASSES else None
    except Exception:
        return None, None
    if type(number) is not int or not -(2**31) <= number < 2**31:
        number = None
    if not isinstance(reason, str) or reason not in _TLS_REASONS:
        reason = None
    return number, reason


def describe_transport(error, timing, url, *, output_observed=False, provider_handle=None, first_output_ms=None):
    """Describe this request's failure; only observed connector failures prove no send."""
    points = timing.points
    observed = any(points.get(name) is not None for name in (
        "headers_sent", "body_sent", "response_headers", "first_body_byte", "first_valid_output", "read_complete"))
    connector = type(error) in _CONNECTOR_CLASSES
    connecting = (points.get("connect_start") is not None and points.get("connect_end") is None
                  and timing.connection_reused is False)
    confirmed = (connector and timing.http_phase == "request" and connecting and timing.send_signals_available
                 and not observed and not output_observed and provider_handle is None and first_output_ms is None)

    if timing.http_phase == "finished":
        phase = "unknown"
    elif timing.http_phase == "close":
        phase = "close"
    elif timing.http_phase == "response" or points.get("response_headers") is not None:
        phase = "response"
    elif type(error) in _TLS_CLASSES and connecting:
        phase = "tls"
    elif type(error) is aiohttp.ClientConnectorDNSError and connecting:
        phase = "dns"
    elif connecting:
        phase = "connect"
    else:
        phase = "request" if timing.http_phase == "request" else "unknown"

    host, port = _endpoint(url, error)
    number, tls_reason = _errno_and_tls_reason(error)
    return {"schema_version": "rbh.transport.v1", "phase": phase,
            "error_class": type(error).__name__ if type(error) in _KNOWN_ERROR_CLASSES else "OtherError",
            "os_errno": number, "tls_reason": tls_reason, "host": host, "port": port,
            "headers_sent": True if points.get("headers_sent") is not None else False if confirmed else None,
            "body_sent": True if points.get("body_sent") is not None else False if confirmed else None,
            "evidence": "confirmed_not_sent" if confirmed else "sent_or_unknown"}
