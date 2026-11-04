"""Reroute only a single proven unstarted Action through its owning port."""

from __future__ import annotations

from tianshu.runtime.commands import body_hash
from tianshu.runtime.serialization import wire
from .recovery import RecoveryAction, RecoveryExecutor


def capability_reroute(actions, context_for_action):
    async def inspect(incident, target):
        action_id = target.get("material_id") or incident.get("summary", {}).get("material_id")
        if not action_id:
            return {"eligible": False}
        context = await context_for_action(action_id)
        value = await actions.inspect_recovery(action_id, context)
        attempt, receipt, job = value["attempt"], value["receipt"], value["execution"]
        version = value["result_revision"]
        eligible = (receipt.get("execution") == "pending" and attempt.get("dispatch") == "not_started"
                    and not attempt.get("provider_handle") and not attempt.get("started_at")
                    and not attempt.get("finished_at") and job.get("job_state") == "todo"
                    and type(job.get("job_id")) is int and type(version) is int)
        if target.get("version") is not None and target["version"] != str(version):
            eligible = False
        facts = {"eligible": eligible, "version": str(version), "material_id": action_id,
                 "material_digest": body_hash({"action_id": action_id, "attempt_id": attempt["attempt_id"]}),
                 "dependency": "capability-execution", "original_state": "none", "action_id": action_id,
                 "original_attempt_id": attempt["attempt_id"], "job_id": job.get("job_id"),
                 "result_revision": version, "policy_domain": "capabilities", "policy_owner": "operations",
                 "policy_subject_id": action_id}
        # An owner that already established a shared lineage supplies its exact
        # identity and claim; never substitute a new incident-based budget.
        policy = value.get("recovery_policy")
        if isinstance(policy, dict):
            for key in ("policy_domain", "policy_owner", "policy_subject_id", "material_id",
                        "material_digest", "dependency", "max_attempts", "lineage_id", "claim_token"):
                if key in policy:
                    facts[key] = policy[key]
        return facts

    async def invoke(recovery, _target, facts, attempt_id):
        context = await context_for_action(facts["action_id"])
        receipt = await actions.invoke_registered_recovery(
            facts["action_id"], context, operation="reroute_pending", client_request_id=attempt_id,
            expected_revision=facts["result_revision"], expected_attempt_id=facts["original_attempt_id"],
            expected_job_id=facts["job_id"], guard_on=RecoveryExecutor.guard_on,
            guard_attempt_id=attempt_id, guard_owner_token=recovery["owner_token"])
        receipt = wire(receipt)
        if receipt.get("attempt_id") != facts["original_attempt_id"]:
            return {"state": "unknown", "reason": "original_attempt_changed"}
        return {"state": "accepted", "original_attempt_id": facts["original_attempt_id"]}

    async def observe(_recovery, _target, facts, _attempt_id):
        context = await context_for_action(facts["action_id"])
        value = await actions.observe_recovery(facts["action_id"], context,
                                               expected_attempt_id=facts["original_attempt_id"])
        receipt = value["receipt"]
        execution = receipt.get("execution")
        if execution in {"pending", "running"}:
            return {"state": "pending", "reason": "original_action_pending"}
        if execution == "unknown" or receipt.get("effect") == "unknown" or receipt.get("delivery") == "unknown":
            return {"state": "unknown", "reason": "original_effect_unknown"}
        if execution != "succeeded" or receipt.get("delivery") == "failed":
            return {"state": "failed", "reason": "original_action_not_succeeded"}
        coverage = receipt.get("coverage")
        if isinstance(coverage, dict) and not coverage.get("complete", False):
            return {"state": "failed", "reason": "original_result_incomplete"}
        return {"state": "verified", "original_attempt_id": facts["original_attempt_id"],
                "result_revision": value["result_revision"]}

    return RecoveryAction("capability_reroute_pending",
                          frozenset({"execution_lane_unresolved", "job_routing_blocked", "capability_wrong_lane"}),
                          inspect, invoke, observe, domain_managed=True)
