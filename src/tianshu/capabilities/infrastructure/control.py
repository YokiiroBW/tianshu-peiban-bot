"""Background Dockge work over durable claims; a possible send is never submitted twice."""

import asyncio
import copy
import uuid

from ..contracts import Fault, canonical, digest, require, utc
from . import control_projection as projection
from .client import timestamp
from .control_client import ControlClient
from .control_repository import ControlRepository, MUTATIONS

TASK_PREFIX = "infra-control:"
TASK_STATES = ("queued", "running", "observed", "partial", "failed", "unknown")


class NodeRefusal(Fault):
    """A validated explicit refusal, as distinct from a lost or ambiguous transport result."""


class Control:
    def __init__(self, runtime):
        self.runtime = runtime
        self.local, self.clock = runtime.local, runtime.clock
        self.repo = (
            ControlRepository(runtime.repo.database, runtime=runtime.storage_runtime)
            if runtime.repo
            else None
        )
        self.client = (
            ControlClient(runtime.config, runtime.executor)
            if runtime.config and runtime.config.get("control")
            else None
        )
        self.jobs, self.pump = {}, None
        self.started = False
        self.wake = asyncio.Event()
        self.lease = 2 * runtime.config["timeout_ms"] / 1000 + 10 if runtime.config else 70

    @property
    def configured(self):
        return self.client is not None

    async def start(self):
        if self.started or not self.configured:
            return
        self.started = True
        # The infrastructure owner registers bounded claims in unified Procrastinate.

    async def close(self):
        self.started = False
        tasks = [task for task in [self.pump, *self.jobs.values()] if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.jobs.clear()

    async def quiesce(self):
        self.started = False
        if self.pump:
            self.pump.cancel()
            await asyncio.gather(self.pump, return_exceptions=True)
        while self.jobs:
            await asyncio.gather(
                *(asyncio.shield(task) for task in tuple(self.jobs.values())),
                return_exceptions=True,
            )

    async def changed(self, node_id):
        tasks = [task for key, task in self.jobs.items() if key[0] == node_id]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.wake.set()

    async def _pump(self):
        if not self.started:return
        owner=uuid.uuid4().hex
        row=await self.local(self.repo.claim,owner,self.clock(),self.lease)
        if row is None:return
        key=row["node_id"],row["operation_id"]
        task=asyncio.create_task(self._work(row,owner),name="dockge:"+row["kind"])
        self.jobs[key]=task
        try:await task
        finally:self.jobs.pop(key,None)

    def _finished(self, key, task):
        if self.jobs.get(key) is task:
            self.jobs.pop(key, None)
        if not task.cancelled():
            task.exception()
        self.wake.set()

    async def accept(
        self,
        kind,
        node_id,
        revision,
        client_id,
        operation,
        params,
        *,
        stack_id=None,
        container_id=None,
        action=None,
        lock_key=None,
        deduplicate=False,
    ):
        require(self.configured, "infrastructure_control_not_configured", 503)
        payload = {
            "node_id": node_id,
            "revision": revision,
            "operation": operation,
            "params": params,
        }
        document = {
            "operation_id": TASK_PREFIX + client_id,
            "client_id": client_id,
            "kind": kind,
            "node_id": node_id,
            "connection_revision": revision,
            "stack_id": stack_id,
            "container_id": container_id,
            "action": action,
            "state": "queued",
            "stage": "queued",
            "acceptance": "not_sent",
            "created_at": utc(self.clock()),
            "updated_at": utc(self.clock()),
            "finished_at": None,
            "settled": False,
            "reason_codes": [],
            "can_refresh": False,
            "result": None,
        }

        def record():
            previous = self.repo.get(client_id=client_id)
            if previous:
                require(
                    previous["semantics"] == digest({"kind": kind, "payload": payload}),
                    "idempotency_conflict",
                    409,
                )
                return previous
            require(self.runtime.current(node_id, revision), "revision_conflict", 409)
            label, display_action = None, action
            if kind == "action":
                self._authorize(document, params)
                if container_id:
                    sampled = self.runtime.observation(
                        node_id, "control.container:" + container_id, revision
                    )
                    label = ((sampled["data"] or {}).get("container") or {}).get("name")
                else:
                    sampled = self.runtime.observation(node_id, "control.inventory", revision)
                    selected = next(
                        (
                            item
                            for item in (sampled["data"] or {}).get("stacks", [])
                            if item["id"] == stack_id
                        ),
                        None,
                    )
                    label = selected["name"] if selected else None
            elif kind == "execute":
                plan = self.execution_plan(node_id, revision, params["target"]["plan_id"])
                label, display_action = plan["project_name"], plan["action"]
            document.update(
                title=projection.ACTION_TITLES.get(display_action, "Dockge 状态读取"),
                target_label=label or stack_id or container_id or node_id,
            )
            return self.repo.accept(
                document,
                payload,
                lock_key or stack_id or container_id or node_id,
                self.clock(),
                deduplicate=deduplicate,
            )

        row = await self.local(record)
        self.wake.set()
        return row["document"]

    def _authorize(self, document, params):
        node_id, revision = document["node_id"], document["connection_revision"]
        identity = document["container_id"] or document["stack_id"]
        scope = "container" if document["container_id"] else "stack"
        sample = self.runtime.observation(node_id, "control." + scope + ":" + identity, revision)
        value = (sample["data"] or {}).get(scope)
        if value is None and scope == "stack":
            inventory = self.runtime.observation(node_id, "control.inventory", revision)
            require(inventory["freshness"] == "current", "infrastructure_observation_stale", 409)
            value = next(
                (
                    row
                    for row in (inventory["data"] or {}).get("stacks", [])
                    if row["id"] == identity
                ),
                None,
            )
        else:
            require(sample["freshness"] == "current", "infrastructure_observation_stale", 409)
        require(value is not None, "infrastructure_target_changed", 409)
        require(not value["protection"]["protected"], "infrastructure_maintenance_required", 409)
        action = params["action"].removeprefix(scope + ".")
        allowed = value["allowed_actions"]
        require(
            action in allowed or scope + "." + action in allowed,
            "infrastructure_action_unavailable",
            409,
        )
        if scope == "stack":
            require(
                value["revision"] == params["target"]["expected_revision"], "revision_conflict", 409
            )

    def execution_plan(self, node_id, revision, plan_id):
        plan = self.repo.plan(node_id, plan_id)
        require(plan is not None, "infrastructure_plan_not_found", 404)
        require(
            plan["node_id"] == node_id and plan["connection_revision"] == revision,
            "revision_conflict",
            409,
        )
        require(timestamp(plan["expires_at"]) > self.clock(), "infrastructure_plan_expired", 409)
        require(plan["executable"], "infrastructure_plan_blocked", 409)
        return plan

    async def request_read(self, node_id, *, stack_id=None, container_id=None):
        require(self.configured, "infrastructure_control_not_configured", 503)
        row = await self.local(self.runtime.definition, node_id, enabled=True)
        revision = row["value"]["connection_revision"]
        kind, operation, params = "inventory", "stack.list", {}
        if stack_id:
            kind, operation, params = "detail", "stack.inspect", {"stack_id": stack_id}
        elif container_id:
            kind, operation, params = "detail", "container.inspect", {"container_id": container_id}
        return await self.accept(
            kind,
            node_id,
            revision,
            str(uuid.uuid4()),
            operation,
            params,
            stack_id=stack_id,
            container_id=container_id,
            lock_key="read:" + (stack_id or container_id or node_id),
            deduplicate=True,
        )

    async def refresh_operation(self, identity):
        row = await self.local(self.repo.get, identity)
        require(row is not None, "infrastructure_operation_not_found", 404)
        require(
            await self.local(self.runtime.current, row["node_id"], row["revision"]),
            "revision_conflict",
            409,
        )
        await self.local(self.repo.refresh, identity, self.clock())
        self.wake.set()
        return (await self.local(self.repo.get, identity))["document"]

    async def _call(self, node_id, operation, params):
        response = await self.client.invoke(node_id, operation, params)
        if response["status"] not in {"ok", "partial"}:
            code = (response["reason_codes"] or ["infrastructure_control_unavailable"])[0]
            if operation == "operation.submit" and response["status"] in {
                "conflict",
                "unsupported",
                "invalid",
            }:
                raise NodeRefusal(code, 409)
            raise Fault(code, 409 if response["status"] == "conflict" else 503)
        return response

    async def _renew(self, row, owner):
        require(
            await self.local(self.repo.renew, row["operation_id"], owner, self.clock(), self.lease),
            "infrastructure_claim_lost",
            409,
        )
        require(
            await self.local(self.runtime.current, row["node_id"], row["revision"]),
            "revision_conflict",
            409,
        )

    async def _pages(self, row, owner, operation, key, params=None):
        result, cursor, seen, cursors, size = [], None, set(), set(), 0
        first, reasons = None, set()
        for _ in range(50):
            await self._renew(row, owner)
            response = await self._call(
                row["node_id"], operation, {**(params or {}), "limit": 200, "cursor": cursor}
            )
            first = first or response
            rows = response["data"][key]
            identifier = "id" if key == "stacks" else "version_id"
            ids = {row[identifier] for row in rows}
            size += len(canonical(rows).encode())
            require(not seen & ids and size <= 16 * 1024**2, "infrastructure_inventory_limit", 503)
            seen.update(ids)
            result.extend(rows)
            reasons.update(response["reason_codes"])
            if response["status"] == "partial":
                first["status"] = "partial"
            cursor = response["completeness"]["next_cursor"]
            if cursor is None:
                total = response["completeness"]["total"]
                if total is not None and total != len(result):
                    first["status"] = "partial"
                    reasons.add("inventory_incomplete")
                first.update(data={key: result}, reason_codes=sorted(reasons)[:100])
                first["completeness"] = {
                    "state": "complete" if first["status"] == "ok" else "partial",
                    "returned": len(result),
                    "total": total,
                    "next_cursor": None,
                }
                return first
            require(cursor not in cursors, "infrastructure_pagination_invalid", 502)
            cursors.add(cursor)
        raise Fault("infrastructure_inventory_limit", 503)

    async def _read(self, row, owner):
        node_id, revision = row["node_id"], row["revision"]
        payload, document = row["payload"], row["document"]
        operation, params = payload["operation"], payload["params"]
        if operation == "stack.list":
            catalog = await self._call(None, "control.catalog", {})
            node = next((item for item in catalog["data"]["nodes"] if item["id"] == node_id), None)
            require(node is not None, "infrastructure_control_node_not_registered", 409)
            response = await self._pages(row, owner, operation, "stacks")
            response["data"]["capabilities"] = node["capabilities"]
            scope = "control.inventory"
        elif operation == "stack.inspect":
            response = await self._call(node_id, operation, params)
            versions = await self._pages(row, owner, "stack.versions", "versions", params)
            response["data"]["versions"] = versions["data"]["versions"]
            response["reason_codes"] = sorted(
                set(response["reason_codes"] + versions["reason_codes"])
            )
            if versions["status"] == "partial":
                response["status"] = "partial"
            scope = "control.stack:" + params["stack_id"]
        elif operation == "container.inspect":
            response = await self._call(node_id, operation, params)
            scope = "control.container:" + params["container_id"]
        else:
            response = await self._call(node_id, operation, params)
            await self._renew(row, owner)
            document["result"] = await self.local(
                self.plan_projection, response["data"]["plan"], node_id, revision
            )
            document["state"] = "observed" if response["status"] == "ok" else "partial"
            document["reason_codes"] = response["reason_codes"]
            return
        await self._renew(row, owner)
        await self.local(self.runtime.repo.record, node_id, scope, revision, response)
        document.update(
            state="observed" if response["status"] == "ok" else "partial",
            reason_codes=response["reason_codes"],
        )

    def plan_projection(self, plan, node_id, revision):
        saved = self.runtime.observation(node_id, "control.stack:" + plan["stack_id"], revision)
        stack = (saved["data"] or {}).get("stack")
        if stack is None:
            inventory = self.runtime.observation(node_id, "control.inventory", revision)
            stack = next(
                (
                    value
                    for value in (inventory["data"] or {}).get("stacks", [])
                    if value["id"] == plan["stack_id"]
                ),
                None,
            )
        protection = stack["protection"] if stack else None
        # A fresh plan's explicit refusal corroborates the cached maintenance owner.
        if not protection or protection["reason"] not in plan["blockers"]:
            protection = None
        return projection.plan(plan, node_id, revision, protection)

    async def _work(self, row, owner):
        document, operation_id = row["document"], row["operation_id"]
        sent = bool(row["sent"])
        try:
            require(
                await self.local(self.runtime.current, row["node_id"], row["revision"]),
                "revision_conflict",
                409,
            )
            if row["kind"] in MUTATIONS:
                if not sent:
                    if row["kind"] == "execute":
                        await self.local(
                            self.execution_plan,
                            row["node_id"],
                            row["revision"],
                            row["payload"]["params"]["target"]["plan_id"],
                        )
                    require(
                        await self.local(self.repo.sending, operation_id, owner, self.clock()),
                        "infrastructure_claim_lost",
                        409,
                    )
                    sent = True
                    # Re-read after durable ownership, immediately before transport.
                    require(
                        await self.local(self.runtime.current, row["node_id"], row["revision"]),
                        "revision_conflict",
                        409,
                    )
                    response = await self._call(
                        row["node_id"], "operation.submit", row["payload"]["params"]
                    )
                else:
                    response = await self._call(
                        row["node_id"],
                        "operation.get",
                        {"operation_id": operation_id, "reconcile": True},
                    )
                remote = response["data"]["operation"]
                require(
                    remote["action"] == row["payload"]["params"]["action"]
                    and remote["target"] == row["payload"]["params"]["target"],
                    "infrastructure_response_invalid",
                    502,
                )
                document.update(projection.receipt(remote))
            else:
                async with asyncio.timeout(90):
                    await self._read(row, owner)
                document.update(settled=True, stage="finished")
        except asyncio.CancelledError:
            document.update(
                state="unknown" if sent else "failed",
                stage="finished",
                acceptance="unknown" if sent else "not_sent",
                settled=not sent,
                can_refresh=sent,
                reason_codes=["infrastructure_work_interrupted"],
            )
            raise
        except NodeRefusal as error:
            document.update(
                state="failed",
                stage="finished",
                acceptance="rejected",
                settled=True,
                can_refresh=False,
                reason_codes=[error.code],
                result=None,
            )
        except (Fault, OSError) as error:
            code = (
                error.code
                if isinstance(error, Fault)
                else "infrastructure_control_timeout"
                if isinstance(error, TimeoutError)
                else "infrastructure_store_unavailable"
            )
            stale = code == "revision_conflict"
            document.update(
                state="unknown" if sent else "failed",
                stage="finished",
                acceptance="unknown" if sent else "not_sent",
                settled=not sent or stale,
                can_refresh=sent and not stale,
                reason_codes=[code],
            )
            if row["kind"] in {"inventory", "detail"}:
                scope = "control.inventory"
                if row["stack_id"]:
                    scope = "control.stack:" + row["stack_id"]
                elif row["container_id"]:
                    scope = "control.container:" + row["container_id"]
                try:
                    if await self.local(self.runtime.current, row["node_id"], row["revision"]):
                        await self.local(
                            self.runtime.repo.record,
                            row["node_id"],
                            scope,
                            row["revision"],
                            self.runtime.failure(row["node_id"], scope, code),
                        )
                except (Fault, OSError):
                    pass
        finally:
            now = self.clock()
            document["updated_at"] = utc(now)
            document["finished_at"] = utc(now) if document["settled"] else None
            try:
                await self.local(
                    self.repo.finish,
                    operation_id,
                    owner,
                    document,
                    now,
                    retry_after=2 if not document["settled"] else 0,
                )
            except (Fault, OSError):
                pass  # Durable sent bit survives even if this receipt cannot be committed.
        if (
            self.started
            and row["kind"] in MUTATIONS
            and document["settled"]
            and document["result"]
            and document["result"]["execution_active"] is False
        ):
            try:
                if await self.local(self.runtime.current, row["node_id"], row["revision"]):
                    await self.request_read(row["node_id"])
                    if row["stack_id"]:
                        await self.request_read(row["node_id"], stack_id=row["stack_id"])
                    if row["container_id"]:
                        await self.request_read(row["node_id"], container_id=row["container_id"])
                    await self.runtime.request(row["node_id"])
            except (Fault, OSError):
                pass  # The durable receipt remains readable if a fresh observation cannot queue.

    def lookup(self, *, operation_id=None, client_id=None):
        require(self.repo is not None, "infrastructure_control_not_configured", 503)
        row = self.repo.get(operation_id, client_id=client_id)
        require(row is not None, "infrastructure_operation_not_found", 404)
        return copy.deepcopy(row["document"])

    def task_record(self, row):
        return {
            "task_id": row["operation_id"],
            "state": row["state"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "document": row["document"],
        }

    def history(self, *, after=None, limit=50, states=None):
        if self.repo is None:
            return [], False
        try:
            rows, more = self.repo.page(after=after, limit=limit, states=states, kinds=MUTATIONS)
        except Fault:
            return None
        return [self.task_record(row) for row in rows], more

    def history_count(self):
        if self.repo is None:
            return 0
        try:
            return self.repo.count(kinds=MUTATIONS)
        except Fault:
            return None

    def record(self, identity):
        row = self.repo.get(identity) if self.repo else None
        return self.task_record(row) if row and row["kind"] in MUTATIONS else None
