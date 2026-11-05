import json
import logging
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from tianshu.operations.agent import incident_from_log
from tianshu.operations.incidents import safe_summary
from tianshu.runtime.api import error_middleware
from tianshu.runtime.errors import AppError
from tianshu.runtime.logging import StructuredFormatter, TaskMetadata
from tianshu.runtime.rb_logs_output import output_record
from tianshu.runtime.rb_logs_query import LogQuery


class Events:
    def __init__(self):
        self.values = []

    async def emit(self, event, outcome, **fields):
        self.values.append({"event": event, "outcome": outcome, **fields})


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
