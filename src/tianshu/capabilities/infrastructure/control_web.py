"""Administrator HTTP validation and cached projections. No route awaits an executor exchange."""

import base64
import re
import uuid

from ..contracts import Fault, canonical, digest, loads, require
from . import control_projection as projection
from .control import TASK_STATES
from .web import container_id, node_id


def identifier(value, *, stack=False, client=False):
    require(isinstance(value, str), "invalid_input", 400)
    if client:
        try:
            require(str(uuid.UUID(value)) == value, "invalid_input", 400)
        except ValueError:
            raise Fault("invalid_input", 400) from None
    else:
        pattern = r"stack_[a-f0-9]{24}" if stack else r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}"
        require(re.fullmatch(pattern, value), "invalid_input", 400)
    return value


def revision(value):
    require(type(value) is int and value > 0, "invalid_input", 400)
    return value


def fingerprint(value):
    require(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value), "invalid_input", 400)
    return value


def refresh(body):
    require(type(body.get("refresh", False)) is bool, "invalid_input", 400)
    return body.get("refresh", False)


def observation(value):
    return {key: value[key] for key in ("status", "freshness", "observed_at", "reason_codes")}


async def read_snapshot(control, node, *, stack_id=None, container_id=None, requested=False):
    def read():
        registered = control.runtime.definition(node)
        version = registered["value"]["connection_revision"]
        key = "control.inventory"
        if stack_id:
            key = "control.stack:" + stack_id
        elif container_id:
            key = "control.container:" + container_id
        sample = control.runtime.observation(
            node, key, version, enabled=registered["value"]["enabled"]
        )
        latest = control.repo.latest_read(
            node, version, stack_id=stack_id, container_id=container_id
        )
        return registered, sample, latest

    registered, sample, latest = await control.local(read)
    enabled = registered["value"]["enabled"]
    if enabled and (
        requested
        or latest is None
        or (
            sample["freshness"] != "current"
            and latest["document"]["settled"]
            and control.clock() - latest["updated_at"]
            >= control.runtime.config["poll_interval_seconds"]
        )
    ):
        latest_document = await control.request_read(
            node, stack_id=stack_id, container_id=container_id
        )
    else:
        latest_document = latest["document"] if latest else None
    return registered["value"]["connection_revision"], sample, latest_document


async def view(control, body):
    require(
        {"node_id"} <= set(body) <= {"node_id", "refresh", "query", "page", "page_size"},
        "invalid_input",
        400,
    )
    node = node_id(body["node_id"])
    query, page, size = body.get("query", ""), body.get("page", 0), body.get("page_size", 50)
    require(
        isinstance(query, str)
        and len(query) <= 128
        and type(page) is int
        and 0 <= page <= 10000
        and type(size) is int
        and 1 <= size <= 200,
        "invalid_input",
        400,
    )
    requested = refresh(body)
    result = {
        "configured": control.configured,
        "node_id": node,
        "connection_revision": 0,
        "capabilities": {
            "available": False,
            "reason_codes": ["infrastructure_control_not_configured"],
            "create": {"enabled": False, "reason_codes": ["infrastructure_control_not_configured"]},
        },
        "stacks": [],
        "containers": [],
        "total": 0,
        "page": page,
        "page_size": size,
        "has_more": False,
        "observation": {
            "status": "unsupported",
            "freshness": "missing",
            "observed_at": None,
            "reason_codes": ["infrastructure_control_not_configured"],
        },
        "refresh": None,
    }
    if not control.configured:
        return result
    version, sample, latest = await read_snapshot(control, node, requested=requested)
    fresh = sample["freshness"] == "current"
    data = sample["data"] or {}
    rows = [projection.stack(row, fresh=fresh) for row in data.get("stacks", [])]
    selected = [
        row
        for row in rows
        if not query
        or query.casefold()
        in (
            row["display_name"] + " " + (row["project_name"] or "") + " " + row["stack_id"]
        ).casefold()
    ]
    selected.sort(key=lambda row: (row["display_name"].casefold(), row["stack_id"]))

    def containers():
        owners = {identity: row["stack_id"] for row in rows for identity in row["containers"]}
        found = []
        for key in control.runtime.repo.keys(node, "control.container:", version):
            saved = control.runtime.observation(node, key, version)
            value = (saved["data"] or {}).get("container")
            if value:
                found.append(
                    projection.container(
                        value, owners.get(value["id"]), fresh=saved["freshness"] == "current"
                    )
                )
        return found

    can_create = fresh and "stack.plan" in data.get("capabilities", [])
    result.update(
        connection_revision=version,
        stacks=selected[page * size : (page + 1) * size],
        containers=await control.local(containers),
        total=len(selected),
        has_more=(page + 1) * size < len(selected),
        observation=observation(sample),
        refresh=latest,
        capabilities={
            "available": fresh,
            "reason_codes": sample["reason_codes"],
            "create": {
                "enabled": can_create,
                "reason_codes": [] if can_create else ["infrastructure_action_unavailable"],
            },
        },
    )
    return result


async def detail(control, body):
    require(
        {"node_id"} <= set(body) <= {"node_id", "stack_id", "container_id", "refresh"}
        and ("stack_id" in body) != ("container_id" in body),
        "invalid_input",
        400,
    )
    node = node_id(body["node_id"])
    stack = identifier(body["stack_id"], stack=True) if "stack_id" in body else None
    container = container_id(body["container_id"]) if "container_id" in body else None
    version, sample, latest = await read_snapshot(
        control, node, stack_id=stack, container_id=container, requested=refresh(body)
    )
    result = {
        "node_id": node,
        "connection_revision": version,
        "observation": observation(sample),
        "refresh": latest,
    }
    data, fresh = sample["data"] or {}, sample["freshness"] == "current"
    if container:
        value = data.get("container")
        result["container"] = projection.container(value, fresh=fresh) if value else None
    else:
        value = data.get("stack")
        projected = projection.stack(value, fresh=fresh) if value else None
        result.update(
            stack=projected,
            compose_content=value["compose_content"] if value else None,
            versions=[
                projection.version(
                    row,
                    can_restore=projected["allowed_actions"]["restore"]["enabled"],
                )
                for row in data.get("versions", [])
            ]
            if value
            else [],
        )
    return result


async def submit(control, kind, body):
    base = {"node_id", "expected_connection_revision", "client_id"}
    require(base <= set(body), "invalid_input", 400)
    node = node_id(body["node_id"])
    version = revision(body["expected_connection_revision"])
    client = identifier(body["client_id"], client=True)
    stack, container, action, lock_key = None, None, None, None
    if kind == "execute":
        require(set(body) == base | {"plan_id"}, "invalid_input", 400)
        plan_id = identifier(body["plan_id"])
        # A replay uses its persisted target even after a plan expired or its source changed.
        existing = await control.local(control.repo.get, client_id=client)
        if existing:
            stack = existing["stack_id"]
        else:
            plan = await control.local(control.execution_plan, node, version, plan_id)
            stack = plan["stack_id"]
        action, operation = "stack.apply", "operation.submit"
        params = {
            "operation_id": "infra-control:" + client,
            "action": action,
            "target": {"plan_id": plan_id},
        }
    elif kind == "action":
        action = body.get("action")
        require(
            action
            in {
                "container.start",
                "container.stop",
                "container.restart",
                "stack.start",
                "stack.stop",
                "stack.restart",
            },
            "invalid_input",
            400,
        )
        if action.startswith("container."):
            require(set(body) == base | {"action", "container_id"}, "invalid_input", 400)
            container = container_id(body["container_id"])
            target = {"container_id": container}
            # Lock by its actual Compose membership, even when issued from the container view.
            inventory = await control.local(
                control.runtime.observation, node, "control.inventory", version
            )
            stack = next(
                (
                    row["id"]
                    for row in (inventory["data"] or {}).get("stacks", [])
                    if any(item["id"] == container for item in row["runtime"]["containers"])
                ),
                None,
            )
            lock_key = stack or "node:" + node  # Unknown membership serializes conservatively.
        else:
            require(
                set(body) == base | {"action", "stack_id", "expected_revision"},
                "invalid_input",
                400,
            )
            stack = identifier(body["stack_id"], stack=True)
            target = {
                "stack_id": stack,
                "expected_revision": fingerprint(body["expected_revision"]),
            }
        operation = "operation.submit"
        params = {"operation_id": "infra-control:" + client, "action": action, "target": target}
    else:
        action = body.get("action")
        require(action in {"create", "configure", "update", "restore"}, "invalid_input", 400)
        selected = {"action"}
        params = {"action": "deploy" if action == "configure" else action}
        if action == "create":
            selected |= {"project_name", "compose_content"}
            project = body.get("project_name")
            require(
                isinstance(project, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", project),
                "invalid_input",
                400,
            )
            params["project_name"] = project
            lock_key = "project:" + project
        else:
            selected |= {"stack_id", "expected_revision"}
            stack = identifier(body.get("stack_id"), stack=True)
            params.update(
                stack_id=stack, expected_revision=fingerprint(body.get("expected_revision"))
            )
        if action == "restore":
            selected.add("restore_version_id")
            params["restore_version_id"] = identifier(body.get("restore_version_id"))
        if action in {"create", "configure"}:
            selected.add("compose_content")
            content = body.get("compose_content")
            require(
                isinstance(content, str) and 1 <= len(content) <= 262144 and "\x00" not in content,
                "invalid_input",
                400,
            )
            params["compose_content"] = content
        require(set(body) == base | selected, "invalid_input", 400)
        operation = "stack.plan"
    document = await control.accept(
        kind,
        node,
        version,
        client,
        operation,
        params,
        stack_id=stack,
        container_id=container,
        action=action,
        lock_key=lock_key,
    )
    return {"operation": document}


def operations(control, body):
    require(
        set(body) <= {"node_id", "stack_id", "state", "page_size", "cursor"}, "invalid_input", 400
    )
    filters = {
        "node_id": node_id(body["node_id"]) if "node_id" in body else None,
        "stack_id": identifier(body["stack_id"], stack=True) if "stack_id" in body else None,
        "state": body.get("state"),
    }
    require(filters["state"] is None or filters["state"] in TASK_STATES, "invalid_input", 400)
    size = body.get("page_size", 20)
    require(type(size) is int and 1 <= size <= 100, "invalid_input", 400)
    after = None
    if body.get("cursor") is not None:
        cursor = body["cursor"]
        require(isinstance(cursor, str) and 0 < len(cursor) <= 1024, "invalid_input", 400)
        try:
            decoded = loads(base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True))
        except (ValueError, UnicodeError):
            raise Fault("invalid_input", 400) from None
        require(
            isinstance(decoded, dict) and set(decoded) == {"filter", "after"}, "invalid_input", 400
        )
        require(decoded["filter"] == digest(filters), "cursor_conflict", 409)
        after = decoded["after"]
        require(
            isinstance(after, list)
            and len(after) == 2
            and type(after[0]) is int
            and isinstance(after[1], str),
            "invalid_input",
            400,
        )
    rows, more = control.repo.page(
        after=after,
        limit=size,
        node_id=filters["node_id"],
        stack_id=filters["stack_id"],
        states={filters["state"]} if filters["state"] else None,
        kinds={"plan", "action", "execute"},
    )
    cursor = None
    if rows and more:
        last = rows[-1]
        cursor = base64.urlsafe_b64encode(
            canonical(
                {
                    "filter": digest(filters),
                    "after": [int(last["created_at"]), last["operation_id"]],
                }
            ).encode()
        ).decode("ascii")
    return {
        "items": [row["document"] for row in rows],
        "page": {"returned": len(rows), "has_more": more, "next_cursor": cursor},
    }


async def route(control, operation, body):
    if operation == "view":
        return await view(control, body)
    require(control.repo is not None, "infrastructure_control_not_configured", 503)
    if operation == "operations":
        return await control.local(operations, control, body)
    if operation == "operation":
        require(
            set(body) <= {"client_id", "operation_id", "refresh"}
            and ("client_id" in body) != ("operation_id" in body),
            "invalid_input",
            400,
        )
        document = await control.local(
            control.lookup,
            operation_id=identifier(body["operation_id"]) if "operation_id" in body else None,
            client_id=identifier(body["client_id"], client=True) if "client_id" in body else None,
        )
        if refresh(body):
            require(control.configured, "infrastructure_control_not_configured", 503)
            document = await control.refresh_operation(document["operation_id"])
        return {"operation": document}
    require(control.configured, "infrastructure_control_not_configured", 503)
    if operation == "detail":
        return await detail(control, body)
    if operation in {"plan", "action", "execute"}:
        return await submit(control, operation, body)
    raise Fault("not_found", 404)
