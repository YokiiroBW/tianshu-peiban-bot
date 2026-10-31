"""Capability-owned cross-domain values; canonical action/media types live in tianshu.types."""
from dataclasses import dataclass,field
from datetime import datetime
from typing import Any,Mapping,Literal
from tianshu.types import MediaRef,SourceRef

@dataclass(frozen=True)
class SceneSnapshot:
    actor_id:str
    revision:str
    captured_at:datetime
    timezone:str
    local_time:str
    character:str
    activity:Mapping[str,Any]
    place:Mapping[str,Any]
    weather:Mapping[str,Any]|None
    outfit:Mapping[str,Any]|None
    references:tuple[MediaRef,...]=()
    source_refs:tuple[SourceRef,...]=()

@dataclass(frozen=True)
class ImageIntent:
    character:str
    outfit:str
    activity:str
    scene:str
    local_time:str
    light:str
    weather:str
    pose:str
    expression:str
    camera:str
    style:str
    negative_constraints:tuple[str,...]=()
    references:tuple[MediaRef,...]=()
    purpose:Literal["selfie","creation"]="selfie"
    requested_change:str|None=None

@dataclass(frozen=True)
class PhotoIntent:
    trigger:Literal["user","schedule","actor"]
    purpose:Literal["album","share"]
    motivation:str
    window:Mapping[str,Any]
    dedup_key:str
    target_ref:str|None=None
    artifact_ids:tuple[str,...]=()
    delivery_kind:Literal["create_only","share_candidate","requested_delivery"]="share_candidate"

@dataclass(frozen=True)
class ReactionIntent:
    meaning:str
    purpose:str
    attitude:str
    intensity:float
    actor_id:str
    scope_ids:tuple[str,...]
    ironic:bool=False
    recent_asset_ids:tuple[str,...]=()
    requested_pack_ids:tuple[str,...]=()

@dataclass(frozen=True)
class ConsumptionReceipt:
    source:SourceRef
    requested:Mapping[str,Any]
    modalities:tuple[str,...]
    ranges:tuple[Mapping[str,Any],...]
    frame_times:tuple[float,...]=()
    sampling:str|None=None
    omitted:tuple[str,...]=()
    next_position:Mapping[str,Any]|None=None
    analysis_completed:bool=False
    analysis_artifact_id:str|None=None
    model_attempt_id:str|None=None
