"""Bounded shared material, true authorship and sparse old-record unions."""

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC,datetime,timedelta
import json
import re
from time import perf_counter

from aiohttp import ClientError
from psycopg.types.json import Jsonb
from tianshu.models.budgets import BACKGROUND_INPUT_TOKENS
from tianshu.models.estimation import media_tokens
from tianshu.models.types import ModelMessage, ModelPart
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.sources.dependencies import unique_refs
from tianshu.types import RequestBudget,SourceRef
from . import codec
from .authority import context_record
from .domain_utils import lexical
from .extraction_contract import SCHEMA_VERSION
from .evidence import build_evidence_unit
from .material_media import (
    evidence_kind, image_aliases, image_key, image_media, material_relations, read_view,
    reply_event_ids as _reply_event_ids, source_speaker, visible_media,
)
from .preparation import PreparationMetrics, candidate_coverage_view
from .types import RecordRef


@dataclass(frozen=True, slots=True)
class ConsolidationPolicy:
    quiet_seconds: int = 120
    max_wait_seconds: int = 600
    max_input_tokens: int = BACKGROUND_INPUT_TOKENS
    max_output_tokens: int = 4096
    reasoning_reserve_tokens: int = 8192
    max_episodes: int = 32
    max_subjects: int = 16
    max_materials: int = 32
    max_old_records: int = 32
    context_messages: int = 6
    max_source_characters: int = 12000
    time_zone: str = "Asia/Shanghai"

    @classmethod
    def from_settings(cls, settings=None):
        values = settings or {}
        selected = {name: values[name] for name in cls.__dataclass_fields__ if name in values}
        result = cls(**selected)
        if not (0 <= result.quiet_seconds <= result.max_wait_seconds <= 86400
                and 4000 <= result.max_input_tokens <= BACKGROUND_INPUT_TOKENS
                and 512 <= result.max_output_tokens <= 32000 and 0 <= result.reasoning_reserve_tokens <= 64000
                and 1 <= result.max_episodes <= 64 and 1 <= result.max_subjects <= 64 and 1 <= result.max_materials <= 128
                and 1 <= result.max_old_records <= 128 and 0 <= result.context_messages <= 32
                and 256 <= result.max_source_characters <= 100000):
            raise AppError("invalid_memory_consolidation_settings")
        return result


def partition_identity(context, scope_ids, scene_id):
    """A stable Sources scene; authority is frozen separately for each batch."""
    bound = context_record(context)
    if context.conversation_kind == "group":
        bound["person_id"] = None
    scope_key = body_hash(sorted(scope_ids))
    bound["write_scope_ids"] = sorted(scope_ids)
    bound["scene_id"] = scene_id
    return "memory_partition_" + body_hash({"scene_id": scene_id})[:48], scope_key, bound


def reference_key(ref):
    return ref.source_id, ref.revision, json.dumps(ref.locator or {}, sort_keys=True)


def batch_frozen(batch):
    """The last recorded step, including any authorized original-context supplement."""
    manifest = batch["manifest"]
    steps = batch.get("step_ids", ())
    return (manifest.get("steps", {}).get(steps[-1]) if steps else None) or manifest.get("frozen", {})


def freeze_dependency_closure(frozen):
    """Certify the whole selected input as one component when independence is unknown.

    The component is closed by construction: no input in this batch is outside
    it. Missing reply edges or different speakers do not prove independence.
    If a future program-owned partition supplies stronger proof, the split
    validator still checks all declared source/reply/matter/record edges.
    """
    episodes = [item["episode_id"] for item in frozen.get("episodes", ())]
    frozen["dependency_closure"] = {"version":"memory.dependency-closure.v1", "complete":True,
        "components":[episodes] if episodes else [], "basis":"unknown_semantics_joined"}
    return frozen["dependency_closure"]


def closed_episode_split(frozen):
    """Find a boundary that does not cut any known input dependency.

    Do not split a source range or an Episode; explicit replies, source reuse,
    matters and a shared subject's old records keep their episodes together.
    An unknown dependency is a reason to retain the original failed batch.
    """
    episodes = frozen.get("episodes", ())
    if len(episodes) < 2 or any(not item.get("complete") for item in episodes):
        return None
    positions = {item["episode_id"]: index for index, item in enumerate(episodes)}
    # Absence of a reply edge cannot prove the absence of an implicit semantic
    # dependency. Only a producer-certified complete component declaration is
    # eligible for reorganization; model output is never such a declaration.
    proof = frozen.get("dependency_closure") or {}
    components = proof.get("components")
    if (proof.get("complete") is not True or not isinstance(components, list)
            or len(components) < 2 or proof.get("basis") != "program_isolated"
            or any(not isinstance(component, list) or not component for component in components)):
        return None
    declared = [episode for component in components if isinstance(component, list) for episode in component]
    if (any(not isinstance(episode,str) for episode in declared) or len(positions) != len(episodes)
            or len(declared) != len(positions) or set(declared) != set(positions)):
        return None
    materials = frozen.get("materials", {})
    records = frozen.get("records", {})
    material_episodes = proof.get("material_episodes")
    record_episodes = proof.get("record_episodes")
    if (not isinstance(material_episodes,dict) or set(material_episodes) != set(materials)
            or not isinstance(record_episodes,dict) or set(record_episodes) != set(records)):
        return None
    for coverage in (*material_episodes.values(),*record_episodes.values()):
        if (not isinstance(coverage,list) or not coverage or any(not isinstance(episode,str) for episode in coverage)
                or len(set(coverage)) != len(coverage)
                or any(episode not in positions for episode in coverage)):
            return None
    edges, source_owners, matter_owners, subjects = [], {}, {}, {}
    for component in components:
        owners = [positions[episode] for episode in component]
        edges.extend((owners[0], other) for other in owners[1:])
    for alias, item in materials.items():
        owners = [positions[value] for value in material_episodes[alias]]
        owner = positions.get(item.get("episode_id")) if item.get("role") == "new" else owners[0]
        if owner is None or item.get("role") == "new" and owner not in owners:
            return None
        edges.extend((owner, other) for other in owners if other != owner)
        for ref in item.get("reply_to", ()):
            if ref not in material_episodes:
                return None
            edges.extend((owner,positions[episode]) for episode in material_episodes[ref])
        for ref in item.get("source_refs", ()):
            key = (ref["source_id"], ref["revision"])
            if key in source_owners:
                edges.append((owner, source_owners[key]))
            source_owners[key] = owner
        matter = item.get("matter_id") or (item.get("locator") or {}).get("matter_id")
        if matter:
            if matter in matter_owners:
                edges.append((owner, matter_owners[matter]))
            matter_owners[matter] = owner
        if item.get("role") == "new":
            subjects.setdefault(item.get("coverage_subject_id"), set()).add(owner)
        if _reply_event_ids(item.get("locator")) and not item.get("reply_to"):
            return None
    # An old group-wide record can relate any of the new inputs. A repeated
    # subject's old record also couples all material for that subject.
    for alias, record in records.items():
        owners = [positions[value] for value in record_episodes[alias]]
        edges.extend((owners[0], other) for other in owners[1:])
        subject = record.get("subject_id")
        if subject is None or str(subject).startswith("group:"):
            return None
        related = sorted(subjects.get(subject, ()))
        edges.extend((owners[0], other) for other in related)
        if record.get("kind") == "matter" and record.get("id") in matter_owners:
            edges.append((owners[0],matter_owners[record["id"]]))
        for ref in record.get("source_refs", ()):
            key = (ref["source_id"],ref["revision"])
            if key in source_owners:
                edges.append((owners[0],source_owners[key]))
            source_owners[key] = owners[0]
        for relation in record.get("relations", ()):
            for side in ("from_ref","to_ref"):
                endpoint = relation.get(side)
                if endpoint not in record_episodes:
                    return None
                edges.extend((owners[0],positions[episode]) for episode in record_episodes[endpoint])
            for ref in relation.get("source_refs", ()):
                key = (ref["source_id"],ref["revision"])
                if key in source_owners:
                    edges.append((owners[0],source_owners[key]))
                source_owners[key] = owners[0]
    boundaries = [index for index in range(1, len(episodes))
                  if all((left < index) == (right < index) for left, right in edges)]
    if not boundaries:
        return None
    boundary = min(boundaries, key=lambda index: (abs(index * 2 - len(episodes)), index))
    return [list(episodes[:boundary]), list(episodes[boundary:])]


class PreparationReads:
    """Scratch reads for one preparation, bounded by its original authorized requests."""

    def __init__(self,sources):
        self.sources=sources
        self.windows={}
        self.source_sets={}
        self.neighborhoods={}
        self.subject_sets={}
        self.record_sets={}
        self.candidate_sets={}
        self.candidate_missing=()
        self.metrics=PreparationMetrics()

    async def source_set(self,builder,context,row):
        key=(body_hash(context_record(context)),row["episode_id"],row["revision"],row["sequence"])
        if key not in self.source_sets:
            refs,missing=await builder._sources(context,row)
            self.source_sets[key]=(refs,frozenset(missing))
        refs,missing=self.source_sets[key]
        return refs,set(missing)

    async def read(self,context,source,requested):
        position=requested.get("position",(source.locator or {}).get("start",0))
        maximum=min(max(int(requested.get("max_characters",12000)),1),100000)
        key=(body_hash(context_record(context)),reference_key(source),position)
        cached=self.windows.get(key)
        if cached is None or (cached["content"] is not None and cached["next_position"] is not None
                and len(cached["content"])<maximum):
            self.metrics.count("source_reads")
            with self.metrics.phase("source_ms"):
                cached=await self.sources.read(context,source,requested)
            self.windows[key]=deepcopy(cached)
        result=deepcopy(cached)
        if result["content"] is not None and len(result["content"])>maximum:
            span=result["coverage"].ranges[0]
            end=span["start"]+maximum
            result.update(content=result["content"][:maximum],next_position=end,
                coverage=replace(result["coverage"],complete=False,ranges=({"start":span["start"],"end":end},)))
        return result

    async def conversation(self,port,context,**selection):
        key=body_hash({"authority":context_record(context),"selection":wire(selection)})
        if key not in self.neighborhoods:
            self.metrics.count("conversation_reads")
            with self.metrics.phase("conversation_ms"):
                self.neighborhoods[key]=deepcopy(await port(context,**selection))
        return deepcopy(self.neighborhoods[key])

    async def subjects(self,builder,context,subject_ids,*,aliases=(),include_group=True):
        selected=tuple(sorted(subject_ids))
        names=tuple(sorted(aliases))
        key=body_hash({"authority":context_record(context),"subjects":selected,"aliases":names,"group":include_group})
        if key not in self.subject_sets:
            self.metrics.count("identity_reads")
            with self.metrics.phase("identity_ms"):
                async with builder.database.transaction(read_only=True) as conn:
                    self.subject_sets[key]=deepcopy(await builder.identity.resolve_memory_subjects_on(
                        conn,context,selected,aliases=names,include_group=include_group))
        return deepcopy(self.subject_sets[key])

    async def candidates(self,port,context,query,budget,**selection):
        key=body_hash({"authority":context_record(context),"query":query,"selection":wire(selection)})
        if key not in self.candidate_sets:
            self.metrics.count("remote_calls")
            with self.metrics.phase("remote_ms"):
                try:
                    page=await port(context,query,budget,**selection)
                    missing=tuple(page.coverage.missing)
                    if set(missing)-{"ranked_selection"}:
                        self.metrics.count("remote_degradations")
                    self.candidate_sets[key]=(tuple(page.items),missing)
                except (AppError,TimeoutError,OSError,ClientError) as exc:
                    self.metrics.count("remote_failures")
                    code=getattr(exc,"code",None)
                    self.candidate_sets[key]=((),(code if isinstance(code,str) and code else "remote_candidates_unavailable",))
        values,self.candidate_missing=self.candidate_sets[key]
        return deepcopy(values)


def append_context_material(materials,read,*,extra=None):
    """Append uncovered source ranges and original images without renumbering aliases."""
    view=read_view(read)
    source=read["source_ref"]
    span=read["coverage"].ranges[0] if read["coverage"].ranges else None
    ranges=[(span["start"],span["end"])] if view["text"] and span else []
    image_present=bool(view["media"] and image_key(view) not in
        {image_key(item) for item in materials.values() if image_key(item)})
    exact_present=False
    for material in materials.values():
        for existing in codec.refs(material["source_refs"]):
            if (existing.source_id,existing.revision)!=(source.source_id,source.revision):
                continue
            exact_present=exact_present or reference_key(existing)==reference_key(source)
            if not span:
                continue
            if material.get("media") and not material.get("text") and "end" not in (existing.locator or {}):
                # An image-only reference does not claim its source's text ranges.
                continue
            lower=(existing.locator or {}).get("start",0)
            upper=(existing.locator or {}).get("end",span["end"])
            remaining=[]
            for start,end in ranges:
                if upper<=start or end<=lower:
                    remaining.append((start,end))
                else:
                    if start<lower:
                        remaining.append((start,lower))
                    if upper<end:
                        remaining.append((upper,end))
            ranges=remaining
    additions=list(ranges)
    if not additions and (image_present or (not exact_present and
            (view["coverage_missing"] or (read.get("locator") or {}).get("part_kind") in {"reply","mention","mention_all","reference"}))):
        additions=[None]
    for index,part_range in enumerate(additions):
        if part_range is None:
            selected,text=source,""
        else:
            start,end=part_range
            selected=SourceRef(source.source_id,source.revision,{**(source.locator or {}),"start":start,"end":end})
            text=view["text"][start-span["start"]:end-span["start"]]
        media=view["media"] if image_present and index==0 else None
        key="m"+str(max((int(item[1:]) for item in materials),default=0)+1)
        materials[key]={"role":"context","speaker_id":source_speaker(read),"origin_kind":read["origin_kind"],
            "occurred_at":wire(read["occurred_at"]),"reply_to":[],
            "text":text,"media":media,
            "evidence_kind":evidence_kind(text,media),
            "coverage_missing":view["coverage_missing"],
            "source_person_id":read.get("source_person_id"),"scope_ids":read.get("scope_ids",[]),
            "source_occurred_at":wire(read["occurred_at"]),
            "source_refs":wire((selected,)),"original_ref":wire(source),"locator":read["locator"],"actual_result":None,
            **(extra or {})}
    return len(additions)


def prefix_boundary(materials, episodes):
    """Among nearby legal boundaries, keep fewer known reply edges split; ties favor length."""
    if len(episodes)<2:
        return len(episodes)
    owner = {item["episode_id"]:index for index,item in enumerate(episodes)}
    event_owner = {str((item.get("locator") or {}).get("event_id")):owner[item["episode_id"]]
        for item in materials.values() if item.get("episode_id") in owner and (item.get("locator") or {}).get("event_id")}
    edges = []
    for item in materials.values():
        if item.get("episode_id") not in owner:
            continue
        for event in _reply_event_ids(item.get("locator")):
            if event in event_owner:
                edges.append((owner[item["episode_id"]],event_owner[event]))
    candidates = [len(episodes)]
    candidates.extend(index+1 for index,item in enumerate(episodes[:-1])
        if item["complete"] and index+1 >= max(1,len(episodes)-3))
    return min(candidates,key=lambda boundary:(sum((left<boundary)!=(right<boundary) for left,right in edges),-boundary))


def frozen_mapping(value):
    """No second copy of full model input: keep only provenance and identity in the manifest."""
    result = deepcopy(wire(value))
    for material in result["materials"].values():
        material.pop("text", None)
    for record in result["records"].values():
        for key in ("content", "value", "title", "summary", "conditions", "predicate"):
            record.pop(key, None)
    return result


def refresh_read_set(frozen):
    frozen["read_set"]=[{"kind":item["kind"],"id":item["id"],"revision":item["revision"],
        "head_revision":item.get("head_revision") or item["revision"]} for item in frozen["records"].values()]


def expand_record_endpoints(records):
    """An evidence unit's relation endpoints are real reads, even without an operation."""
    result=deepcopy(records)
    identities={(item["kind"],item["id"],item["revision"]):key for key,item in result.items()}
    for record in list(result.values()):
        relations=[]
        for link in record.get("relations",()):
            resolved={}
            for side in ("from","to"):
                endpoint=link.get(side+"_ref") or {}
                if isinstance(endpoint,str) and endpoint in result:
                    resolved[side+"_ref"]=endpoint
                    continue
                identity=(endpoint.get("kind"),endpoint.get("id"),endpoint.get("revision"))
                if all(value is not None for value in identity):
                    if identity not in identities:
                        evidence=link.get(side+"_evidence") or {}
                        key="r"+str(len(result))
                        result[key]={**evidence,"kind":identity[0],"id":identity[1],"revision":identity[2]}
                        if not evidence:
                            result[key]["content_omitted"]=True
                            result[key]["source_refs"]=link.get("source_refs",[])
                        identities[identity]=key
                    resolved[side+"_ref"]=identities[identity]
            if len(resolved)==2:
                relations.append({**resolved,**{key:link.get(key) for key in
                    ("relation_kind","valid_from","valid_until","recorded_at","state","current_status","source_refs")}})
        record["relations"]=relations
    return result


def _legacy_model_material(value, policy, *, context_round=0, requests=()):
    return {"schema_version": SCHEMA_VERSION, "batch_id": value["batch_id"],
        "context": {**value["context"], "context_round": context_round},
        "subjects": list(value["subjects"].values()),
        "materials": [{"id": key, **{name: item[name] for name in
            ("role", "speaker_id", "origin_kind", "occurred_at", "reply_to", "text")},
            "reporting_speaker_id": item.get("reporting_speaker_id"),
            "actual_result": {name:data for name,data in (item.get("actual_result") or {}).items()
                if name in {"execution","effect","delivery","fictional_world","software_action","representation","event_kind","internal"}},
            "action_id":item.get("action_id"),"receipt_id":item.get("receipt_id"),
            "quoted_author": {name:data for name,data in ((item.get("locator") or {}).get("metadata") or {}).items()
                if name in {"original_author_external_id","original_author_display_name","verified_original_author_person_id",
                            "original_message_id","forward_node_index","reply_to_event_id","channel"}}}
            for key, item in value["materials"].items()],
        "targets": [{"subject_id": key, **item} for key, item in value["targets"].items()],
        "existing_records": [{"ref": key, **{name: data for name, data in item.items()
            if name not in {"id","source_refs", "scope_ids", "derivation_refs", "support_groups", "display_subjects"}},
            "relations":[{name:data for name,data in link.items() if name!="source_refs"} for link in item.get("relations",())]}
            for key, item in value["records"].items()],
        "context_requests": list(requests),
        "coverage": value.get("selection_coverage", {}),
        "budget": {"max_output_tokens": policy.max_output_tokens,
                   "reasoning_reserve_tokens": policy.reasoning_reserve_tokens,
                   "max_context_rounds": max(0, 1-context_round)}}


def model_material(value, policy, *, context_round=0, requests=()):
    payload=_legacy_model_material(value,policy,context_round=context_round,requests=requests)
    if value.get("model_protocol") != "memory.proposal.v2":
        return payload
    aliases=image_aliases(value["materials"])
    relations=material_relations(value["materials"])
    payload["model_protocol"]=value["model_protocol"]
    payload["coverage"]=candidate_coverage_view(payload["coverage"])
    for material in payload["materials"]:
        key=material["id"]
        original=value["materials"][key]
        metadata=(original.get("locator") or {}).get("metadata") or {}
        material.update(relations[key],evidence_kind=original.get("evidence_kind","text"),
            media=visible_media(original,aliases.get(key)),coverage_missing=original.get("coverage_missing",[]))
        if original.get("reported_at") is not None:
            material["reported_at"]=original["reported_at"]
        material["mention_subject_ids"]=[subject for subject in material["mention_subject_ids"] if subject in value["subjects"]]
        material["quoted_author"]={name:metadata[name] for name in
            ("original_author_display_name","verified_original_author_person_id") if metadata.get(name) is not None}
    return payload


def model_messages(value, policy, *, context_round=0, requests=()) -> tuple[ModelMessage,...]:
    """The leading JSON remains readable on recovery; images use the same local aliases."""
    payload=model_material(value,policy,context_round=context_round,requests=requests)
    parts=[ModelPart("text",text=json.dumps(wire(payload),ensure_ascii=False,separators=(",",":")))]
    if value.get("model_protocol") == "memory.proposal.v2":
        aliases=image_aliases(value["materials"])
        for alias,material in value["materials"].items():
            if aliases.get(alias) != alias:
                continue
            parts.extend((ModelPart("text",text="原图对应材料 "+alias+"；作者、时间和引用关系以该材料为准。"),
                ModelPart("image",media=image_media(material))))
    return (ModelMessage("user",tuple(parts)),)


def has_new_material(value) -> bool:
    return any(item.get("role")=="new" and
        (bool((item.get("text") or "").strip()) or image_media(item) is not None)
        for item in value.get("materials",{}).values())


def material_query(materials):
    return " ".join(item["text"] for item in materials.values() if item["role"]=="new").strip()


def material_capabilities(value) -> frozenset[str]:
    return frozenset({"text","vision"} if any(image_media(item) is not None
        for item in value.get("materials",{}).values()) else {"text"})


class MaterialBuilder:
    def __init__(self, memory, policy):
        self.memory, self.policy = memory, policy
        self.database, self.sources, self.identity = memory.database, memory.sources, memory.identity
        self.conversation = None
        self.record_candidates = None

    def bind_conversation(self, port):
        if self.conversation is not None:
            raise RuntimeError("memory_conversation_already_bound")
        self.conversation = port

    async def pending(self, context, partition_id, *, authority=None, limit=None):
        async with self.database.transaction(read_only=True) as conn:
            return await (await conn.execute(
                "SELECT * FROM memory.episodes WHERE actor_id=%s AND principal_id=%s "
                "AND consolidation_partition_id=%s AND active "
                "AND extraction_state='pending' AND scope_ids<@%s::text[] "
                "AND (%s::jsonb IS NULL OR consolidation_authority=%s::jsonb) ORDER BY sequence LIMIT %s",
                (context.actor_id, context.principal_id, partition_id,
                 list(context.scope_ids),Jsonb(authority) if authority is not None else None,
                 Jsonb(authority) if authority is not None else None,limit or self.policy.max_episodes))).fetchall()

    async def _sources(self, context, row):
        existing = codec.refs(row["extraction_sources"])
        if existing:
            return existing, set(row["extraction_missing"])
        refs = unique_refs(codec.refs(row["source_refs"]), codec.refs(row.get("supplement_refs")))
        missing = set(row["extraction_missing"])
        actual = row["actual_result"] or {}
        consumption = actual.get("consumption") if isinstance(actual, dict) else None
        if row["origin_kind"] == "tool_result" and consumption:
            analysis = actual.get("analysis")
            original = consumption.get("source") if isinstance(consumption, dict) else None
            source = (SourceRef(**original) if isinstance(original,dict) and isinstance(original.get("source_id"),str)
                and type(original.get("revision")) is int and set(original)<={"source_id","revision","locator"} else None)
            if (source and source in refs and actual.get("capability_id") == "video.observe"
                    and actual.get("execution") == "succeeded" and consumption.get("analysis_completed") is True
                    and isinstance(analysis, str) and analysis and consumption.get("model_attempt_id")):
                async with self.database.transaction() as conn:
                    descriptions = await self.sources.describe_for_write_on(conn, context, (source,))
                derived = await self.sources.capture(context, locator={"producer": "memory.observation",
                    "event_id": row["episode_id"], "revision": row["revision"], "support_groups": ((source,),),
                    "consumption": consumption, "evidence_unit": descriptions[0]["evidence_unit"]},
                    raw=analysis.encode(), mime_type="text/plain")
                refs = unique_refs(refs, (derived,))
                missing.update(value for value in consumption.get("omitted",()) if isinstance(value,str))
            else:
                missing.add("observation_analysis_unavailable")
        return refs, missing

    async def build(self, context, rows, batch_id, *, text_ceiling=None, max_new_materials=None,
                    reads=None, budget: RequestBudget | None = None, include_records=True):
        """Take a complete episode/source prefix; a huge source alone uses an exact range cursor."""
        text_ceiling = text_ceiling or min(self.policy.max_source_characters, self.policy.max_input_tokens // 2)
        reads=reads or PreparationReads(self.sources)
        reserved=min(self.policy.context_messages,max(0,self.policy.max_materials-1))
        material_ceiling=max(1,min(self.policy.max_materials-reserved,max(1,text_ceiling//256)))
        if max_new_materials is not None:
            material_ceiling = min(material_ceiling, max_new_materials)
        materials, episodes, seen = {}, [], {}
        used = 0
        authors = set()
        async with self.database.transaction(read_only=True) as conn:
            supplement_rows = await (await conn.execute("SELECT episode_id,event_revision,original_ref,supplement_ref "
                "FROM memory.episode_supplements WHERE episode_id=ANY(%s::text[]) AND active",
                ([row["episode_id"] for row in rows],))).fetchall()
        supplements = {(item["episode_id"],item["event_revision"],item["original_ref"]["source_id"],
                        item["original_ref"]["revision"]): item["supplement_ref"] for item in supplement_rows}
        for row in rows:
            try:
                refs, missing = await reads.source_set(self,context,row)
            except AppError as error:
                error.details = {**(error.details or {}), "episode_id": row["episode_id"],
                                 "episode_revision": row["revision"], "episode_sequence": row["sequence"]}
                raise
            before = row["extraction_cursor"] or {"source_index": 0, "position": None, "step": 0}
            index, position = before.get("source_index", 0), before.get("position")
            after = {"protocol": SCHEMA_VERSION, "source_index": index, "position": position, "step": before.get("step", 0)+1}
            staged = []
            stop = False
            for source_index, ref in enumerate(refs[index:], index):
                if len(materials)+len(staged) >= material_ceiling:
                    stop = True
                    break
                requested = {"max_characters": min(self.policy.max_source_characters, max(1, text_ceiling-used))}
                if source_index == index and position is not None:
                    requested["position"] = position
                try:
                    original = await reads.read(context, ref, requested)
                except AppError as error:
                    error.details = {**(error.details or {}), "episode_id": row["episode_id"],
                                     "episode_revision": row["revision"], "episode_sequence": row["sequence"]}
                    raise
                view = read_view(original)
                attached = supplements.get((row["episode_id"],row["event_revision"],ref.source_id,ref.revision))
                if attached and "image_original_unavailable" in view["coverage_missing"]:
                    supplement_ref = codec.refs((attached,))[0]
                    if any((value.source_id,value.revision) == (supplement_ref.source_id,supplement_ref.revision)
                           for value in codec.refs(row.get("supplement_refs"))):
                        supplement_read = await reads.read(context,supplement_ref,{"max_characters":1})
                        supplement_view = read_view(supplement_read)
                        if supplement_view["media"] is not None:
                            view["coverage_missing"].remove("image_original_unavailable")
                missing.update(view["coverage_missing"])
                speaker = source_speaker(original,context.actor_id)
                coverage_subject = row["person_id"] if row["origin_kind"] == "human" and row["person_id"] else speaker or "actor:"+context.actor_id
                if coverage_subject not in authors and len(authors) >= self.policy.max_subjects:
                    stop = True
                    break
                authors.add(coverage_subject)
                content = view["text"]
                if content or view["media"] or view["coverage_missing"] or original["content"] is not None:
                    span = original["coverage"].ranges[0] if original["coverage"].ranges else {}
                    selected = SourceRef(ref.source_id, ref.revision, {**(ref.locator or {}), **span})
                    # One source may be reported again in a later episode. Keep that
                    # speaker's occurrence; model_messages deduplicates only the image.
                    key = row["episode_id"], reference_key(selected)
                    local_id = seen.get(key)
                    if local_id is None:
                        local_id = "m" + str(len(materials)+len(staged)+1)
                        seen[key] = local_id
                        staged.append((local_id, {"role": "new", "speaker_id": speaker,
                            "origin_kind": original["origin_kind"], "occurred_at": wire(row["occurred_at"]
                                if (original.get("locator") or {}).get("kind") in {"channel_original","channel_media_representation","media_supplement"}
                                else original["occurred_at"] or row["occurred_at"]),
                            "source_occurred_at":wire(original["occurred_at"]),"source_person_id":original.get("source_person_id"),
                            "reply_to": [], **view, "source_refs": wire((selected,)),
                            "locator": original["locator"], "episode_id": row["episode_id"],
                            "episode_revision": row["revision"], "reporting_speaker_id": row["person_id"],
                            "reported_at":wire(row["occurred_at"]),
                            "coverage_subject_id": coverage_subject,
                            "original_ref": wire(ref), "action_id": row["action_id"], "receipt_id": row["receipt_id"],
                            "matter_id": row.get("matter_id"),
                            "actual_result": row["actual_result"], "scope_ids": original.get("scope_ids",row["scope_ids"]),
                            "conversation_id": row.get("conversation_id",context.conversation_id)}))
                        used += len(content)
                if original["media"] and not view["media"]:
                    actual=row["actual_result"] or {}
                    consumption=actual.get("consumption") if isinstance(actual,dict) else None
                    if not consumption or consumption.get("analysis_completed") is not True:
                        missing.add("media_without_confirmed_analysis")
                if original["next_position"] is not None:
                    after.update(source_index=source_index, position=original["next_position"])
                    stop = True
                    break
                after.update(source_index=source_index+1, position=None)
                if used >= text_ceiling:
                    stop = True
                    break
            if not staged and stop and episodes:
                break
            materials.update(staged)
            complete = after["source_index"] >= len(refs)
            if not refs:
                missing.add("no_original_material")
                complete = True
            episodes.append({"episode_id": row["episode_id"], "revision": row["revision"],
                "sequence": row["sequence"], "before": wire(row["extraction_cursor"]), "after": after,
                "source_refs": wire(refs), "scope_ids": row["scope_ids"], "missing": sorted(missing), "complete": complete,
                "material_ids": [key for key, item in materials.items() if item.get("episode_id") == row["episode_id"]]})
            if stop:
                break
        if not episodes:
            return None
        boundary = prefix_boundary(materials,episodes)
        if boundary<len(episodes):
            episodes=episodes[:boundary]
            kept={item["episode_id"] for item in episodes}
            materials={key:item for key,item in materials.items() if item.get("episode_id") in kept}
            authors={item["coverage_subject_id"] for item in materials.values()}
        # Context is deliberately contiguous as well as quote-based; sparse replies alone miss pronouns.
        cutoff = 0
        selection_coverage = {}
        if self.conversation and context.conversation_id and has_new_material({"materials":materials}):
            earliest = min(row["occurred_at"] for row in rows[:len(episodes)])
            neighborhood = await reads.conversation(self.conversation,context, end=earliest,
                start=earliest-timedelta(days=1),
                limit=max(1,self.policy.context_messages))
            cutoff = neighborhood["cutoff"]
            if not self.policy.context_messages:
                neighborhood={**neighborhood,"items":()}
            explicit_refs=sorted({event for item in materials.values() for event in _reply_event_ids(item.get("locator"))})[:32]
            if explicit_refs:
                quoted=await reads.conversation(self.conversation,context,cutoff=cutoff,event_ids=explicit_refs,limit=min(32,len(explicit_refs)))
                existing={item["message_id"] for item in neighborhood["items"]}
                neighborhood={**neighborhood,"items":tuple(neighborhood["items"])+tuple(
                    item for item in quoted["items"] if item["message_id"] not in existing)}
            # Select whole historical messages and reserve images with the existing
            # media estimate. Explicit reply originals have priority; the caller's
            # full protocol counter still makes the final input-budget decision.
            media_ceiling=max(0,self.policy.max_input_tokens//2-
                sum(len(item["text"].encode("utf-8")) for item in materials.values()))
            ordered=sorted(neighborhood["items"],key=lambda item:item.get("event_id") not in explicit_refs)
            for message in ordered:
                required=message.get("event_id") in explicit_refs
                refs=codec.refs(message["source_refs"])
                omitted=None
                if len(refs)>self.policy.max_materials:
                    omitted="context_material_limit_omitted"
                else:
                    expanded=dict(materials)
                    for ref in refs:
                        original=await reads.read(context,ref,{"max_characters":min(1200,max(64,text_ceiling//4))})
                        append_context_material(expanded,original,extra={"message_id":message["message_id"],
                            "context_message_id":message["message_id"],"context_required":required,
                            "conversation_id":context.conversation_id,
                            "occurred_at":wire(message["occurred_at"]),
                            "scope_ids":original.get("scope_ids",list(context.scope_ids)),
                            "speaker_id":source_speaker(original,context.actor_id)})
                    if len(expanded)>self.policy.max_materials:
                        omitted="context_material_limit_omitted"
                    elif (not required and set(filter(None,(image_key(item) for item in expanded.values()))) -
                            set(filter(None,(image_key(item) for item in materials.values()))) and
                            len(set(image_aliases(expanded).values()))*media_tokens("image")>media_ceiling):
                        omitted="context_image_budget_omitted"
                    else:
                        materials=expanded
                if omitted:
                    selection_coverage[omitted]=selection_coverage.get(omitted,0)+1
                    if required:
                        selection_coverage["required_context_omitted"]=selection_coverage.get("required_context_omitted",0)+1
        linked_ids, names, matter_ids = set(), set(), set()
        linked_ids.update(authors)
        for material in materials.values():
            if material["speaker_id"]:
                linked_ids.add(material["speaker_id"])
            locator = material.get("locator") or {}
            metadata = locator.get("metadata") or {}
            actual=material.get("actual_result") or {}
            if isinstance(actual,dict):
                linked_ids.update(value for value in actual.get("affected_subject_ids",()) if isinstance(value,str))
            if material.get("reporting_speaker_id"):
                linked_ids.add(material["reporting_speaker_id"])
            if metadata.get("verified_original_author_person_id"):
                linked_ids.add(metadata["verified_original_author_person_id"])
            for field in ("verified_subject_ids","mentioned_person_ids","subject_ids"):
                linked_ids.update(value for value in metadata.get(field,()) if isinstance(value,str))
            for value in locator.get("subject_ids", ()):
                if isinstance(value, str):
                    linked_ids.add(value)
            for value in locator.get("mentions", ()):
                if isinstance(value, dict) and value.get("person_id"):
                    linked_ids.add(value["person_id"])
            # Exact alias lookup is bounded by mentioned tokens, never by a group-wide model directory.
            names.update(re.findall(r"@([^\s@，。！？:：]{1,32})", material.get("text", "")))
            for key in ("matter_id",):
                if locator.get(key):
                    matter_ids.add(locator[key])
        matter_ids.update(row["matter_id"] for row in rows[:len(episodes)] if row["matter_id"])
        resolved=await reads.subjects(self,context,linked_ids,aliases=names)
        subjects = {item["subject_id"]: {"subject_id": item["subject_id"], "name": item["display_name"],
                    "aliases": list(item["aliases"]), "kind": item["kind"], "revision": item["revision"]} for item in resolved}
        if any(author not in subjects for author in authors):
            raise AppError("memory_material_author_unavailable", status=403)
        targets = {subject: {"new_material_ids": [key for key, item in materials.items()
            if item["role"] == "new" and item.get("coverage_subject_id") == subject]} for subject in sorted(authors)}
        for subject in subjects:
            if subject not in targets:
                targets[subject] = {"new_material_ids": []}
        if not targets and materials:
            actor_subject = "actor:"+context.actor_id
            subjects[actor_subject] = {"subject_id": actor_subject, "name": context.actor_id, "aliases": [], "kind": "actor", "revision": 1}
            targets[actor_subject] = {"new_material_ids": [key for key,item in materials.items() if item["role"] == "new"]}
        relations=material_relations(materials)
        for key,item in materials.items():
            item.update(relations[key])
            if item["role"]=="new" and _reply_event_ids(item.get("locator")):
                missing=set()
                if not item["reply_to"]:
                    missing.add("quoted_context_unavailable")
                elif any(materials[parent].get("coverage_missing") for parent in item["reply_to"]):
                    missing.add("quoted_material_incomplete")
                for episode in episodes:
                    if episode["episode_id"]==item["episode_id"]:
                        episode["missing"]=sorted(set(episode["missing"])|missing)
        frozen={"batch_id": batch_id, "schema_version": SCHEMA_VERSION,"model_protocol":"memory.proposal.v2",
            "context": {"actor_id": context.actor_id, "conversation_id": context.conversation_id,
                        "time_zone": self.policy.time_zone, "cutoff_at": wire(max(row["occurred_at"] for row in rows[:len(episodes)])),
                        "message_cutoff": cutoff},
            "subjects": subjects, "materials": materials, "targets": targets, "records": {},
            "selection_coverage":selection_coverage,
            "candidate_matter_ids":sorted(matter_ids),"read_set":[],
            "episodes": episodes}
        if include_records:
            await self.attach_records(context,frozen,reads=reads,budget=budget)
        freeze_dependency_closure(frozen)
        return frozen

    async def attach_records(self,context,frozen,*,reads=None,budget=None):
        """Enrich an already fitted material range once, without rebuilding that range."""
        reads=reads or PreparationReads(self.sources)
        matter_ids=set(frozen.get("candidate_matter_ids",()))
        subjects=frozen["subjects"]
        added_before=set(frozen.get("candidate_subject_ids",()))
        material_subjects={key:value for key,value in subjects.items() if key not in added_before}
        key=body_hash({"authority":context_record(context),"subjects":material_subjects,"matters":sorted(matter_ids),
            "materials":[{"source_refs":item["source_refs"],"text":item.get("text",""),
                "changed_records":(item.get("actual_result") or {}).get("changed_records",())}
                for item in frozen["materials"].values() if item["role"]=="new"]})
        if key not in reads.record_sets:
            records=await self.old_records(context,material_subjects,matter_ids,frozen["materials"],reads=reads,budget=budget)
            reads.record_sets[key]=(deepcopy(records),reads.candidate_missing)
        records,candidate_missing=deepcopy(reads.record_sets[key])
        frozen["records"]=expand_record_endpoints(records)
        if candidate_missing:
            frozen.setdefault("selection_coverage",{})["record_candidates_missing"]=list(candidate_missing)
        related_ids=sorted({item.get("subject_id") for item in frozen["records"].values()}-set(material_subjects)-{None})
        added=[]
        for start in range(0,len(related_ids),128):
            related=await reads.subjects(self,context,related_ids[start:start+128],include_group=False)
            for item in related:
                subject_id=item["subject_id"]
                subjects[subject_id]={"subject_id":subject_id,"name":item["display_name"],"kind":item["kind"],
                    "aliases":list(item["aliases"]),"revision":item["revision"]}
                frozen["targets"][subject_id]={"new_material_ids":[]}
                added.append(subject_id)
        frozen["candidate_subject_ids"]=added
        refresh_read_set(frozen)
        freeze_dependency_closure(frozen)
        return frozen

    async def old_records(self, context, subjects, matter_ids, materials, *, budget=None,reads=None):
        """Per-subject candidates plus explicit matters plus lexical candidates, deduped by stable ID."""
        reads=reads or PreparationReads(self.sources)
        reads.candidate_missing=()
        local_started=perf_counter()
        candidates = {}
        query = material_query(materials)
        explicit_claim_ids=sorted({record["id"] for item in materials.values()
            for record in (item.get("actual_result") or {}).get("changed_records",())
            if record.get("kind") in {"claim","hypothesis"}})
        async with self.database.transaction(read_only=True) as conn:
            people = sorted(subjects)
            # LATERAL preserves a sparse speaker's candidate slot under an active speaker's history.
            claim_rows = await (await conn.execute(
                "SELECT r.*,c.actor_id FROM unnest(%s::text[]) s(subject_id) CROSS JOIN LATERAL ("
                "SELECT cr.*,cl.actor_id FROM memory.claims cl JOIN memory.claim_revisions cr ON cr.claim_id=cl.claim_id "
                "AND cr.revision=cl.current_revision WHERE cl.actor_id=%s AND cr.subject_id=s.subject_id "
                "AND cr.state='active' AND cr.current_status='current' AND sources.any_support_usable(cr.support_groups,%s::text[]) "
                "ORDER BY cr.observed_at DESC,cr.claim_id LIMIT %s) r JOIN memory.claims c USING(claim_id)",
                (people, context.actor_id, list(context.scope_ids),self.policy.max_old_records))).fetchall()
            lexical_rows = await (await conn.execute(
                "SELECT r.*,c.actor_id FROM memory.claims c JOIN memory.claim_revisions r ON r.claim_id=c.claim_id AND r.revision=c.current_revision "
                "WHERE c.actor_id=%s AND r.state='active' AND (r.current_status='current' OR r.claim_id=ANY(%s::text[])) "
                "AND sources.any_support_usable(r.support_groups,%s::text[]) AND (r.claim_id=ANY(%s::text[]) OR r.search_vector @@ plainto_tsquery('simple',%s)) "
                "ORDER BY (r.claim_id=ANY(%s::text[])) DESC,ts_rank(r.search_vector,plainto_tsquery('simple',%s)) DESC,r.claim_id LIMIT %s",
                (context.actor_id,explicit_claim_ids,list(context.scope_ids),explicit_claim_ids,lexical(query[:12000]),explicit_claim_ids,lexical(query[:12000]),self.policy.max_old_records))).fetchall()
            matter_rows = await (await conn.execute(
                "SELECT m.* FROM unnest(%s::text[]) s(subject_id) CROSS JOIN LATERAL ("
                "SELECT item.* FROM memory.matters item WHERE item.actor_id=%s AND item.active "
                "AND item.current_status='current' AND item.subject_id=s.subject_id "
                "AND sources.any_support_usable(item.support_groups,%s::text[]) "
                "ORDER BY item.observed_at DESC,item.matter_id LIMIT %s) m "
                "UNION SELECT m.* FROM memory.matters m WHERE m.actor_id=%s AND m.active "
                "AND m.matter_id=ANY(%s::text[]) AND sources.any_support_usable(m.support_groups,%s::text[])",
                (people,context.actor_id,list(context.scope_ids),self.policy.max_old_records,
                 context.actor_id,sorted(matter_ids),list(context.scope_ids)))).fetchall()
            selected = await self.memory.claims.selected_groups(conn, context, tuple(claim_rows+lexical_rows))
            matter_selected=await self.memory.claims.selected_groups(conn,context,tuple(matter_rows))
            endpoints=[RecordRef("hypothesis" if row.get("memory_kind")=="hypothesis" else "claim",row["claim_id"],row["revision"])
                for row,refs in zip(claim_rows+lexical_rows,selected,strict=True) if refs]
            endpoints.extend(RecordRef("matter",row["matter_id"],row["revision"])
                for row,refs in zip(matter_rows,matter_selected,strict=True) if refs)
            links=await self.memory.claims.revisions.links.for_records_on(conn,context,endpoints)
            for row, refs in zip(claim_rows+lexical_rows, selected, strict=True):
                if refs:
                    kind = "hypothesis" if row.get("memory_kind") == "hypothesis" else "claim"
                    value=codec.claim(row,refs,relations=links.get(row["claim_id"],()))
                    candidates.setdefault((kind,row["claim_id"]),wire(build_evidence_unit(value)))
            for row,refs in zip(matter_rows,matter_selected,strict=True):
                if refs:
                    value=codec.matter(row,selected_refs=refs,relations=links.get(row["matter_id"],()))
                    candidates.setdefault(("matter",row["matter_id"]),wire(build_evidence_unit(value)))
        reads.metrics.values["local_records_ms"]+=(perf_counter()-local_started)*1000
        if self.record_candidates and query:
            values=await reads.candidates(self.record_candidates,context,query,budget,subject_ids=tuple(subjects),
                matter_refs=tuple(sorted(matter_ids)),kinds=("claim","hypothesis","matter"),limit=self.policy.max_old_records)
            for value in values:
                item=wire(value)
                if item["kind"] in {"claim","hypothesis","matter"}:
                    candidates.setdefault((item["kind"],item["id"]),item)
        # Explicit event identity wins, then round-robin the sparse subject union. The
        # largest speaker's history cannot consume every slot before a quiet speaker.
        explicit = [item for item in candidates.values() if item["kind"] == "matter" and item["id"] in matter_ids]
        buckets = {}
        for item in candidates.values():
            if item not in explicit:
                buckets.setdefault(item.get("subject_id"),[]).append(item)
        selected = list(explicit)
        while buckets and len(selected)<self.policy.max_old_records:
            for subject in list(buckets):
                selected.append(buckets[subject].pop(0))
                if not buckets[subject]:
                    del buckets[subject]
                if len(selected)>=self.policy.max_old_records:
                    break
        selected=selected[:self.policy.max_old_records]
        return {"r"+str(index): item for index,item in enumerate(selected)}

    async def expand_record(self,context,record):
        """Read a requested fixed version, with the current source and scope rules."""
        if record["kind"] in {"claim","hypothesis"}:
            value=await self.memory.claims.get(context,record["id"])
            if value.revision != record["revision"]:
                raise AppError("memory_consolidation_read_changed",status=409)
            return wire(build_evidence_unit(value))
        if record["kind"] == "matter":
            value=await self.memory.claims.get_matter(context,record["id"],revision=record["revision"])
            return wire(build_evidence_unit(value))
        raise AppError("memory_context_record_kind_invalid",status=502)

    @staticmethod
    def generation_dependencies(frozen):
        # Only the model-visible selected materials and records participated in
        # this computation. An omitted candidate is not a derivation input.
        values=(*frozen["materials"].values(),*frozen["records"].values())
        return unique_refs(*(unique_refs(codec.refs(item.get("source_refs")),codec.refs(item.get("derivation_refs")),
            *(codec.refs(link.get("source_refs")) for link in item.get("relations",()))) for item in values))

    @staticmethod
    def dependencies(frozen):
        # Cursor advancement also checks its original Episode sources, even if
        # some of those originals are reserved for a later input range.
        return unique_refs(MaterialBuilder.generation_dependencies(frozen),
            *(codec.refs(item.get("source_refs")) for item in frozen.get("episodes", ())))
