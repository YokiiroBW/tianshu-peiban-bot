"""A settled planning response is distinct from a submitted image request."""

from uuid import uuid4

from tianshu.models.completion import content_completion
from tianshu.runtime.serialization import wire
from tianshu.types import Receipt

from ..contracts import Fault, model_json
from ..external import ProviderError


def planning_evidence(result):
    return {"receipt": wire(result.receipt), "completion": wire(content_completion(result))}


class ImagePlanError(ProviderError):
    def __init__(self, code, result):
        self.result = result
        self.settled = result.receipt.status in {"succeeded", "failed", "cancelled"}
        super().__init__(code, 502, effect="none" if self.settled else "unknown")

    def receipt(self, action, attempt):
        return Receipt(
            str(uuid4()), action.action_id, attempt.attempt_id,
            "failed" if self.settled else "unknown", "none", "not_requested",
            result={"phase": "planning", "image_submission": "not_started",
                    "planning": planning_evidence(self.result)},
            error_code=self.code,
        )


def image_plan(result):
    if result.receipt.status != "succeeded" or content_completion(result).state != "complete":
        raise ImagePlanError("image_plan_incomplete", result)
    visible = tuple(part for part in result.parts if not part.native.get("thought"))
    if (any(part.kind != "text" for part in result.parts)
            or any(not isinstance(part.text, str) for part in visible)):
        raise ImagePlanError("image_plan_invalid", result)
    try:
        planned = model_json("".join(part.text for part in visible))
    except Fault:
        raise ImagePlanError("image_plan_invalid", result) from None
    required = {"pose", "expression", "camera", "light"}
    if (not isinstance(planned, dict) or not required <= planned.keys()
            or planned.keys() - required - {"negative_constraints"}
            or any(not isinstance(planned[key], str) for key in required)
            or not isinstance(planned.get("negative_constraints", []), list)
            or any(not isinstance(item, str) for item in planned.get("negative_constraints", []))):
        raise ImagePlanError("image_plan_invalid", result)
    return planned
