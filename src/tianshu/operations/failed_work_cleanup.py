"""Execute an explicit immutable cleanup manifest through each domain's retirement port.

The maintenance CLI requires the already authorized operator's manifest on stdin.
It opens no Models/Channels client and never invokes or retries business work.
"""

import argparse
import asyncio
import json
import sys

from tianshu.companion.work_retirement import retire_failed_batch_on as retire_growth_on
from tianshu.memory.work_retirement import retire_failed_batch_on as retire_memory_on
from tianshu.runtime.config import Settings
from tianshu.runtime.database import Database
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.runtime.work_retirement import retire_failed_job_on
from .work_retirement import archive_incident_on


async def retire_native_on(connection, target, **request):
    # Operations supplies business correlation; Runtime only owns native mechanics.
    unknown = await (await connection.execute(
        "SELECT 1 FROM procrastinate_jobs j JOIN capabilities.actions a ON a.id IN "
        "(j.args->>'action_id',j.args#>>'{event,action_id}',"
        "(SELECT action_id FROM life.activities WHERE id=j.args->>'activity_id')) "
        "WHERE j.id=%s AND (a.receipt->>'execution'='unknown' OR a.receipt->>'effect'='unknown') LIMIT 1",
        (target["id"],))).fetchone()
    if unknown:
        return {"state": "skipped", "reason": "unknown_action_retained"}
    return await retire_failed_job_on(connection, target, **request)


PORTS = {"runtime": retire_native_on, "memory": retire_memory_on,
         "growth": retire_growth_on, "operations": archive_incident_on}
TARGET_FIELDS = {
    "runtime": {"id": int, "attempts": int, "args_digest": str},
    "memory": {"batch_id": str, "manifest_digest": str, "updated_at": str},
    "growth": {"actor_id": str, "scope_key": str, "retry_key": str, "revision": int},
    "operations": {"incident_id": str, "revision": int, "episode": int, "occurrences": int, "last_seen": str},
}


async def execute_plan(database, plan, *, apply=False):
    if not isinstance(plan, dict) or set(plan) != {"generation", "request_ref", "requested_by", "targets"}:
        raise AppError("invalid_cleanup_manifest")
    for name in ("generation", "request_ref", "requested_by"):
        value = plan[name]
        if not isinstance(value, str) or not value or len(value) > 256 or any(ord(c) < 32 for c in value):
            raise AppError("invalid_cleanup_manifest")
    targets = plan["targets"]
    if not isinstance(targets, list) or len(targets) > 10000:
        raise AppError("invalid_cleanup_manifest")
    if any(not isinstance(t, dict) or set(t) != {"owner", "target"}
            or t["owner"] not in PORTS or not isinstance(t["target"], dict) for t in targets):
        raise AppError("invalid_cleanup_manifest")
    for item in targets:
        fields, target = TARGET_FIELDS[item["owner"]], item["target"]
        if set(target) != set(fields) or any(type(target[k]) is not kind for k, kind in fields.items()):
            raise AppError("invalid_cleanup_manifest")
    request = {name: plan[name] for name in ("generation", "request_ref", "requested_by")}
    results = []
    for item in targets:
        owner, target = item["owner"], item["target"]
        try:
            async with database.transaction() as connection:
                result = await PORTS[owner](connection, target, apply=apply, **request)
        except AppError as error:
            if error.code == "job_generation_expired":
                raise
            result = {"state": "unavailable", "reason": error.code}
        results.append({"owner": owner, "target": target, **result})
    counts = {}
    for result in results:
        key = result["owner"] + ":" + result["state"]
        counts[key] = counts.get(key, 0) + 1
    return {"mode": "apply" if apply else "preview", "request_ref": plan["request_ref"],
            "generation": plan["generation"], "counts": counts, "results": results}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", default="/config/settings.json")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    plan = json.load(sys.stdin)
    settings = Settings.load(args.settings)
    database = Database(settings.database("operations"), role="operations")
    await database.open()
    try:
        result = await execute_plan(database, plan, apply=args.apply)
    finally:
        await database.close()
    print(json.dumps(wire(result)))


if __name__ == "__main__":
    asyncio.run(main())
