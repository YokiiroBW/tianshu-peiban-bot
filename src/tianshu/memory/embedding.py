"""One configured, versioned 1024-dimensional profile; no local-model assumptions."""

from dataclasses import replace
import math
import unicodedata
from uuid import NAMESPACE_URL, uuid5

from tianshu.models.types import ModelRequest
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.types import ServiceClass
from .retrieval_budget import MODEL_EXECUTION_SECONDS


PREPROCESSING_VERSION = "unicode-nfc-v1"


def normalized_input(text):
    return unicodedata.normalize("NFC", text)


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
        # The space key describes the model and preprocessing, not dimensions
        # alone. A profile change never makes old vectors valid for a new model.
        identity = {name: profile.get(name) for name in ("route_id", "route_fingerprint", "provider_id", "model",
            "model_version", "dimensions", "normalization", "query_instruction", "space_id")}
        return profile, body_hash({"profile": identity, "preprocessing": PREPROCESSING_VERSION})

    @staticmethod
    def request(context, text, budget, *, intent, profile, fingerprint, query=False,
                source_refs=(), service_class=ServiceClass.ELASTIC, max_queue_wait=None,
                execution_seconds=None, business_deadline_at=None, data_generation=None):
        prefix = profile.get("query_instruction", "") if query else ""
        return ModelRequest(
            intent, str(uuid5(NAMESPACE_URL, intent + ":" + fingerprint)), "embed",
            service_class, context, budget, (), required_capabilities=frozenset({"embed"}),
            max_output_tokens=0, native_payload={"input": [normalized_input(prefix + text)]},
            source_refs=tuple(source_refs), max_queue_wait=max_queue_wait,
            route_id=profile.get("route_id"), route_revision=profile.get("route_revision"),
            execution_timeout_seconds=execution_seconds if execution_seconds is not None else MODEL_EXECUTION_SECONDS,
            business_deadline_at=business_deadline_at, data_generation=data_generation,
        )

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
        execution_seconds=None,
        business_deadline_at=None,
        data_generation=None,
    ):
        # A query's existence check and its embedding use the same profile.
        # This is request-local reuse, never a second profile cache.
        budget.check()
        profile, fingerprint = profile_snapshot if profile_snapshot is not None else await self.profile()
        budget.check()
        request = self.request(context, text, budget, intent=intent, profile=profile,
            fingerprint=fingerprint, query=query, source_refs=source_refs, service_class=service_class,
            max_queue_wait=max_queue_wait, execution_seconds=execution_seconds,
            business_deadline_at=business_deadline_at, data_generation=data_generation)
        step_id = intent + ":" + fingerprint
        if not query:
            try:
                _, original = await self.steps.recorded(step_id)
            except AppError as error:
                if error.status != 404 or error.code != "memory_model_step_unavailable":
                    raise
            else:
                # Keep the original execution policy, provider session scope and
                # route. Compare today's semantic input/authority against that
                # frozen request; ModelSteps still rejects any changed meaning.
                request = replace(original,
                    intent_id=request.intent_id, attempt_id=request.attempt_id,
                    function=request.function, service_class=request.service_class,
                    context=request.context, budget=request.budget, messages=request.messages,
                    system=request.system, tools=request.tools,
                    required_capabilities=request.required_capabilities,
                    max_output_tokens=request.max_output_tokens,
                    native_payload=request.native_payload, source_refs=request.source_refs)
        result = (
            await self.models.generate(request)
            if query
            else await self.steps.run(step_id, request)
        )
        return self.decode(result, profile, fingerprint)

    @staticmethod
    def decode(result, profile, fingerprint):
        if result.receipt.status != "succeeded":
            raise AppError(result.receipt.error_code or "embedding_outcome_unknown", status=503)
        # The provider may select an alternate route after the profile read.
        # A successful vector is usable only when its receipt proves the same
        # space; equal dimensions do not establish compatibility.
        for field in ("route_id", "route_revision", "provider_id", "model", "quota_group"):
            expected = profile.get(field)
            if expected is not None and getattr(result.receipt, field, None) != expected:
                raise AppError("embedding_profile_rebuild_required", status=503,
                               details={"reason": "embedding_receipt_profile_mismatch", "field": field})
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
