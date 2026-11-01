"""One configured, versioned 1024-dimensional profile; no local-model assumptions."""

from dataclasses import replace
import math
from uuid import NAMESPACE_URL, uuid5

from tianshu.models.types import ModelRequest
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.types import ServiceClass


class Embeddings:
    def __init__(self, database, models, steps):
        self.database, self.models, self.steps = database, models, steps

    async def profile(self):
        profile = await self.models.embedding_profile()
        if not profile or not profile.get("configured", True):
            raise AppError("embedding_profile_not_configured", status=503)
        profile = profile.get("profile", profile)
        if profile.get("dimensions") != 1024 or profile.get("normalization") not in {"none", "l2"}:
            raise AppError("invalid_embedding_profile", status=503)
        return profile, body_hash(profile)

    async def vector(
        self,
        context,
        text,
        budget,
        *,
        intent,
        query=False,
        source_refs=(),
        service_class=ServiceClass.ELASTIC,
        profile_snapshot=None,
        max_queue_wait=None,
    ):
        # A query's existence check and its embedding use the same profile.
        # This is request-local reuse, never a second profile cache.
        budget.check()
        profile, fingerprint = profile_snapshot if profile_snapshot is not None else await self.profile()
        budget.check()
        prefix = profile.get("query_instruction", "") if query else ""
        request = ModelRequest(
            intent,
            str(uuid5(NAMESPACE_URL, intent + ":" + fingerprint)),
            "embed",
            service_class,
            context,
            budget,
            (),
            required_capabilities=frozenset({"embed"}),
            max_output_tokens=0,
            native_payload={"input": [prefix + text]},
            source_refs=tuple(source_refs),
        )
        if max_queue_wait is not None:
            request = replace(request, max_queue_wait=min(request.max_queue_wait, max_queue_wait))
        result = (
            await self.models.generate(request)
            if query
            else await self.steps.run(intent + ":" + fingerprint, request)
        )
        if result.receipt.status != "succeeded":
            raise AppError(result.receipt.error_code or "embedding_outcome_unknown", status=503)
        payload = result.native_response or {}
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise AppError("invalid_embedding_response", status=502)
        values = data[0].get("embedding")
        if (
            not isinstance(values, list)
            or len(values) != 1024
            or any(type(v) not in {int, float} or not math.isfinite(v) for v in values)
        ):
            raise AppError("invalid_embedding_dimensions", status=502)
        norm = math.sqrt(sum(v * v for v in values))
        if not norm:
            raise AppError("invalid_embedding_zero", status=502)
        if profile["normalization"] == "l2":
            values = [v / norm for v in values]
        return values, fingerprint
