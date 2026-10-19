"""Existing SSH transport plus a durable no-resend relay journal."""

from __future__ import annotations

import copy

from ..protocol import Failure, decode_json, utc_now
from ..transport import Deadline, SSHTransport
from .protocol import OPERATIONS, Request, VERSION, empty_runtime, encode, envelope, receipt, semantic
from .state import Store


def unknown(value, reason):
    public = copy.deepcopy(value["receipt"])
    public.update(state="unknown", phase="unknown", updated_at=utc_now(), execution_active=None, reason_codes=[reason], runtime=empty_runtime(),
                  command={"state": "unknown", "exit_code": None, "reason_codes": [reason]})
    value["receipt"] = public
    return public


def execute(request, config, *, transport_factory=SSHTransport):
    if request.operation == "control.catalog":
        nodes = [{"id": node_id, "label": config.observation.nodes[node_id].label, "capabilities": list(OPERATIONS)} for node_id in sorted(config.commands)]
        return envelope(request, data={"nodes": nodes}, returned=len(nodes), total=len(nodes))
    if request.node_id not in config.commands:
        raise Failure("control_node_not_registered", "unsupported")
    deadline = Deadline(request.timeout_ms / 1000)
    node = config.observation.nodes[request.node_id]
    transport = transport_factory(config.observation, node, deadline)
    store = Store(config.state_dir)

    def call(outgoing):
        result = transport.command(config.commands[request.node_id], encode(outgoing.value()), max_output=4 * 1024 * 1024)
        if result.reason:
            raise Failure(result.reason)
        try:
            response = decode_json(result.stdout)
            if not isinstance(response, dict) or response.get("version") != VERSION or response.get("request_id") != outgoing.request_id or response.get("node_id") != outgoing.node_id or response.get("operation") != outgoing.operation:
                raise ValueError("response scope")
            if set(response) != {"version", "request_id", "node_id", "operation", "status", "reason_codes", "observed_at", "capabilities", "completeness", "data"} or response["status"] not in ("ok", "partial", "unavailable", "unsupported", "invalid", "conflict"):
                raise ValueError("response envelope")
            return response
        except (ValueError, UnicodeError, RecursionError, TypeError):
            raise Failure("control_response_invalid") from None

    def recovery(operation_id):
        return Request(request.request_id, request.node_id, "operation.get", {"operation_id": operation_id, "reconcile": True}, request.timeout_ms)

    if request.operation not in ("operation.submit", "operation.get"):
        return call(request)
    identity = request.node_id + ":" + request.params["operation_id"]
    with store.lock("relay", identity, deadline, wait=True):
        value = store.read("relay", identity)
        if request.operation == "operation.submit":
            digest = semantic(request.node_id, request.params)
            if value and value["semantic"] != digest:
                raise Failure("operation_id_conflict", "conflict")
            if value and value["receipt"]["state"] in ("succeeded", "failed", "rejected"):
                return envelope(request, data={"operation": value["receipt"], "replayed": True}, returned=1, total=1)
            if value:
                outgoing = recovery(request.params["operation_id"])
            else:
                value = {"semantic": digest, "receipt": receipt(request.node_id, request.params), "sent": True}
                # Persist before calling SSH. A crash in the send window is
                # reconciled through GET, never another submit.
                store.write("relay", identity, value)
                outgoing = request
        else:
            outgoing = request
        try:
            response = call(outgoing)
            data = response.get("data")
            if value and data and "operation" in data:
                public = data["operation"]
                if public.get("operation_id") != request.params["operation_id"] or public.get("node_id") != request.node_id or semantic(request.node_id, {key: public[key] for key in ("operation_id", "action", "target")}) != value["semantic"]:
                    raise Failure("control_receipt_identity_invalid")
                value["receipt"] = public
                store.write("relay", identity, value)
            elif value and response["status"] not in ("ok", "partial"):
                if outgoing.operation == "operation.submit" and response["status"] in ("invalid", "unsupported", "conflict") and response["reason_codes"] != ["operation_not_found"]:
                    public = value["receipt"]
                    public.update(state="rejected", phase="complete", execution_active=False, reason_codes=response["reason_codes"], updated_at=utc_now())
                    store.write("relay", identity, value)
                    return envelope(request, data={"operation": public, "replayed": False}, returned=1, total=1)
                raise Failure("operation_receipt_unconfirmed")
            if outgoing.operation != request.operation:
                response.update(operation=request.operation, request_id=request.request_id)
                if data:
                    data["replayed"] = True
            return response
        except Failure as failure:
            if not value:
                raise
            if value["receipt"]["state"] in ("succeeded", "failed", "rejected"):
                return envelope(request, data={"operation": value["receipt"], "replayed": True}, status="partial", reasons=["receipt_readback_unavailable"], returned=1, total=1)
            public = unknown(value, "transport_outcome_unknown")
            store.write("relay", identity, value)
            return envelope(request, data={"operation": public, "replayed": outgoing.operation != "operation.submit"}, status="partial", reasons=["transport_outcome_unknown"], returned=1, total=1)
