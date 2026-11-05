"""Safe structured representation of Python and supervised process output."""

from datetime import UTC, datetime
import json
import re
from urllib.parse import unquote, urlsplit
from uuid import uuid4


LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "UNCLASSIFIED"}
SAFE_FIELDS = ("trace_id", "correlation_id", "job_id", "attempt_id", "error_code",
               "error_family", "dependency_ref", "task_name", "connection_id", "stack",
               "lookup_phase", "lookup_state", "failure_phase", "pool_name")
_SENSITIVE_KEY = re.compile(
    r"(?i)([\"']?(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"password|passwd|secret|credential|prompt|messages|body|payload|content|"
    r"arguments|kwargs|source_text)[\"']?\s*[:=]\s*)"
    r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;]+)"
)
_CONTENT_START = re.compile(
    r"(?i)([\"']?(?:prompt|messages|body|payload|content|arguments|kwargs|source_text)"
    r"[\"']?\s*[:=]\s*).*"
)


def secret_values(environ):
    values = set()
    for name, value in environ.items():
        if not isinstance(value, str) or not re.search(r"KEY|TOKEN|SECRET|PASSWORD|DSN|DATABASE_URL", name, re.I):
            continue
        if len(value) >= 4:
            values.add(value)
        if re.search(r"DSN|DATABASE_URL", name, re.I):
            try:
                password = urlsplit(value).password
                if password and len(unquote(password)) >= 4:
                    values.add(unquote(password))
            except ValueError:
                pass
            match = re.search(r"(?:^|\s)password=(?:'([^']*)'|(\S+))", value, re.I)
            if match and len(match.group(1) or match.group(2) or "") >= 4:
                values.add(match.group(1) or match.group(2))
    return tuple(sorted(values, key=len, reverse=True))


def redact_text(text, secrets=()):
    text = str(text)
    for value in sorted((str(item) for item in secrets if item), key=len, reverse=True):
        text = text.replace(value, "[redacted]")
    text = re.sub(r"(?i)(Bearer\s+)[^\s\"']+", r"\1[redacted]", text)
    text = re.sub(r"(?i)(https?://)[^/@\s]+:[^/@\s]+@", r"\1[redacted]@", text)
    text = _CONTENT_START.sub(r"\1[redacted]", text)
    return _SENSITIVE_KEY.sub(r"\1[redacted]", text)


def _safe_value(value, secrets, limit=1024):
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        return None
    return redact_text(value[:limit], secrets)


def output_record(role, instance_id, sequence, message, secrets=()):
    """Return one redacted schema-2 record; unrecognized levels remain unknown.

    Python's formatter emits a single JSON line, including traceback frames in a
    field. Non-Python processes may instead emit plain text or their own JSON.
    """
    if not isinstance(message, str):
        message = "[non-text output redacted]"
    if len(message) > 1024 * 1024:
        message = "[oversize output line redacted]"
    try:
        parsed = json.loads(message)
    except (TypeError, ValueError):
        parsed = None
    parsed = parsed if isinstance(parsed, dict) else {}
    raw_level = parsed.get("level") or parsed.get("levelname") or parsed.get("severity")
    level = str(raw_level).upper() if raw_level is not None else "UNCLASSIFIED"
    if level == "WARN":
        level = "WARNING"
    if level not in LEVELS:
        level = "UNCLASSIFIED"
    raw_message = parsed.get("message", "[structured output without safe message]") if parsed else message
    if not isinstance(raw_message, str):
        raw_message = "[non-text output redacted]"
    raw_module = parsed.get("module") or parsed.get("logger") or parsed.get("name")
    value = {
        "schema_version": "2.0.0", "timestamp": datetime.now(UTC).isoformat(),
        "service": "tianshu", "role": role, "instance_id": instance_id,
        "sequence": sequence, "event_id": str(uuid4()), "event": "process.output",
        "outcome": "unknown", "level": level,
        "module": _safe_value(raw_module, secrets, 128),
        "message": redact_text(raw_message, secrets),
    }
    for field in SAFE_FIELDS:
        if field in parsed:
            value[field] = _safe_value(parsed[field], secrets)
    return value
