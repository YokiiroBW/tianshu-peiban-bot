"""Refresh the existing broker quota catalogue and verify the applied generation."""

from __future__ import annotations

from tianshu.models.management import configuration
from tianshu.runtime.commands import body_hash
from .recovery import RecoveryAction, RecoveryExecutor


def model_configuration_refresh(runtime):
    async def catalog(conn):
        view = await configuration(conn, runtime.settings)
        policies = sorted(view["quota_groups"], key=lambda item: item["quota_group"])
        return body_hash(policies), [item["quota_group"] for item in policies]

    async def inspect(_incident, _target):
        async with runtime.database.transaction(control=True, read_only=True) as conn:
            version, groups = await catalog(conn)
        # The action is applicable only when the broker can actually receive it.
        try:
            health = await runtime.models.snapshot()
        except Exception:
            health = None
        return {"eligible": bool(health and health.get("status") == "ready"),
                "version": version, "material_id": "broker-quota-catalog",
                "material_digest": body_hash({"material": "broker-quota-catalog"}),
                "dependency": "model-broker", "original_state": "failed", "groups": groups,
                "policy_domain": "models", "policy_owner": "operations",
                "policy_subject_id": "broker-quota-catalog"}

    async def invoke(recovery, _target, facts, attempt_id):
        # Reload applies only the current authoritative catalogue. A version
        # change invalidates the decision and cannot restore older settings.
        async with runtime.database.transaction(control=True) as conn:
            await RecoveryExecutor.guard_on(conn, attempt_id, recovery["owner_token"])
            current, _ = await catalog(conn)
            if current != facts["version"]:
                return {"state": "failed", "reason": "configuration_changed"}
        response = await runtime.models.reload()
        return {"state": "accepted", "applied_groups": sorted(response.get("quota_groups", []))}

    async def observe(recovery, _target, facts, _attempt_id):
        async with runtime.database.transaction(control=True, read_only=True) as conn:
            current, groups = await catalog(conn)
        if current != facts["version"]:
            return {"state": "failed", "reason": "configuration_changed"}
        receipt = recovery["result"]["receipt"]
        if receipt.get("applied_groups") != sorted(groups):
            return {"state": "failed", "reason": "broker_reload_incomplete"}
        try:
            health = await runtime.models.snapshot()
        except Exception:
            return {"state": "pending", "reason": "broker_observation_unavailable"}
        if health.get("status") != "ready" or not set(groups) <= set(health.get("quota_groups") or {}):
            return {"state": "pending", "reason": "broker_observation_not_ready"}
        return {"state": "verified", "version": current, "verified_groups": len(groups)}

    return RecoveryAction("model_configuration_refresh",
                          frozenset({"broker_reload_pending", "model_configuration_refresh_required"}),
                          inspect, invoke, observe, max_attempts=3, resolves_dependency=True)
