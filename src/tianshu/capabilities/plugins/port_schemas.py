"""Machine contracts for the operations this host actually provides."""

from .control import OBJECT, TEXT, request
from ..content_discovery.schemas import INPUT as DISCOVERY_INPUT, OUTPUT as DISCOVERY_OUTPUT
from ..media.comics.plugin import EMPTY as COMIC_EMPTY, STATUS as COMIC_STATUS

NULL_REVISION = {"type": ["integer", "null"], "minimum": 1}
VALUE = {
    "type": "object",
    "required": ["value", "revision"],
    "properties": {"value": {}, "revision": {"type": "integer", "minimum": 1}},
    "additionalProperties": False,
}
RECEIPT = {"type": "object", "required": ["id", "execution", "effect", "delivery", "revision"]}
OPERATIONS = {
    **{
        ("comics.files", operation): (COMIC_EMPTY, COMIC_STATUS)
        for operation in ("start", "stop", "status")
    },
    ("content.discovery", "discover"): (DISCOVERY_INPUT, DISCOVERY_OUTPUT),
    ("state.kv", "get"): (request({"key": TEXT}), {"oneOf": [VALUE, {"type": "null"}]}),
    ("state.kv", "compare_and_set"): (
        request({"key": TEXT, "value": {}, "expected_revision": NULL_REVISION}),
        VALUE,
    ),
    ("state.kv", "delete"): (
        request({"key": TEXT, "expected_revision": {"type": "integer", "minimum": 1}}),
        request({"deleted": {"type": "boolean"}}),
    ),
    ("http.client", "request"): (
        request(
            {
                "method": {"enum": ["GET", "HEAD", "POST", "PUT", "PATCH", 'DELETE']},
                "url": {"type": "string", "format": "uri", "maxLength": 4096},
                "headers": {"type": "object", "additionalProperties": {"type": "string"}},
                "body": {},
                "timeout_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 60},
                "max_response_bytes": {"type": "integer", "minimum": 1, "maximum": 786432},
                "auth": request(
                    {"secret_name": TEXT, "scheme": {"enum": ["bearer", "x-api-key"]}},
                    optional=("scheme",),
                ),
            },
            optional=("method", "headers", "body", "timeout_seconds", "max_response_bytes", "auth"),
        ),
        request(
            {
                "status": {"type": "integer", "minimum": 100, "maximum": 599},
                "headers": OBJECT,
                "body": {},
            }
        ),
    ),
    ("secrets.read", "resolve"): (
        request({"name": TEXT}),
        request({"secret_ref": TEXT, "configured": {"const": True}}),
    ),
    ("jobs.manage", "submit"): (
        request({"action": TEXT, "input": OBJECT, "intent_key": TEXT}),
        request({"invocation_id": TEXT}),
    ),
    ("jobs.manage", "get"): (request({"invocation_id": TEXT}), RECEIPT),
    ("jobs.manage", "cancel"): (request({"invocation_id": TEXT}), RECEIPT),
    ("jobs.manage", "checkpoint"): (
        request(
            {"state": OBJECT, "expected_revision": NULL_REVISION}, optional=("expected_revision",)
        ),
        request({"checkpoint_ref": TEXT, "revision": {"type": "integer", "minimum": 1}}),
    ),
    ("jobs.manage", "schedule.put"): (
        request(
            {
                "schedule_id": TEXT,
                "action": TEXT,
                "input": OBJECT,
                "trigger": OBJECT,
                "expected_revision": NULL_REVISION,
            }
        ),
        request(
            {"schedule_revision": {"type": "integer", "minimum": 1}, "removed": {"type": "boolean"}}
        ),
    ),
    ("jobs.manage", "schedule.remove"): (
        request({"schedule_id": TEXT, "expected_revision": {"type": "integer", "minimum": 1}}),
        request(
            {"schedule_revision": {"type": "integer", "minimum": 1}, "removed": {"type": "boolean"}}
        ),
    ),
    ("notifications.emit", "publish"): (
        request(
            {
                "dedup_key": TEXT,
                "title": {"type": "string", "maxLength": 256},
                "body": {"type": "string", "maxLength": 4096},
                "severity": {"enum": ["info", "warning", "error"]},
                "invocation_id": TEXT,
            },
            optional=("severity", "invocation_id"),
        ),
        request({"notification_ref": TEXT, "delivery": {"const": "delivered"}}),
    ),
}

OPERATIONS.update({
    ("models.generate","generate"):(request({"request_key":TEXT,"function":TEXT,"messages":{"type":"array","minItems":1,"maxItems":32},"system":{"type":"string","maxLength":32000},"required_capabilities":{"type":"array","items":{"type":"string"}},"max_output_tokens":{"type":"integer","minimum":1,"maximum":8192}},optional=("system","required_capabilities","max_output_tokens")),OBJECT),
    ("files.store","put"):(request({"data_base64":{"type":"string","maxLength":699052},"media_type":TEXT}),OBJECT),
    ("files.store","get"):(request({"media":OBJECT}),OBJECT),
    ("sources.read","read"):(request({"source":OBJECT,"requested":OBJECT},optional=("requested",)),OBJECT),
    ("messages.present","present"):(request({"expression_key":TEXT,"public_parts":{"type":"array","maxItems":32},"artifacts":{"type":"array","maxItems":16}},optional=("artifacts",)),OBJECT),
})


def describe():
    return [
        {
            "capability": key[0],
            "major": 1,
            "operation": key[1],
            "input_schema": value[0],
            "output_schema": value[1],
        }
        for key, value in OPERATIONS.items()
    ]
