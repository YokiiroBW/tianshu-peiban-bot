"""Server-owned observation pump. Browsers enqueue work and read its bounded cache."""

import asyncio
import copy
import time
import uuid
from collections import OrderedDict

from ..contracts import Fault, canonical, require, utc
from ..external_catalog import ExternalCatalog
from .client import Executor, VERSION, timestamp
from .config import configuration
from .repository import Repository

PREFIX = "infrastructure:"
OPERATIONS = ("node.snapshot", "docker.inventory", "docker.stats", "compose.inventory")
MAX_NODES = 64
MAX_CACHED_BYTES = 16 * 1024**2


class Infrastructure:
    def __init__(self, settings, *, clock=time.time, local, runtime=None):
        self.storage_runtime = runtime
        self.config = configuration(settings, runtime=runtime)
        self.clock, self.run_local = clock, local
        self.pending_local = set()
        self.started = False
        self.pump = None
        self.wake = asyncio.Event()
        self.slots = asyncio.Semaphore(2)
        self.jobs, self.runs, self.due = {}, {}, {}
        self.node_locks = {}
        self.catalog_job = None
        self.catalog_due = 0
        self.log_jobs, self.logs = {}, OrderedDict()
        self.catalog = self.repo = self.executor = None
        if self.config is not None:
            self.catalog = ExternalCatalog(self.config["directory"], runtime=runtime)
            self.repo = Repository(
                settings["database_path"] + ".infrastructure", runtime=runtime
            )
            self.executor = Executor(self.config)
        from .control import Control

        self.control = Control(self)

    async def local(self, operation, *args, **kwargs):
        # LocalWork keeps a running native database transaction alive when its caller is cancelled.
        # Keep ownership of that completion too, so close does not return with open files.
        task = asyncio.create_task(self.run_local(operation, *args, **kwargs))
        self.pending_local.add(task)
        task.add_done_callback(self.pending_local.discard)
        task.add_done_callback(
            lambda result: result.exception() if not result.cancelled() else None
        )
        return await asyncio.shield(task)

    def definitions(self):
        if self.catalog is None:
            return 0, {}
        revision, rows = self.catalog.snapshot()
        return revision, {
            key[len(PREFIX) :]: row for key, row in rows.items() if key.startswith(PREFIX)
        }

    def definition(self, node_id, *, enabled=False):
        _, rows = self.definitions()
        row = rows.get(node_id)
        require(row is not None, "infrastructure_node_not_registered", 404)
        require(not enabled or row["value"]["enabled"], "infrastructure_node_disabled", 409)
        return row

    def current(self, node_id, revision):
        _, rows = self.definitions()
        row = rows.get(node_id)
        return bool(
            row and row["value"]["enabled"] and row["value"]["connection_revision"] == revision
        )

    async def start(self):
        if self.started or self.config is None:
            return
        self.started = True
        await self.control.start()
        from ..media.background import Background
        self.background=Background(self.storage_runtime)
        self.background.start("infrastructure_observe",self._pump)
        await self.background.activate()

    async def quiesce(self):
        self.started = False
        if hasattr(self,"background"):await self.background.close()
        if self.pump:
            self.pump.cancel()
            await asyncio.gather(self.pump, return_exceptions=True)
        await self.control.quiesce()
        tasks = [self.catalog_job] if self.catalog_job else []
        tasks.extend(item[1] for item in self.jobs.values())
        tasks.extend(self.log_jobs.values())
        await asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True)
        while self.pending_local:
            await asyncio.gather(
                *(asyncio.shield(task) for task in tuple(self.pending_local)),
                return_exceptions=True,
            )

    async def close(self):
        self.started = False
        if hasattr(self,"background"):await self.background.close()
        await self.control.close()
        tasks = [item for item in [self.pump, self.catalog_job] if item]
        tasks.extend(item[1] for item in self.jobs.values())
        tasks.extend(self.log_jobs.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.executor:
            await self.executor.close()
        await asyncio.gather(*tuple(self.pending_local), return_exceptions=True)
        self.jobs.clear()
        self.log_jobs.clear()

    async def _pump(self):
        if self.started:
            try:
                _, rows = await self.local(self.definitions)
                now = time.monotonic()
                if now >= self.catalog_due:
                    self.request_catalog()
                for node_id, row in rows.items():
                    value = row["value"]
                    revision = value["connection_revision"]
                    old = self.jobs.get(node_id)
                    if old and (not value["enabled"] or old[0] != revision):
                        await self.changed(node_id)
                    if (
                        value["enabled"]
                        and node_id not in self.jobs
                        and now >= self.due.get(node_id, 0)
                    ):
                        self._enqueue(node_id, revision)
            except (Fault, OSError):
                # A temporarily unreadable connection store cannot authorise new collection.
                for node_id in tuple(self.jobs):
                    await self.changed(node_id)

    def request_catalog(self):
        if self.config is None:
            return {"state": "unconfigured"}
        if self.catalog_job is None or self.catalog_job.done():
            self.catalog_job = asyncio.create_task(self._catalog(), name="infrastructure-catalog")
        return {"state": "in_progress"}

    async def _catalog(self):
        try:
            async with self.slots:
                response = await self._call(None, "node.catalog")
                await self.local(self.repo.record, "@catalog", "node.catalog", 0, response)
        finally:
            self.catalog_due = time.monotonic() + 30

    async def request(self, node_id):
        require(self.config is not None, "infrastructure_not_configured", 503)
        row = await self.local(self.definition, node_id, enabled=True)
        revision = row["value"]["connection_revision"]
        existing = self.jobs.get(node_id)
        if existing and existing[0] != revision:
            await self.changed(node_id)
        if node_id not in self.jobs:
            self._enqueue(node_id, revision)
        return copy.deepcopy(self.runs[node_id])

    def _enqueue(self, node_id, revision):
        self.runs[node_id] = {
            "attempt_id": uuid.uuid4().hex,
            "state": "queued",
            "stage": "queued",
            "started_at": utc(self.clock()),
            "finished_at": None,
            "reason_codes": [],
        }
        self.jobs[node_id] = (
            revision,
            asyncio.create_task(
                self._collect(node_id, revision),
                name="infrastructure-node:" + node_id,
            ),
        )

    async def changed(self, node_id):
        await self.control.changed(node_id)
        previous = self.jobs.pop(node_id, None)
        tasks = [previous[1]] if previous else []
        tasks.extend(task for key, task in self.log_jobs.items() if key[0] == node_id)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for key in tuple(self.logs):
            if key[0] == node_id:
                self.logs.pop(key, None)
        self.due.pop(node_id, None)
        self.runs.pop(node_id, None)
        self.wake.set()

    async def _call(self, node_id, operation, params=None):
        try:
            return await self.executor.invoke(node_id, operation, params)
        except Fault as error:
            return self.failure(node_id, operation, error.code)

    def failure(self, node_id, operation, code):
        return {
            "version": VERSION,
            "request_id": None,
            "node_id": node_id,
            "operation": operation,
            "status": "unavailable",
            "reason_codes": [code],
            "observed_at": utc(self.clock()),
            "capabilities": [],
            "completeness": {
                "state": "unavailable",
                "returned": 0,
                "total": None,
                "next_cursor": None,
            },
            "data": None,
        }

    async def _publish(self, node_id, revision, operation, response):
        if not await self.local(self.current, node_id, revision):
            return False
        await self.local(self.repo.record, node_id, operation, revision, response)
        return True

    async def _collect(self, node_id, revision):
        run = self.runs[node_id]
        try:
            async with self.slots, self.node_locks.setdefault(node_id, asyncio.Lock()):
                if not await self.local(self.current, node_id, revision):
                    return
                run.update(state="in_progress", stage="node.snapshot")
                async with asyncio.timeout(90):
                    snapshot = await self._call(node_id, "node.snapshot")
                    await self._publish(node_id, revision, "node.snapshot", snapshot)
                    results = [snapshot]
                    if snapshot["status"] in {"ok", "partial"}:
                        capabilities = snapshot["capabilities"]
                        inventory = None
                        for operation in ("docker.inventory", "docker.stats", "compose.inventory"):
                            run["stage"] = operation
                            if operation not in capabilities:
                                result = self.failure(
                                    node_id, operation, "capability_not_supported"
                                )
                                result["status"] = "unsupported"
                            elif operation == "docker.stats":
                                result = await self._stats(node_id, revision, inventory)
                            else:
                                result = await self._pages(node_id, operation)
                            if operation == "docker.inventory":
                                inventory = result
                            await self._publish(node_id, revision, operation, result)
                            if result["status"] != "unsupported":
                                results.append(result)
                    else:
                        for operation in OPERATIONS[1:]:
                            await self._publish(
                                node_id,
                                revision,
                                operation,
                                self.failure(node_id, operation, "node_unavailable"),
                            )
                    if await self.local(self.current, node_id, revision):
                        await self._record_test(node_id, revision, snapshot)
                    run["state"] = (
                        "observed"
                        if all(item["status"] == "ok" for item in results)
                        else "partial"
                        if snapshot["data"] is not None
                        else "failed"
                    )
                    run["reason_codes"] = sorted(
                        {code for item in results for code in item["reason_codes"]}
                    )[:64]
        except TimeoutError:
            run.update(state="failed", reason_codes=["infrastructure_cycle_timeout"])
            await self._publish(
                node_id,
                revision,
                run["stage"],
                self.failure(node_id, run["stage"], "infrastructure_cycle_timeout"),
            )
        except (Fault, OSError):
            run.update(state="failed", reason_codes=["infrastructure_store_unavailable"])
        except asyncio.CancelledError:
            run.update(state="cancelled", reason_codes=["infrastructure_collection_cancelled"])
            raise
        except Exception as error:
            run.update(state="failed", reason_codes=["infrastructure_collection_failed"], error_type=type(error).__name__)
        finally:
            run.update(stage="finished", finished_at=utc(self.clock()))
            try:
                observation = self.failure(node_id, "collection.run", "collection_finished")
                observation.update(status="ok", data={"run": dict(run)}, reason_codes=list(run["reason_codes"]))
                await self._publish(node_id, revision, "collection.run", observation)
                events = getattr(self.storage_runtime, "events", None)
                if events and run["state"] in {"failed", "cancelled"}:
                    await events.emit("job.failed", run["state"], task_name="capabilities.infrastructure.collect",
                        correlation_id=run["attempt_id"],
                        error_code=(run["reason_codes"] or ["infrastructure_collection_failed"])[0])
            except Exception:
                run["reason_codes"] = sorted(set(run["reason_codes"]) | {"infrastructure_store_unavailable"})
            existing = self.jobs.get(node_id)
            if existing and existing[1] is asyncio.current_task():
                self.jobs.pop(node_id, None)
            self.due[node_id] = time.monotonic() + self.config["poll_interval_seconds"]

    async def _record_test(self, node_id, revision, snapshot):
        def record():
            global_revision, rows = self.definitions()
            row = rows.get(node_id)
            if row is None or row["value"]["connection_revision"] != revision:
                return
            result = {
                "state": "connected" if snapshot["status"] in {"ok", "partial"} else "unavailable",
                "code": (snapshot["reason_codes"] or ["read_observed"])[0],
                "checked_at": utc(self.clock()),
                "connection_revision": revision,
            }
            try:
                self.catalog.record_test(PREFIX + node_id, global_revision, result)
            except Fault as error:
                if error.code != "revision_conflict":
                    raise

        await self.local(record)

    async def _pages(self, node_id, operation):
        key = "containers" if operation == "docker.inventory" else "stacks"
        for restart in range(2):
            collected, seen, cursors, reasons = [], set(), set(), set()
            cursor, complete, earliest = None, True, None
            accumulated_bytes = 0
            for _ in range(50):
                response = await self._call(node_id, operation, {"limit": 200, "cursor": cursor})
                if "cursor_invalidated" in response["reason_codes"]:
                    break
                if response["status"] not in {"ok", "partial"}:
                    return response
                rows = response["data"][key]
                accumulated_bytes += len(canonical(rows).encode())
                if accumulated_bytes > MAX_CACHED_BYTES:
                    return {
                        **response,
                        "status": "partial",
                        "reason_codes": ["infrastructure_cache_limit"],
                        "data": {key: collected},
                        "observed_at": earliest or response["observed_at"],
                        "completeness": {
                            "state": "partial",
                            "returned": len(collected),
                            "total": response["completeness"]["total"],
                            "next_cursor": cursor,
                        },
                    }
                ids = {item["id"] for item in rows}
                if seen & ids:
                    return self.failure(node_id, operation, "infrastructure_pagination_invalid")
                seen.update(ids)
                collected.extend(rows)
                reasons.update(response["reason_codes"])
                complete &= (
                    response["status"] == "ok" and response["completeness"]["state"] == "complete"
                )
                if earliest is None or timestamp(response["observed_at"]) < timestamp(earliest):
                    earliest = response["observed_at"]
                cursor = response["completeness"]["next_cursor"]
                if cursor is None:
                    total = response["completeness"]["total"]
                    if total is not None and total != len(collected):
                        complete = False
                        reasons.add("inventory_incomplete")
                    return {
                        **response,
                        "status": "ok" if complete else "partial",
                        "observed_at": earliest,
                        "reason_codes": sorted(reasons)[:64],
                        "completeness": {
                            "state": "complete" if complete else "partial",
                            "returned": len(collected),
                            "total": total,
                            "next_cursor": None,
                        },
                        "data": {key: collected},
                    }
                if cursor in cursors:
                    return self.failure(node_id, operation, "infrastructure_pagination_invalid")
                cursors.add(cursor)
            else:
                return self.failure(node_id, operation, "infrastructure_inventory_limit")
            if restart:
                return self.failure(node_id, operation, "cursor_invalidated")
        return self.failure(node_id, operation, "cursor_invalidated")

    async def _stats(self, node_id, revision, inventory):
        if inventory is None or inventory["status"] not in {"ok", "partial"}:
            return self.failure(node_id, "docker.stats", "inventory_unavailable")
        ids = [row["id"] for row in inventory["data"]["containers"] if row["availability"] == "ok"]
        old = await self.local(self.repo.read, node_id, "docker.stats", revision)
        previous = (
            {row["id"]: row for row in old["sample"]["data"]["containers"]}
            if old and old["sample"]
            else {}
        )
        rows, reasons, response = [], set(inventory["reason_codes"]), None
        batches_complete, earliest = True, None
        accumulated_bytes = 0
        for start in range(0, len(ids), 200):
            response = await self._call(
                node_id, "docker.stats", {"container_ids": ids[start : start + 200]}
            )
            if response["status"] not in {"ok", "partial"}:
                return response
            batches_complete &= (
                response["status"] == "ok" and response["completeness"]["state"] == "complete"
            )
            if earliest is None or timestamp(response["observed_at"]) < timestamp(earliest):
                earliest = response["observed_at"]
            reasons.update(response["reason_codes"])
            accumulated_bytes += len(canonical(response["data"]).encode())
            if accumulated_bytes > MAX_CACHED_BYTES:
                reasons.add("infrastructure_cache_limit")
                break
            for row in response["data"]["containers"]:
                row["rates"] = self._rates(row, previous.get(row["id"]))
                reasons.update(row["reason_codes"])
                if row["cpu"]:
                    reasons.update(row["cpu"].get("reason_codes", []))
                if row["rates"]["state"] != "ok":
                    reasons.add(row["rates"]["state"])
                rows.append(row)
        if response is None:
            response = {**inventory, "operation": "docker.stats", "observed_at": utc(self.clock())}
        complete = (
            inventory["status"] == "ok"
            and batches_complete
            and not reasons
            and len(rows) == len(ids)
            and all(row["availability"] == "ok" for row in rows)
        )
        return {
            **response,
            "observed_at": earliest or inventory["observed_at"],
            "status": "ok" if complete else "partial",
            "reason_codes": sorted(reasons)[:64],
            "data": {"containers": rows},
            "completeness": {
                "state": "complete" if complete else "partial",
                "returned": len(rows),
                "total": len(ids),
                "next_cursor": None,
            },
        }

    def _rates(self, row, previous):
        fields = {
            "receive_bytes_per_second": ("network", "receive_bytes"),
            "transmit_bytes_per_second": ("network", "transmit_bytes"),
            "read_bytes_per_second": ("block_io", "read_bytes"),
            "write_bytes_per_second": ("block_io", "write_bytes"),
        }
        result = {name: None for name in fields}
        result["state"] = "first_sample"
        if row["availability"] != "ok" or not previous or previous["availability"] != "ok":
            return result
        if row["cpu"] and row["cpu"]["state"] != "ok":
            result["state"] = row["cpu"]["state"]
            return result
        try:
            elapsed = timestamp(row["sampled_at"]) - timestamp(previous["sampled_at"])
        except Fault:
            return result
        if elapsed <= 0 or elapsed > self.config["stale_after_seconds"]:
            result["state"] = "stale"
            return result
        result["state"] = "ok"
        for name, (group, field) in fields.items():
            current, before = row.get(group), previous.get(group)
            if group == "network" and (not current or current["attribution"] != "container"):
                continue
            if not current or not before or current[field] is None or before[field] is None:
                continue
            if current[field] < before[field]:
                result["state"] = "counter_reset"
                continue
            result[name] = (current[field] - before[field]) / elapsed
        return result

    def observation(self, node_id, operation, revision, *, enabled=True):
        saved = self.repo.read(node_id, operation, revision) if self.repo else None
        latest = saved["latest"] if saved else None
        sample = saved["sample"] if saved else None
        age = self.clock() - timestamp(sample["observed_at"]) if sample else None
        fresh = bool(
            enabled
            and sample
            and latest["status"] in {"ok", "partial"}
            and -5 <= age <= self.config["stale_after_seconds"]
        )
        return {
            "status": latest["status"] if latest else "unknown",
            "freshness": "current" if fresh else "stale" if sample else "missing",
            "observed_at": sample["observed_at"] if sample else None,
            "last_attempt_at": latest["observed_at"] if latest else None,
            "age_seconds": max(0, round(age)) if age is not None else None,
            "reason_codes": latest["reason_codes"] if latest else ["not_sampled"],
            "capabilities": sample["capabilities"] if sample else [],
            "completeness": sample["completeness"] if sample else None,
            "data": copy.deepcopy(sample["data"]) if sample else None,
        }

    def _saved_run(self, node_id, revision):
        saved = self.repo.read(node_id, "collection.run", revision) if self.repo else None
        return ((saved["latest"].get("data") or {}).get("run")) if saved else None

    def node(self, node_id):
        row = self.definition(node_id)
        value = row["value"]
        snapshot = self.observation(
            node_id, "node.snapshot", value["connection_revision"], enabled=value["enabled"]
        )
        connection = (
            "disabled"
            if not value["enabled"]
            else "offline"
            if snapshot["status"] in {"unavailable", "invalid"}
            else snapshot["freshness"]
        )
        return {
            "id": node_id,
            "label": value["label"],
            "kind": value["kind"],
            "enabled": value["enabled"],
            "connection_revision": value["connection_revision"],
            "connection_state": connection,
            "observed_at": snapshot["observed_at"],
            "last_attempt_at": snapshot["last_attempt_at"],
            "refresh": copy.deepcopy(self.runs.get(node_id)) or self._saved_run(node_id, value["connection_revision"]),
            "last_test": row["last_test"],
            "capabilities": snapshot["capabilities"],
            "snapshot": snapshot,
        }

    def view(self):
        revision, rows = self.definitions()
        nodes = []
        for node_id in sorted(
            rows, key=lambda identity: (rows[identity]["value"]["label"].casefold(), identity)
        ):
            node = self.node(node_id)
            node.pop("snapshot")
            nodes.append(node)
        catalog = self.observation("@catalog", "node.catalog", 0)
        catalog["refreshing"] = bool(self.catalog_job and not self.catalog_job.done())
        return {
            "configured": self.config is not None,
            "revision": revision,
            "catalog": catalog,
            "nodes": nodes,
            "max_nodes": MAX_NODES,
        }

    async def request_logs(self, node_id, container_id):
        row = await self.local(self.definition, node_id, enabled=True)
        revision = row["value"]["connection_revision"]
        key = node_id, container_id
        inventory = await self.local(self.observation, node_id, "docker.inventory", revision)
        require(
            inventory["data"]
            and any(item["id"] == container_id for item in inventory["data"]["containers"]),
            "infrastructure_container_not_found",
            404,
        )
        if key not in self.log_jobs:
            require(len(self.log_jobs) < 8, "too_many_requests", 429)
            while len(self.logs) >= 16:
                oldest = next((item for item in self.logs if item not in self.log_jobs), None)
                require(oldest is not None, "too_many_requests", 429)
                self.logs.pop(oldest)
            self.logs[key] = {
                "state": "queued",
                "attempt_id": uuid.uuid4().hex,
                "data": None,
                "observed_at": None,
                "reason_codes": [],
            }
            self.log_jobs[key] = asyncio.create_task(self._logs(node_id, container_id, revision))
        return copy.deepcopy(self.logs[key])

    async def _logs(self, node_id, container_id, revision):
        key = node_id, container_id
        result = self.logs[key]
        try:
            async with self.slots, self.node_locks.setdefault(node_id, asyncio.Lock()):
                if not await self.local(self.current, node_id, revision):
                    return
                result["state"] = "in_progress"
                response = await self._call(
                    node_id,
                    "docker.logs",
                    {
                        "container_id": container_id,
                        "tail": 200,
                        "max_bytes": 65536,
                        "since_seconds": 3600,
                    },
                )
                if not await self.local(self.current, node_id, revision):
                    return
                result.update(
                    state="observed" if response["status"] in {"ok", "partial"} else "failed",
                    data=response["data"],
                    observed_at=response["observed_at"],
                    reason_codes=response["reason_codes"],
                )
        except (Fault, OSError):
            result.update(state="failed", reason_codes=["infrastructure_store_unavailable"])
        finally:
            self.log_jobs.pop(key, None)

    def log_view(self, node_id, container_id):
        self.definition(node_id)
        return copy.deepcopy(
            self.logs.get(
                (node_id, container_id),
                {
                    "state": "not_requested",
                    "attempt_id": None,
                    "data": None,
                    "observed_at": None,
                    "reason_codes": [],
                },
            )
        )
