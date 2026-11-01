"""Evaluation-only context translation to the existing single production Broker.

The isolated application validates and persists synthetic provenance. Production
Broker receives the explicitly supplied authorized model context and no source
references, because its database contains none of the synthetic Sources. This
adapter neither creates providers/quota groups nor claims production provenance.
"""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import NAMESPACE_URL, uuid5
import json

from .dataset import digest, load_json, write_json


class EvaluationModels:
    def __init__(self, database, sources, config, output, run_id):
        from tianshu.models.client import ModelClient
        from tianshu.models.codec import context_from_wire
        from tianshu.models.types import ModelReceipt, ModelUsage

        class CompatibleClient(ModelClient):
            """Only wire compatibility; the selected checkout owns all Memory behavior."""

            async def _json(client, *args, **kwargs):
                value = await super()._json(*args, **kwargs)

                def adapt(item):
                    if isinstance(item, list):
                        return [adapt(x) for x in item]
                    if not isinstance(item, dict):
                        return item
                    if {"attempt_id", "intent_id", "dispatch", "quota_group", "usage"} <= item.keys():
                        client.receipts[item["attempt_id"]] = item
                        item = {key: data for key, data in item.items()
                                if key in ModelReceipt.__dataclass_fields__}
                        item["usage"] = {key: data for key, data in item["usage"].items()
                                         if key in ModelUsage.__dataclass_fields__}
                        return item
                    return {key: adapt(data) for key, data in item.items()}

                return adapt(value)

        self.database, self.sources = database, sources
        self.model_context = context_from_wire(config["model_context"])
        self.client = CompatibleClient(config["broker_url"], config["broker_token"], max_connections=1)
        self.client.receipts = {}
        self.output, self.run_id = Path(output), run_id
        self.allowed_actors = set()
        self.stage = "setup"
        self.calls = []
        path = self.output / "attempt-map.json"
        self.mapping = load_json(path) if path.exists() else {}

    async def open(self):
        await self.client.open()

    async def close(self):
        await self.client.close()

    def _save(self):
        write_json(self.output / "attempt-map.json", self.mapping)

    def _audit(self, value):
        from tianshu.runtime.serialization import wire

        value = wire(value)
        self.calls.append(value)
        with (self.output / "model-calls.jsonl").open("a", encoding="utf-8") as target:
            target.write(json.dumps(value, ensure_ascii=False) + "\n")

    async def _validate(self, context, refs):
        if context.actor_id not in self.allowed_actors:
            raise ValueError("evaluation_context_outside_synthetic_actor")
        async with self.database.transaction() as connection:
            if not await self.sources.validate_persistence(connection, context, refs):
                raise ValueError("evaluation_synthetic_sources_unavailable")

    @staticmethod
    def identity(request):
        from tianshu.runtime.serialization import wire

        value = wire(request)
        value["budget"].pop("deadline_at", None)
        return digest(value)

    def _restore(self, result, mapping):
        return replace(result, receipt=replace(result.receipt,
            attempt_id=mapping["local_attempt_id"], intent_id=mapping["local_intent_id"]))

    async def generate(self, request):
        from tianshu.runtime.errors import AppError
        from tianshu.runtime.serialization import wire

        if request.function not in {"memory.extract", "chat", "embed", "reflect"}:
            raise ValueError("evaluation_function_not_in_scope")
        if (any(part.media for message in request.messages for part in message.parts)
                or request.required_capabilities - {"text", "tools", "embed"}
                or getattr(request, "capability_probe", False)):
            raise ValueError("evaluation_only_supports_synthetic_text")
        await self._validate(request.context, request.source_refs)
        fingerprint = self.identity(request)
        previous = self.mapping.get(request.attempt_id)
        if previous:
            if previous["fingerprint"] != fingerprint:
                raise ValueError("evaluation_original_attempt_identity_conflict")
            result = await self.result(request.attempt_id, context=request.context)
            if result is not None:
                return result
            raise AppError("evaluation_observe_original_attempt", status=503,
                           details={"attempt_id": request.attempt_id})
        entry = {"fingerprint": fingerprint, "local_attempt_id": request.attempt_id,
                 "local_intent_id": request.intent_id, "actor_id": request.context.actor_id,
                 "broker_attempt_id": str(uuid5(NAMESPACE_URL, self.run_id + ":" + request.attempt_id)),
                 "broker_intent_id": "memory-evaluation:" + self.run_id + ":" + request.intent_id,
                 "source_refs": wire(request.source_refs), "state": "dispatching"}
        self.mapping[request.attempt_id] = entry
        self._save()  # An interruption from this point only permits observation.
        self._audit({"operation": "prepared", "stage": self.stage,
                     "broker_attempt_id": entry["broker_attempt_id"], "request": wire(request),
                     "provenance": "isolated_synthetic_sources_validated; broker_source_refs_empty"})
        remote = replace(request, context=self.model_context, source_refs=(),
                         attempt_id=entry["broker_attempt_id"], intent_id=entry["broker_intent_id"])
        started = perf_counter()
        try:
            result = await self.client.generate(remote)
        except BaseException as error:
            entry["state"] = "unknown"
            self._save()
            self._audit({"operation": "generate", "stage": self.stage,
                "broker_attempt_id": entry["broker_attempt_id"], "function": request.function,
                "receipt": None, "error_code": getattr(error, "code", type(error).__name__),
                "elapsed_ms": (perf_counter() - started) * 1000})
            raise
        entry["state"] = result.receipt.status
        self._save()
        self._audit({"operation": "generate", "stage": self.stage,
            "broker_attempt_id": entry["broker_attempt_id"], "function": request.function,
            "receipt": self.client.receipts.get(entry["broker_attempt_id"], wire(result.receipt)),
            "result": wire(result),
            "elapsed_ms": (perf_counter() - started) * 1000,
            "observed_at": datetime.now(UTC).isoformat()})
        return self._restore(result, entry)

    async def result(self, attempt_id, *, context):
        from tianshu.runtime.serialization import wire
        from tianshu.types import SourceRef

        entry = self.mapping.get(attempt_id)
        if not entry or entry["actor_id"] != context.actor_id:
            return None
        await self._validate(context, tuple(SourceRef(**ref) for ref in entry["source_refs"]))
        result = await self.client.result(entry["broker_attempt_id"], context=self.model_context)
        self._audit({"operation": "result", "stage": self.stage,
            "broker_attempt_id": entry["broker_attempt_id"],
            "receipt": self.client.receipts.get(entry["broker_attempt_id"], wire(result.receipt)) if result else None,
            "result": wire(result) if result else None})
        if result is not None:
            entry["state"] = result.receipt.status
            self._save()
            return self._restore(result, entry)
        return None

    async def status(self, attempt_id):
        from tianshu.runtime.serialization import wire

        entry = self.mapping.get(attempt_id)
        if not entry:
            return None
        receipt = await self.client.status(entry["broker_attempt_id"])
        self._audit({"operation": "status", "stage": self.stage,
            "broker_attempt_id": entry["broker_attempt_id"],
            "receipt": self.client.receipts.get(entry["broker_attempt_id"], wire(receipt))})
        return replace(receipt, attempt_id=attempt_id, intent_id=entry["local_intent_id"]) if receipt else None

    async def embedding_profile(self):
        return await self.client.embedding_profile()
