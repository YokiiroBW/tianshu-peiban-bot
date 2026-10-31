"""One bounded JSON exchange with the fixed, deployment-owned node executor."""

import asyncio
import hashlib
import os
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from ..contracts import Fault, canonical, loads, require
from .process import CREATE_SUSPENDED, ProcessTree, stop_process

VERSION = "infrastructure-node.v1"
SCHEMA_SHA256 = {
    "request": "83208d146feaaeca586558391bf04c80f7266973a4fa17cde184da389fdffbb5",
    "response": "e2bee732aa22119000be753974e54124cc119e01ca2b1fd9d79157e267f6e092",
}
MAX_RESPONSE = 4 * 1024**2


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(
            result.tzinfo is not None and result.utcoffset().total_seconds() == 0,
            "infrastructure_response_invalid",
            502,
        )
        return result.timestamp()
    except (ValueError, TypeError, AttributeError):
        raise Fault("infrastructure_response_invalid", 502) from None


class Contract:
    def __init__(self, directory):
        self.validators = {}
        try:
            for name, expected in SCHEMA_SHA256.items():
                raw = (Path(directory) / f"{name}.schema.json").read_bytes().replace(b"\r\n", b"\n")
                require(
                    hashlib.sha256(raw).hexdigest() == expected,
                    "infrastructure_contract_unavailable",
                    503,
                )
                self.validators[name] = Draft202012Validator(
                    loads(raw), format_checker=FormatChecker()
                )
        except (OSError, ValueError):
            raise Fault("infrastructure_contract_unavailable", 503) from None

    def check(self, name, document):
        require(
            not next(self.validators[name].iter_errors(document), None),
            "infrastructure_response_invalid" if name == "response" else "invalid_input",
            502 if name == "response" else 400,
        )


class Executor:
    def __init__(self, config):
        self.command = config["executor"]["command"]
        self.directory = config["executor"]["directory"]
        self.timeout_ms = config["timeout_ms"]
        self.contract = Contract(config["contract_directory"])
        self.processes = set()
        self.trees = {}

    async def close(self):
        await asyncio.gather(
            *(stop_process(process, self.trees.get(process)) for process in tuple(self.processes)),
            return_exceptions=True,
        )

    async def invoke(self, node_id, operation, params=None):
        request = {
            "version": VERSION,
            "request_id": "infra:" + uuid.uuid4().hex,
            "node_id": node_id,
            "operation": operation,
            "params": params or {},
            "timeout_ms": self.timeout_ms,
        }
        return await self.exchange(request, self.contract, self._semantics)

    async def exchange(self, request, contract, semantics, *, executor=None):
        """Share bounded transport and process ownership across the two versioned protocols."""
        contract.check("request", request)
        executor = executor or {"command": self.command, "directory": self.directory}
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            in {"PATH", "SYSTEMROOT", "WINDIR", "PROGRAMDATA", "TEMP", "TMP", "LANG", "LC_ALL"}
        }
        process = tree = None
        command = executor["command"]
        if sys.platform == "linux":
            command = [
                sys.executable,
                "-I",
                "-B",
                str(Path(__file__).with_name("supervisor.py")),
                "--parent-pid",
                str(os.getpid()),
                "--",
                *command,
            ]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=executor["directory"],
                env=environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **(
                    {
                        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP
                        | subprocess.CREATE_NO_WINDOW
                        | CREATE_SUSPENDED
                    }
                    if os.name == "nt"
                    else {"start_new_session": True}
                ),
            )
            tree = ProcessTree(process.pid)
            tree.resume()
        except OSError:
            if process is not None:
                await stop_process(process, tree)
            raise Fault("infrastructure_executor_unavailable", 503) from None
        self.processes.add(process)
        self.trees[process] = tree

        async def read(stream, limit):
            result = bytearray()
            while chunk := await stream.read(8192):
                require(len(result) + len(chunk) <= limit, "infrastructure_output_exceeded", 502)
                result.extend(chunk)
            return bytes(result)

        async def write():
            try:
                process.stdin.write((canonical(request) + "\n").encode())
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()

        readers = [
            asyncio.create_task(read(process.stdout, MAX_RESPONSE)),
            asyncio.create_task(read(process.stderr, 65536)),
            asyncio.create_task(write()),
            asyncio.create_task(process.wait()),
        ]
        try:
            async with asyncio.timeout(request["timeout_ms"] / 1000 + 1):
                raw, _, _, exit_code = await asyncio.gather(*readers)
            require(exit_code == 0, "infrastructure_executor_failed", 502)
            try:
                response = loads(raw)
            except Fault:
                raise Fault("infrastructure_response_invalid", 502) from None
            contract.check("response", response)
            semantics(request, response)
            return response
        except TimeoutError:
            raise Fault("infrastructure_executor_timeout", 504) from None
        finally:
            await stop_process(process, self.trees.pop(process, None))
            for reader in readers:
                reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            self.processes.discard(process)

    @staticmethod
    def _semantics(request, response):
        for key in ("version", "request_id", "node_id", "operation"):
            require(response[key] == request[key], "infrastructure_response_invalid", 502)
        timestamp(response["observed_at"])
        if response["status"] not in {"ok", "partial"}:
            return
        data, operation, completeness = (
            response["data"],
            request["operation"],
            response["completeness"],
        )
        list_key = {
            "node.catalog": "nodes",
            "docker.inventory": "containers",
            "docker.stats": "containers",
            "compose.inventory": "stacks",
        }.get(operation)
        if list_key:
            rows = data[list_key]
            ids = [row["id"] for row in rows]
            require(
                len(ids) == len(set(ids))
                and completeness["returned"] == len(rows)
                and (completeness["total"] is None or completeness["total"] >= len(rows)),
                "infrastructure_response_invalid",
                502,
            )
            if operation == "docker.stats":
                require(
                    set(ids) == set(request["params"]["container_ids"]),
                    "infrastructure_response_invalid",
                    502,
                )
                for row in rows:
                    if row["sampled_at"] is not None:
                        timestamp(row["sampled_at"])
                    cpu, network = row["cpu"], row["network"]
                    require(
                        not cpu or cpu["state"] == "ok" or cpu["usage_ratio"] is None,
                        "infrastructure_response_invalid",
                        502,
                    )
                    require(
                        not network
                        or network["attribution"] == "container"
                        or (network["receive_bytes"] is None and network["transmit_bytes"] is None),
                        "infrastructure_response_invalid",
                        502,
                    )
        if operation == "node.snapshot":
            require(
                data["identity"]["node_id"] == request["node_id"],
                "infrastructure_response_invalid",
                502,
            )
            require(
                data["cpu"]["state"] == "ok" or data["cpu"]["usage_ratio"] is None,
                "infrastructure_response_invalid",
                502,
            )
            for row in data["interfaces"]:
                require(
                    row["state"] == "ok"
                    or (
                        row["receive_bytes_per_second"] is None
                        and row["transmit_bytes_per_second"] is None
                    ),
                    "infrastructure_response_invalid",
                    502,
                )
        if operation == "docker.logs":
            params = request["params"]
            size = len(data["stdout"].encode()) + len(data["stderr"].encode())
            require(
                data["container_id"] == params["container_id"]
                and size == data["returned_bytes"]
                and size <= params.get("max_bytes", 65536)
                and data["max_bytes"] <= params.get("max_bytes", 65536)
                and data["tail"] <= params.get("tail", 200)
                and len(data["stdout"].splitlines()) + len(data["stderr"].splitlines())
                <= params.get("tail", 200),
                "infrastructure_response_invalid",
                502,
            )
