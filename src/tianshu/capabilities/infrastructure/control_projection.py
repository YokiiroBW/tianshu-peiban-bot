"""Small public projections; node secrets, execution payloads and raw output stay private."""

ACTION_TITLES = {
    "container.start": "启动容器",
    "container.stop": "停止容器",
    "container.restart": "重启容器",
    "stack.start": "启动 Compose 栈",
    "stack.stop": "停止 Compose 栈",
    "stack.restart": "重启 Compose 栈",
    "create": "创建 Compose 栈",
    "configure": "部署 Compose 栈配置",
    "update": "更新 Compose 栈镜像",
    "restore": "恢复 Compose 栈版本",
}


def maintenance(protection=None):
    protection = protection or {}
    return {
        "required": bool(protection.get("protected")),
        "owner": protection.get("owner"),
        "reason_codes": [protection["reason"]] if protection.get("reason") else [],
    }


def actions(names, available, protection, *, fresh=True, prefix="stack"):
    result = {}
    for name in names:
        remote = "deploy" if name == "configure" else name
        allowed = remote in available or prefix + "." + remote in available
        code = (
            "infrastructure_maintenance_required"
            if protection.get("protected")
            else "infrastructure_observation_stale"
            if not fresh
            else "infrastructure_action_unavailable"
        )
        result[name] = {
            "enabled": bool(allowed and fresh and not protection.get("protected")),
            "reason_codes": [] if allowed and fresh and not protection.get("protected") else [code],
        }
    return result


def health(value):
    return {"not_configured": "none"}.get(value, value)


def stack(value, *, fresh=True):
    return {
        "stack_id": value["id"],
        "project_name": value["project_name"],
        "display_name": value["name"],
        "revision": value["revision"],
        "ownership": value["owner"],
        "exists": value["compose_file"] is not None,
        "runtime_state": value["runtime"]["state"],
        "health_state": health(value["runtime"]["health"]),
        "containers": [item["id"] for item in value["runtime"]["containers"]],
        "maintenance": maintenance(value["protection"]),
        "allowed_actions": actions(
            ("start", "stop", "restart", "update", "configure", "restore"),
            value["allowed_actions"],
            value["protection"],
            fresh=fresh,
        ),
    }


def container(value, stack_id=None, *, fresh=True):
    return {
        "container_id": value["id"],
        "stack_id": stack_id,
        "maintenance": maintenance(value["protection"]),
        "allowed_actions": actions(
            ("start", "stop", "restart"),
            value["allowed_actions"],
            value["protection"],
            fresh=fresh,
            prefix="container",
        ),
    }


def plan(value, node_id, revision, protection=None):
    def image(row):
        policy = {"always": "始终拉取", "missing": "本地缺失时拉取", "never": "使用本地镜像"}.get(
            row.get("pull_policy"), row.get("pull_policy") or "未提供拉取策略"
        )
        facts = ["将拉取" if row["pull"] else "不拉取", "策略：" + policy]
        if row.get("current_image_id"):
            facts.append("当前 ID：" + row["current_image_id"])
        if row.get("target_image_id"):
            facts.append("目标 ID：" + row["target_image_id"])
        before = row["from"] or "未提供当前镜像引用"
        return row["service"] + "：" + before + " → " + row["to"] + "（" + "；".join(facts) + "）"

    def port(row):
        host = row["host_ip"] or ""
        if ":" in host:
            host = "[" + host + "]"
        source = (host + ":" if host else "") + (row["published"] or "自动分配主机端口")
        return row["service"] + "：" + source + " → " + str(row["target"]) + "/" + row["protocol"]

    def mount(row):
        kind = {"bind": "宿主目录", "volume": "数据卷", "tmpfs": "临时文件系统"}[row["kind"]]
        source = row["source"] or ("匿名卷" if row["kind"] == "volume" else kind)
        access = "只读" if row["read_only"] else "可写"
        return (
            row["service"]
            + "："
            + source
            + " → "
            + row["target"]
            + "（"
            + kind
            + "，"
            + access
            + "）"
        )

    action = "configure" if value["action"] == "deploy" else value["action"]
    change = ACTION_TITLES[action] + "：" + value["project_name"]
    if action == "restore":
        change += "（恢复配置与运行版本，不恢复数据写入）"

    return {
        "plan_id": value["plan_id"],
        "node_id": node_id,
        "connection_revision": revision,
        "stack_id": value["stack_id"],
        "project_name": value["project_name"],
        "action": action,
        "expected_revision": value["base_revision"],
        "expires_at": value["expires_at"],
        "executable": value["state"] == "ready" and not value["blockers"],
        "changes": [change],
        "diff": value["config_diff"],
        "images": [image(row) for row in value["images"]],
        "ports": [port(row) for row in value["ports"]],
        "mounts": [mount(row) for row in value["mounts"]],
        "warnings": value["warnings"],
        "blockers": value["blockers"],
        "maintenance": maintenance(protection),
    }


def version(value, *, can_restore):
    return {
        "version_id": value["version_id"],
        "created_at": value["created_at"],
        "label": value["version_id"],
        "current": False,
        "restorable": can_restore,
        "reason_codes": [] if can_restore else ["infrastructure_action_unavailable"],
    }


def receipt(value):
    command, state = value["command"], value["state"]
    active = value["execution_active"]
    result = {
        "runtime_state": value["runtime"]["state"],
        "health_state": health(value["runtime"]["health"]),
        "containers": [
            {"container_id": row["id"], "state": row["state"], "health": row["health"]}
            for row in value["runtime"]["containers"]
        ],
        "version_id": value["version_id"],
        "node_operation_id": value["operation_id"],
        "command_state": command["state"],
        "execution_active": active,
    }
    public_state = {
        "accepted": "running",
        "running": "running",
        "succeeded": "observed",
        "failed": "failed",
        "unknown": "unknown",
        "rejected": "failed",
    }[state]
    if state == "succeeded" and command["state"] != "succeeded":
        public_state = "unknown"
    if public_state == "observed" and (
        value["runtime"]["state"] in {"partial", "unknown"}
        or value["runtime"]["health"] in {"unhealthy", "starting", "mixed", "unknown"}
    ):
        public_state = "partial"
    acceptance = (
        "rejected" if state == "rejected" else "unknown" if state == "unknown" else "submitted"
    )
    return {
        "state": public_state,
        "stage": value["phase"],
        "acceptance": acceptance,
        "settled": active is False and state not in {"accepted", "running"},
        "reason_codes": sorted(set(value["reason_codes"] + command["reason_codes"]))[:100],
        "can_refresh": active is not False or public_state in {"running", "unknown"},
        "result": result,
    }
