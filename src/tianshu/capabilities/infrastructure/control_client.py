"""Pinned control protocol; transport and child lifetime belong to the existing Executor."""

import hashlib
import uuid
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from ..contracts import Fault, loads, require
from .client import Contract, timestamp

VERSION = "infrastructure-control.v1"
SCHEMA_SHA256 = {
    "request": "30b0f4f785708797d9061388b1db77606c70137983eaefcea24345df00d1a830",
    "response": "908a2e93282b0dd283f31a2aa67fc4201de1af619b86115e9e9a8c3c42867b22",
}


class ControlContract(Contract):
    def __init__(self, directory):
        self.validators = {}
        try:
            for name, expected in SCHEMA_SHA256.items():
                raw = (Path(directory) / f"{name}.schema.json").read_bytes().replace(b"\r\n", b"\n")
                require(
                    hashlib.sha256(raw).hexdigest() == expected,
                    "infrastructure_control_contract_unavailable",
                    503,
                )
                self.validators[name] = Draft202012Validator(
                    loads(raw), format_checker=FormatChecker()
                )
        except (OSError, ValueError):
            raise Fault("infrastructure_control_contract_unavailable", 503) from None


class ControlClient:
    def __init__(self, config, executor):
        self.config, self.executor = config["control"], executor
        self.timeout_ms = config["timeout_ms"]
        self.contract = ControlContract(self.config["contract_directory"])

    async def invoke(self, node_id, operation, params=None):
        request = {
            "version": VERSION,
            "request_id": "control:" + uuid.uuid4().hex,
            "node_id": node_id,
            "operation": operation,
            "params": params or {},
            "timeout_ms": self.timeout_ms,
        }
        return await self.executor.exchange(
            request, self.contract, self._semantics, executor=self.config["executor"]
        )

    @staticmethod
    def _preview(plan):
        # The envelope schema bounds arrays; their producer-owned display fields are typed here.
        for row in plan["ports"]:
            require(
                isinstance(row.get("service"), str)
                and type(row.get("target")) is int
                and 1 <= row["target"] <= 65535
                and (row.get("published") is None or isinstance(row["published"], str))
                and (row.get("host_ip") is None or isinstance(row["host_ip"], str))
                and row.get("protocol") in {"tcp", "udp", "sctp"},
                "infrastructure_response_invalid",
                502,
            )
        for row in plan["mounts"]:
            require(
                isinstance(row.get("service"), str)
                and row.get("kind") in {"bind", "volume", "tmpfs"}
                and (row.get("source") is None or isinstance(row["source"], str))
                and isinstance(row.get("target"), str)
                and type(row.get("read_only")) is bool,
                "infrastructure_response_invalid",
                502,
            )
        for row in plan["images"]:
            require(
                all(isinstance(row.get(name), str) for name in ("service", "from", "to"))
                and (row.get("pull_policy") is None or isinstance(row["pull_policy"], str))
                and type(row.get("pull")) is bool
                and (
                    row.get("current_image_id") is None or isinstance(row["current_image_id"], str)
                )
                and (row.get("target_image_id") is None or isinstance(row["target_image_id"], str)),
                "infrastructure_response_invalid",
                502,
            )

    @staticmethod
    def _semantics(request, response):
        for key in ("version", "request_id", "node_id", "operation"):
            require(request[key] == response[key], "infrastructure_response_invalid", 502)
        timestamp(response["observed_at"])
        if response["status"] not in {"ok", "partial"}:
            return
        data = response["data"]
        key = {
            "control.catalog": "nodes",
            "stack.list": "stacks",
            "stack.inspect": "stack",
            "container.inspect": "container",
            "stack.plan": "plan",
            "operation.submit": "operation",
            "operation.get": "operation",
            "operation.list": "operations",
            "stack.versions": "versions",
        }[request["operation"]]
        require(isinstance(data, dict) and key in data, "infrastructure_response_invalid", 502)
        if isinstance(data[key], list):
            rows = data[key]
            identifier = {
                "nodes": "id",
                "stacks": "id",
                "operations": "operation_id",
                "versions": "version_id",
            }[key]
            ids = [row[identifier] for row in rows]
            completeness = response["completeness"]
            require(
                len(ids) == len(set(ids))
                and completeness["returned"] == len(rows)
                and (completeness["total"] is None or completeness["total"] >= len(rows)),
                "infrastructure_response_invalid",
                502,
            )
        if request["operation"] in {"operation.submit", "operation.get"}:
            receipt = data["operation"]
            require(
                receipt["operation_id"] == request["params"]["operation_id"]
                and receipt["node_id"] == request["node_id"],
                "infrastructure_response_invalid",
                502,
            )
            if request["operation"] == "operation.submit":
                require(
                    receipt["action"] == request["params"]["action"]
                    and receipt["target"] == request["params"]["target"],
                    "infrastructure_response_invalid",
                    502,
                )
        elif request["operation"] == "stack.inspect":
            require(
                data["stack"]["id"] == request["params"]["stack_id"],
                "infrastructure_response_invalid",
                502,
            )
        elif request["operation"] == "container.inspect":
            require(
                data["container"]["id"] == request["params"]["container_id"],
                "infrastructure_response_invalid",
                502,
            )
        elif request["operation"] == "stack.plan":
            plan, params = data["plan"], request["params"]
            ControlClient._preview(plan)
            require(
                plan["action"] == params["action"]
                and ("stack_id" not in params or plan["stack_id"] == params["stack_id"])
                and (
                    "expected_revision" not in params
                    or plan["base_revision"] == params["expected_revision"]
                )
                and (
                    "project_name" not in params or plan["project_name"] == params["project_name"]
                ),
                "infrastructure_response_invalid",
                502,
            )
