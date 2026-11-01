"""Prove a finished planning failure preceded every image-side submission."""

import json
from dataclasses import dataclass

from tianshu.runtime.serialization import wire

from ..contracts import Fault, digest
from ..serialization import action_from, receipt_from
from ..withdrawal import validate_sources
from .planning import planning_evidence


@dataclass(frozen=True)
class PlanRecoveryProof:
    snapshot: str
    planning: dict


class ImageRecovery:
    def __init__(self, images):
        self.images = images

    async def snapshot_on(self, connection, action, attempt, *, lock=False):
        row = await (await connection.execute(
            "SELECT action,attempt,receipt,document,result_revision,execution_owner "
            "FROM capabilities.actions WHERE id=%s" + (" FOR UPDATE" if lock else ""),
            (action.action_id,),
        )).fetchone()
        work = await (await connection.execute(
            "SELECT body,submitted,provider_handle FROM capabilities.image_work WHERE action_id=%s"
            + (" FOR UPDATE" if lock else ""), (action.action_id,),
        )).fetchone()
        if not row or action_from(row["action"]) != action or row["attempt"]["attempt_id"] != attempt.attempt_id:
            raise Fault("attempt_changed", 409)
        current = receipt_from(row["receipt"])
        document = json.loads(row["document"])
        if (work is not None or row["execution_owner"] is not None
                or not row["attempt"].get("finished_at")
                or current.execution not in {"failed", "cancelled", "unknown"}
                or current.artifacts or current.effect == "confirmed"
                or current.error_code not in {"image_plan_invalid", "image_plan_incomplete"}
                or document.get("source_revoked") or document.get("cancel_requested")):
            raise Fault("image_resume_unverified", 409)
        return digest({**row, "image_work": work}), current

    async def prove(self, action, attempt, current):
        images = self.images
        await images.authority.check(action.context, action.source_refs)
        async with images.config.database.transaction(read_only=True) as connection:
            snapshot, actual = await self.snapshot_on(connection, action, attempt)
        if actual != current:
            raise Fault("image_recovery_changed", 409)
        plan_id = attempt.attempt_id + ":plan"
        planning = await images.broker.status(plan_id)
        if (planning is None or planning.attempt_id != plan_id
                or planning.intent_id != action.action_id + ":plan"
                or planning.status not in {"succeeded", "failed", "cancelled"}
                or planning.finished_at is None or planning.dispatch == "unknown"):
            raise Fault("image_plan_outcome_unknown", 409)
        result = await images.broker.result(plan_id, context=action.context)
        if result is None or wire(result.receipt) != wire(planning):
            raise Fault("image_plan_outcome_unknown", 409)
        # This is the separate physical image request, not the paid planning request.
        # Missing work alone never establishes that its provider effect is absent.
        if await images.broker.status(attempt.attempt_id) is not None:
            raise Fault("image_submission_unverified", 409)
        return PlanRecoveryProof(snapshot, planning_evidence(result))

    async def validate_on(self, connection, action, attempt, current, proof):
        if not isinstance(proof, PlanRecoveryProof):
            raise Fault("image_resume_unverified", 409)
        if not await validate_sources(
            connection, action.context, action.source_refs, self.images.artifacts.sources,
        ):
            raise Fault("source_revoked", 410)
        snapshot, actual = await self.snapshot_on(connection, action, attempt, lock=True)
        if snapshot != proof.snapshot or actual != current:
            raise Fault("image_recovery_changed", 409)
