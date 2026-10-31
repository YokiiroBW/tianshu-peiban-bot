"""Same-origin administrator projections; no HTTP request waits for SSH collection."""

import re

from ..contracts import Fault, require
from .config import node_id
from .runtime import MAX_NODES, PREFIX

HTTP_PREFIX = "/api/v1/infrastructure/"
STATES = {"created", "restarting", "running", "removing", "paused", "exited", "dead", "unknown"}
HEALTH = {"healthy", "unhealthy", "starting", "none", "unknown"}


def container_id(value):
    require(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value), "invalid_input", 400)
    return value


class WebInfrastructure:
    def __init__(self,infrastructure,authority,local):
        self.runtime,self.authority,self.local=infrastructure,authority,local
    def _gate(self,session):
        require("*" in session.permissions or "external.manage" in session.permissions,"external_manage_required",403)
        return True

    async def route(self, path, body, session):
        await self.authority.check(session)
        self._gate(session)
        require(isinstance(body, dict), "invalid_input", 400)
        operation = path.removeprefix(HTTP_PREFIX)
        runtime = self.runtime
        if operation.startswith("control/"):
            from .control_web import route

            result = await route(runtime.control, operation.removeprefix("control/"), body)
        elif operation == "host":
            require(body == {}, "invalid_input", 400)
            result = await self.local(self._host)
        elif operation == "view":
            require(body == {}, "invalid_input", 400)
            if runtime is None:
                return {
                    "configured": False,
                    "revision": 0,
                    "catalog": None,
                    "nodes": [],
                    "max_nodes": MAX_NODES,
                }
            result = await self.local(runtime.view)
        else:
            require(
                runtime is not None and runtime.config is not None,
                "infrastructure_not_configured",
                503,
            )
            if operation == "catalog/refresh":
                require(body == {}, "invalid_input", 400)
                result = runtime.request_catalog()
            elif operation == "save":
                result = await self.local(self._save, body, session)
                await runtime.changed(result["node_id"])
                result["applied"] = True
                if result["enabled"]:
                    result["refresh"] = await runtime.request(result["node_id"])
            elif operation in {"refresh", "test"}:
                require(set(body) == {"node_id"}, "invalid_input", 400)
                result = await runtime.request(node_id(body["node_id"]))
            elif operation == "node":
                require(set(body) == {"node_id"}, "invalid_input", 400)
                result = await self.local(self._node, node_id(body["node_id"]))
            elif operation == "containers":
                result = await self.local(self._containers, body)
            elif operation == "container":
                require(set(body) == {"node_id", "container_id"}, "invalid_input", 400)
                result = await self.local(
                    self._container, node_id(body["node_id"]), container_id(body["container_id"])
                )
            elif operation in {"logs", "logs/refresh"}:
                require(set(body) == {"node_id", "container_id"}, "invalid_input", 400)
                target, identity = node_id(body["node_id"]), container_id(body["container_id"])
                result = (
                    await runtime.request_logs(target, identity)
                    if operation.endswith("/refresh")
                    else await self.local(runtime.log_view, target, identity)
                )
            else:
                raise Fault("not_found", 404)
        await self.authority.check(session)
        self._gate(session)
        return result

    def _host(self):
        # The deployment names its platform host explicitly; never pick an arbitrary node.
        config = self.runtime.config if self.runtime else None
        identity = config.get("platform_node_id") if config else None
        if identity is None:
            return {"configured": False, "node": None}
        return {"configured": True, "node": self.runtime.node(identity)}

    def _save(self, body, session):
        require(
            set(body) == {"node_id", "label", "enabled", "expected_revision", "client_id"},
            "invalid_input",
            400,
        )
        identity = node_id(body["node_id"])
        label = body["label"]
        require(
            isinstance(label, str)
            and 1 <= len(label.strip()) <= 128
            and label.isprintable()
            and type(body["enabled"]) is bool,
            "invalid_input",
            400,
        )
        _, existing = self.runtime.definitions()
        directory = self.runtime.observation("@catalog", "node.catalog", 0)
        selected = next(
            (row for row in (directory["data"] or {}).get("nodes", []) if row["id"] == identity),
            None,
        )
        require(
            selected is not None or (identity in existing and not body["enabled"]),
            "infrastructure_node_not_in_catalog",
            409,
        )
        require(identity in existing or len(existing) < MAX_NODES, "infrastructure_node_limit", 409)
        kind = selected["kind"] if selected else existing[identity]["value"]["kind"]
        self._gate(session)
        saved = self.runtime.catalog.save(
            PREFIX + identity,
            {"node_id": identity, "label": label.strip(), "kind": kind, "enabled": body["enabled"]},
            {"action": "clear"},
            {"action": "clear"},
            body["expected_revision"],
            body["client_id"],
        )
        current = self.runtime.definition(identity)
        require(
            current["value"]["connection_revision"] == saved["revision"], "revision_conflict", 409
        )
        return {**saved, "node_id": identity, "enabled": body["enabled"]}

    def _node(self, identity):
        result = self.runtime.node(identity)
        result["compose"] = self.runtime.observation(
            identity, "compose.inventory", result["connection_revision"], enabled=result["enabled"]
        )
        return result

    def _inventory(self, identity):
        node = self.runtime.node(identity)
        revision, enabled = node["connection_revision"], node["enabled"]
        node.pop("snapshot")
        inventory = self.runtime.observation(
            identity, "docker.inventory", revision, enabled=enabled
        )
        metrics = self.runtime.observation(identity, "docker.stats", revision, enabled=enabled)
        return node, inventory, metrics

    def _containers(self, body):
        require(
            {"node_id"}
            <= set(body)
            <= {"node_id", "query", "state", "health", "application", "page", "page_size"},
            "invalid_input",
            400,
        )
        identity = node_id(body["node_id"])
        query, state, health = body.get("query", ""), body.get("state"), body.get("health")
        application, page, size = (
            body.get("application"),
            body.get("page", 0),
            body.get("page_size", 100),
        )
        require(
            isinstance(query, str)
            and len(query) <= 128
            and (state is None or state in STATES)
            and (health is None or health in HEALTH)
            and (application is None or isinstance(application, str) and len(application) <= 512)
            and type(page) is int
            and 0 <= page <= 10000
            and type(size) is int
            and 1 <= size <= 200,
            "invalid_input",
            400,
        )
        node, inventory, metrics = self._inventory(identity)
        containers = (inventory["data"] or {}).get("containers", [])
        statistics = {row["id"]: row for row in (metrics["data"] or {}).get("containers", [])}
        applications = {}
        for row in containers:
            project = (row["compose"] or {}).get("project") or ""
            applications[project] = applications.get(project, 0) + 1
        filtered = [
            row
            for row in containers
            if (
                not query
                or query.casefold()
                in (
                    (row["name"] or "") + " " + (row["image_reference"] or "") + " " + row["id"]
                ).casefold()
            )
            and (state is None or row["state"] == state)
            and (health is None or row["health"] == health)
            and (
                application is None or ((row["compose"] or {}).get("project") or "") == application
            )
        ]
        filtered.sort(key=lambda row: ((row["name"] or "").casefold(), row["id"]))
        rows = [
            {
                **{
                    key: row[key]
                    for key in (
                        "id",
                        "name",
                        "image_reference",
                        "state",
                        "health",
                        "availability",
                        "compose",
                    )
                },
                "stats": statistics.get(row["id"]),
            }
            for row in filtered[page * size : (page + 1) * size]
        ]
        return {
            "node": node,
            "containers": rows,
            "total": len(filtered),
            "inventory_total": len(containers),
            "page": page,
            "page_size": size,
            "has_more": (page + 1) * size < len(filtered),
            "applications": [
                {"name": name, "containers": count} for name, count in sorted(applications.items())
            ],
            "inventory": {key: value for key, value in inventory.items() if key != "data"},
            "metrics": {key: value for key, value in metrics.items() if key != "data"},
        }

    def _container(self, identity, container):
        node, inventory, metrics = self._inventory(identity)
        row = next(
            (
                row
                for row in (inventory["data"] or {}).get("containers", [])
                if row["id"] == container
            ),
            None,
        )
        require(row is not None, "infrastructure_container_not_found", 404)
        stats = next(
            (
                row
                for row in (metrics["data"] or {}).get("containers", [])
                if row["id"] == container
            ),
            None,
        )
        return {
            "node": node,
            "container": row,
            "stats": stats,
            "inventory": {key: value for key, value in inventory.items() if key != "data"},
            "metrics": {key: value for key, value in metrics.items() if key != "data"},
        }
