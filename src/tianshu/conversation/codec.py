"""Lossless decoding of this application's persisted canonical values."""
import hashlib
import json
from psycopg.types.json import Jsonb
from tianshu.runtime.serialization import wire
from tianshu.types import (ArtifactRef, Coverage, MediaRef, OriginKind, Receipt,
                           SourceRef, TrustedContext)


def document(value):
    return Jsonb(wire(value))


def signature(value):
    return hashlib.sha256(json.dumps(wire(value), ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def context_from_record(value):
    return TrustedContext(
        principal_id=value["principal_id"], actor_id=value["actor_id"],
        person_id=value["person_id"], conversation_id=value["conversation_id"],
        scope_ids=tuple(value["scope_ids"]), audience=tuple(value["audience"]),
        access_epoch=value["access_epoch"], origin_kind=OriginKind(value["origin_kind"]),
        permissions=frozenset(value.get("permissions", ())), conversation_kind=value.get("conversation_kind"))


def input_parts(values):
    from .types import InputPart
    return tuple(item if isinstance(item, InputPart) else InputPart(item["kind"], item.get("text"),
        MediaRef(**item["media"]) if item.get("media") else None, item.get("metadata", {})) for item in values)


def model_parts(values):
    from tianshu.models.types import ModelPart
    return tuple(item if isinstance(item, ModelPart) else ModelPart(item["kind"], item.get("text"),
        MediaRef(**item["media"]) if item.get("media") else None, item.get("call_id"), item.get("name"),
        item.get("arguments"), item.get("result"), item.get("native", {})) for item in values)


def source_refs(values):
    return tuple(item if isinstance(item, SourceRef) else SourceRef(
        item["source_id"], int(item["revision"]), item.get("locator", {})) for item in values)


def coverage(value):
    return None if value is None else Coverage(
        bool(value["complete"]), value.get("total"), tuple(value.get("missing", ())),
        tuple(value.get("ranges", ())), value.get("representation"))


def artifacts(values):
    return tuple(item if isinstance(item, ArtifactRef) else ArtifactRef(
        item["artifact_id"], MediaRef(**item["media"]), source_refs(item.get("source_refs", ())),
        coverage(item.get("coverage")), item.get("metadata", {})) for item in values)


def receipt(value):
    if value is None or isinstance(value, Receipt):
        return value
    return Receipt(value["receipt_id"], value["action_id"], value["attempt_id"],
                   value["execution"], value["effect"], value["delivery"],
                   artifacts(value.get("artifacts", ())), coverage(value.get("coverage")),
                   value.get("result", {}), value.get("error_code"))
