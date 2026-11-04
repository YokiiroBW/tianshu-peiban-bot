"""Reconcile each original broker configuration-reload receipt."""

from __future__ import annotations

from tianshu.models.management import configuration
from tianshu.runtime.commands import body_hash
from .recovery import RecoveryAction, RecoveryExecutor


def model_configuration_refresh(runtime):
    async def catalog(conn):
        view = await configuration(conn, runtime.settings)
        policies = sorted(view["quota_groups"], key=lambda item: item["quota_group"])
        return body_hash(policies), [item["quota_group"] for item in policies]

    async def inspect(_incident, target):
        event_id = target.get("material_id")
        if not event_id:
            return {"eligible": False, "reason": "original_configuration_event_missing"}
        original = await runtime.models.configuration_reload_status(event_id)
        if (not isinstance(original, dict) or type(original.get("revision")) is not int
                or original.get("state") not in {"pending", "applied"}
                or target.get("version") not in {None, str(original["revision"])}):
            return {"eligible": False, "reason": "original_configuration_event_changed"}
        async with runtime.database.transaction(control=True, read_only=True) as conn:
            version, groups = await catalog(conn)
        return {"eligible": True, "version": str(original["revision"]), "material_id": event_id,
                "material_digest": body_hash({"event_id": event_id, "revision": original["revision"]}),
                "dependency": "model-broker", "original_state": "failed", "catalog_version": version,
                "groups": groups, "policy_domain": "models", "policy_owner": "operations",
                "policy_subject_id": event_id}

    async def invoke(recovery, _target, facts, attempt_id):
        async with runtime.database.transaction(control=True) as conn:
            await RecoveryExecutor.guard_on(conn, attempt_id, recovery["owner_token"])
            current, _ = await catalog(conn)
            if current != facts["catalog_version"]:
                return {"state": "failed", "reason": "configuration_changed"}
        original = await runtime.models.configuration_reload_status(facts["material_id"])
        if not original or str(original.get("revision")) != facts["version"]:
            return {"state": "failed", "reason": "original_configuration_event_changed"}
        if original.get("state") == "applied":
            return {"state": "accepted", "original_event_id": facts["material_id"]}
        if original.get("state") != "pending":
            return {"state": "failed", "reason": "original_configuration_event_changed"}
        response = await runtime.models.reload(configuration_event_id=facts["material_id"],
                                               expected_revision=int(facts["version"]))
        receipt = response.get("configuration_reload") or {}
        if receipt.get("state") != "applied" or receipt.get("event_id") != facts["material_id"]:
            return {"state": "unknown", "reason": "broker_reload_receipt_unavailable"}
        return {"state": "accepted", "original_event_id": facts["material_id"]}

    async def observe(_recovery, _target, facts, _attempt_id):
        original = await runtime.models.configuration_reload_status(facts["material_id"])
        if not original or str(original.get("revision")) != facts["version"]:
            return {"state": "unknown", "reason": "original_configuration_event_unavailable"}
        if original.get("state") != "applied":
            return {"state": "pending", "reason": "original_configuration_event_pending"}
        async with runtime.database.transaction(control=True, read_only=True) as conn:
            current, groups = await catalog(conn)
        if current != facts["catalog_version"]:
            return {"state": "unknown", "reason": "configuration_changed_after_reload"}
        try:
            health = await runtime.models.snapshot()
        except Exception:
            return {"state": "pending", "reason": "broker_observation_unavailable"}
        if health.get("status") != "ready" or not set(groups) <= set(health.get("quota_groups") or {}):
            return {"state": "pending", "reason": "broker_observation_not_ready"}
        return {"state": "verified", "version": facts["version"], "verified_groups": len(groups),
                "observation_ref": "models.configuration_reload:" + facts["material_id"]}

    return RecoveryAction("model_configuration_refresh",
                          frozenset({"broker_reload_pending", "model_configuration_refresh_required"}),
                          inspect, invoke, observe, max_attempts=3, unknown_observable=True)
