import json
import logging
from datetime import UTC, datetime
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import make_mocked_request
import pytest

from tianshu.operations.agent import incident_from_log
from tianshu.operations.incidents import safe_summary
from tianshu.runtime.api import error_middleware
from tianshu.runtime.errors import AppError
from tianshu.runtime.logging import StructuredFormatter, TaskMetadata
from tianshu.runtime.rb_logs_output import output_record
from tianshu.runtime.rb_logs_query import LogQuery
from tianshu.runtime.recovery_policy import RecoveryWait, recovery_wait_from_wire


class Events:
    def __init__(self):
        self.values = []

    async def emit(self, event, outcome, **fields):
        self.values.append({"event": event, "outcome": outcome, **fields})


@pytest.mark.parametrize(("local", "remote", "expected"), (
    (True, None, True), (True, True, True), (False, True, False),
    (True, False, False), (None, None, False),
))
async def test_http_wait_preserves_independent_rollback_evidence(local, remote, expected):
    retry_at = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    wait = RecoveryWait("source_lock_set_changed", retry_at, dependency="sources:lock_set")
    if local is not None:
        wait.transaction_rollback_confirmed = local
    wait.remote_rollback_confirmed = remote
    events = Events()
    app = web.Application()
    app["event_log"] = events
    request = make_mocked_request("POST", "/internal/v1/models/completions", app=app)

    async def handler(request):
        request["model_attempt_id"] = "original-attempt"
        request["model_lookup_phase"] = "observe"
        request["model_lookup_state"] = "unavailable"
        raise wait

    response = await error_middleware(request, handler)
    error = json.loads(response.body)["error"]
    assert response.status == 409
    assert error["code"] == error["message"] == "source_lock_set_changed" and error["request_id"]
    assert error["details"] == {"retry_at": retry_at.isoformat(), "dependency": "sources:lock_set"}
    assert error["recovery_wait"] == {"kind": "recovery_wait", "version": 1,
        "reason": "source_lock_set_changed", "retry_at": retry_at.isoformat(),
        "dependency": "sources:lock_set", "rollback_confirmed": expected}
    restored = recovery_wait_from_wire(error["recovery_wait"])
    assert restored.remote_rollback_confirmed is expected
    assert restored.retry_at == retry_at
    finished = {**events.values[-1], "event_id": "wait-1", "role": "model-broker", "level": "INFO"}
    assert finished["outcome"] == ("deferred" if expected else "failed")
    assert finished["attempt_id"] == "original-attempt"
    assert finished["lookup_phase"] == "observe" and finished["lookup_state"] == "unavailable"
    assert (incident_from_log(finished) is None) is expected


@pytest.mark.parametrize("status", (409, 503))
async def test_ordinary_error_is_not_a_typed_wait_even_with_same_code(status):
    events = Events()
    app = web.Application()
    app["event_log"] = events
    request = make_mocked_request("POST", "/internal/v1/models/completions", app=app)

    async def handler(request):
        raise AppError("source_lock_set_changed", status=status,
                       details={"retry_at": "2026-11-05T12:00:00+00:00"})

    response = await error_middleware(request, handler)
    error = json.loads(response.body)["error"]
    assert response.status == status and error["code"] == "source_lock_set_changed"
    assert "recovery_wait" not in error
    assert events.values[-1]["outcome"] == "failed"


async def test_invalid_wait_metadata_does_not_emit_retryable_envelope():
    request = make_mocked_request("POST", "/internal/v1/models/completions", app=web.Application())

    async def handler(request):
        raise RecoveryWait("invalid reason!", datetime(2026, 11, 5, 12, 0, tzinfo=UTC))

    response = await error_middleware(request, handler)
    error = json.loads(response.body)["error"]
    assert response.status == 500 and error["code"] == "invalid_recovery_wait_envelope"
    assert "recovery_wait" not in error


async def test_models_lookup_phases_preserve_expected_absence_and_original_id():
    async def invoke(phase, state, *, failed=False, unrelated=False):
        events = Events()
        app = web.Application()
        app["event_log"] = events
        request = make_mocked_request("GET", "/internal/v1/models/attempts/original-1", app=app)

        async def handler(request):
            if not unrelated:
                request["model_attempt_id"] = "original-1"
                request["model_lookup_phase"] = phase
                request["model_lookup_state"] = state
            if failed:
                raise AppError("attempt_not_found" if not unrelated else "other_not_found", status=404)
            return web.json_response({"receipt": None})

        response = await error_middleware(request, handler)
        event = {**events.values[-1], "role": "model-broker", "event_id": "event-1"}
        return response, event, incident_from_log(event)

    response, expected, incident = await invoke("pre_submit", "absent")
    assert response.status == 200 and expected["outcome"] == "succeeded"
    assert expected["attempt_id"] == "original-1" and incident is None
    response, missing, incident = await invoke("observe", "absent", failed=True)
    assert response.status == 404 and missing["dependency_ref"] == "model-broker"
    assert missing["failure_phase"] == "model_status_lookup"
    assert incident["material_id"] == "original-1"
    assert incident["recovery_target"] == {"material_id": "original-1", "kind": "model_attempt"}
    assert "receipt" not in incident  # A lost original remains unknown.
    _, other, incident = await invoke(None, None, failed=True, unrelated=True)
    assert other["outcome"] == "failed" and incident is not None
    assert "recovery_target" not in incident
    # A failed ledger lookup is not evidence that the original attempt is absent.
    _, unavailable, incident = await invoke("observe", "unavailable", failed=True)
    assert unavailable["lookup_state"] == "unavailable"
    assert incident is not None and "recovery_target" not in incident


def test_database_attribution_survives_formatter_supervisor_query_and_incident(tmp_path):
    error = RuntimeError("private exception content")
    error.database_failure = SimpleNamespace(failure_phase="connection_acquire", pool_name="business")
    record = logging.LogRecord("worker", logging.ERROR, __file__, 1,
                               "payload=private input", (), (RuntimeError, error, None))
    formatter = StructuredFormatter()
    line = formatter.format(record)
    output = output_record("bot-background", "fixture-instance", 1, line)
    assert "private" not in json.dumps(output)
    data = (json.dumps(output) + "\n").encode()
    path = tmp_path / "fixture.jsonl"
    path.write_bytes(data)
    path.with_suffix(".state.json").write_text(json.dumps({
        "instance_id": "fixture-instance", "role": "bot-background", "persisted_bytes": len(data),
        "generated_sequence": 1, "persisted_sequence": 1, "pending": 0,
        "clean_shutdown": True, "missing_count": 0,
    }), encoding="utf-8")
    entry = LogQuery(tmp_path).changes()["entries"][0]
    incident = incident_from_log(entry)
    assert incident["dependency"] == "postgresql"
    assert incident["role"] == "bot-background"
    assert incident["pool_name"] == "business"
    assert incident["failure_phase"] == "connection_acquire"
    assert safe_summary(incident)["pool_name"] == "business"
    assert "private" not in json.dumps(incident)


def test_queue_filter_keeps_database_identity_and_deferred_is_not_incident():
    error = RuntimeError("do not publish")
    error.database_failure = SimpleNamespace(failure_phase="transaction", pool_name="control")
    record = logging.LogRecord("procrastinate.worker", logging.ERROR, __file__, 1,
                               "unsafe %s", ("private arguments",), (RuntimeError, error, None))
    TaskMetadata().filter(record)
    value = json.loads(StructuredFormatter().format(record))
    assert value["pool_name"] == "control" and value["failure_phase"] == "transaction"
    assert "private" not in json.dumps(value)
    assert incident_from_log({"event": "job.deferred", "outcome": "deferred", "level": "INFO",
                              "role": "bot-background", "event_id": "deferred-1"}) is None
