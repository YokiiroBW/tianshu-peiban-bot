"""Bounded shared material, true authorship and sparse old-record unions."""

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC,datetime,timedelta
import json
import re

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
from .types import RecordRef


@dataclass(frozen=True, slots=True)
class ConsolidationPolicy:
    quiet_seconds: int = 120
    max_wait_seconds: int = 600
    max_input_tokens: int = 12000
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
                and 4000 <= result.max_input_tokens <= 128000
                and 512 <= result.max_output_tokens <= 32000 and 0 <= result.reasoning_reserve_tokens <= 64000
                and 1 <= result.max_episodes <= 64 and 1 <= result.max_subjects <= 64 and 1 <= result.max_materials <= 128
                and 1 <= result.max_old_records <= 128 and 0 <= result.context_messages <= 32
                and 256 <= result.max_source_characters <= 100000):
            raise AppError("invalid_memory_consolidation_settings")
        return result


def partition_identity(context, scope_ids):
    """A conversation/authority domain, never a group speaker or global sequence."""
    bound = context_record(context)
    if context.conversation_kind == "group":
        bound["person_id"] = None
    scope_key = body_hash(sorted(scope_ids))
    identity = {key: bound[key] for key in ("actor_id", "principal_id", "conversation_id", "access_epoch", "scope_ids", "permissions")}
    bound["write_scope_ids"] = sorted(scope_ids)
    return "memory_partition_" + body_hash({**identity, "scope_key": scope_key})[:48], scope_key, bound


def reference_key(ref):
    return ref.source_id, ref.revision, json.dumps(ref.locator or {}, sort_keys=True)


class PreparationReads:
    """Scratch reads for one preparation, bounded by its original authorized requests."""

    def __init__(self,sources):
        self.sources=sources
        self.windows={}
        self.source_sets={}

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
            cached=await self.sources.read(context,source,requested)
            self.windows[key]=deepcopy(cached)
        result=deepcopy(cached)
        if result["content"] is not None and len(result["content"])>maximum:
            span=result["coverage"].ranges[0]
            end=span["start"]+maximum
            result.update(content=result["content"][:maximum],next_position=end,
                coverage=replace(result["coverage"],complete=False,ranges=({"start":span["start"],"end":end},)))
        return result


def append_context_material(materials,read,*,extra=None):
    """Source ranges, not body equality, decide which context has already been sent."""
    if not read["content"] or not read["coverage"].ranges:
        return 0
    span=read["coverage"].ranges[0]
    source=read["source_ref"]
    ranges=[(span["start"],span["end"])]
    for material in materials.values():
        for existing in codec.refs(material["source_refs"]):
            if (existing.source_id,existing.revision)!=(source.source_id,source.revision):
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
    for start,end in ranges:
        selected=SourceRef(source.source_id,source.revision,{**(source.locator or {}),"start":start,"end":end})
        key="m"+str(max((int(item[1:]) for item in materials),default=0)+1)
        materials[key]={"role":"context","speaker_id":read["speaker_id"],"origin_kind":read["origin_kind"],
            "occurred_at":wire(read["occurred_at"]),"reply_to":[],
            "text":read["content"][start-span["start"]:end-span["start"]],
            "source_refs":wire((selected,)),"original_ref":wire(source),"locator":read["locator"],"actual_result":None,
            **(extra or {})}
    return len(ranges)


def _reply_event_ids(locator):
    values = []
    for data in (locator or {}, (locator or {}).get("metadata") or {}):
        for key in ("reply_to", "reply_to_event_id", "quoted_event_id", "message_id"):
            value = data.get(key)
            if isinstance(value,(str,int)):
                values.append(str(value))
            elif isinstance(value,dict):
                candidate = value.get("event_id") or value.get("id")
                if candidate is not None:
                    values.append(str(candidate))
    if (locator or {}).get("part_kind") == "reply":
        metadata=(locator or {}).get("metadata") or {}
        value=metadata.get("original_message_id") or metadata.get("event_id")
        event=(locator or {}).get("event_id","")
        if value is not None:
            values.append(str(value))
            if event.startswith("message:") and event.count(":")>=2:
                values.append(event.rsplit(":",1)[0]+":"+str(value))
    return values


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


def model_material(value, policy, *, context_round=0, requests=()):
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
            if name not in {"id","source_refs", "scope_ids", "derivation_refs", "support_groups"}},
            "relations":[{name:data for name,data in link.items() if name!="source_refs"} for link in item.get("relations",())]}
            for key, item in value["records"].items()],
        "context_requests": list(requests),
        "coverage": value.get("selection_coverage", {}),
        "budget": {"max_output_tokens": policy.max_output_tokens,
                   "reasoning_reserve_tokens": policy.reasoning_reserve_tokens,
                   "max_context_rounds": max(0, 1-context_round)}}


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

    async def pending(self, context, partition_id, *, limit=None):
        async with self.database.transaction(read_only=True) as conn:
            return await (await conn.execute(
                "SELECT * FROM memory.episodes WHERE actor_id=%s AND principal_id=%s "
                "AND consolidation_partition_id=%s AND active "
                "AND extraction_state='pending' ORDER BY sequence LIMIT %s",
                (context.actor_id, context.principal_id, partition_id,
                 limit or self.policy.max_episodes))).fetchall()

    async def _sources(self, context, row):
        existing = codec.refs(row["extraction_sources"])
        if existing:
            return existing, set(row["extraction_missing"])
        refs, missing = codec.refs(row["source_refs"]), set(row["extraction_missing"])
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

    async def build(self, context, rows, batch_id, *, text_ceiling=None, reads=None):
        """Take a complete episode/source prefix; a huge source alone uses an exact range cursor."""
        text_ceiling = text_ceiling or min(self.policy.max_source_characters, self.policy.max_input_tokens // 2)
        reads=reads or PreparationReads(self.sources)
        reserved=min(self.policy.context_messages,max(0,self.policy.max_materials-1))
        material_ceiling=max(1,min(self.policy.max_materials-reserved,max(1,text_ceiling//256)))
        materials, episodes, seen = {}, [], {}
        used = 0
        authors = set()
        for row in rows:
            refs, missing = await reads.source_set(self,context,row)
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
                original = await reads.read(context, ref, requested)
                speaker = original["speaker_id"]
                if not speaker and original["origin_kind"] == "actor":
                    speaker = "actor:" + context.actor_id
                coverage_subject = row["person_id"] if row["origin_kind"] == "human" and row["person_id"] else speaker or "actor:"+context.actor_id
                if coverage_subject not in authors and len(authors) >= self.policy.max_subjects:
                    stop = True
                    break
                authors.add(coverage_subject)
                content = original["content"]
                if content:
                    span = original["coverage"].ranges[0]
                    selected = SourceRef(ref.source_id, ref.revision, {**(ref.locator or {}), **span})
                    key = reference_key(selected)
                    local_id = seen.get(key)
                    if local_id is None:
                        local_id = "m" + str(len(materials)+len(staged)+1)
                        seen[key] = local_id
                        staged.append((local_id, {"role": "new", "speaker_id": speaker,
                            "origin_kind": original["origin_kind"], "occurred_at": wire(original["occurred_at"] or row["occurred_at"]),
                            "reply_to": [], "text": content, "source_refs": wire((selected,)),
                            "locator": original["locator"], "episode_id": row["episode_id"],
                            "episode_revision": row["revision"], "reporting_speaker_id": row["person_id"],
                            "coverage_subject_id": coverage_subject,
                            "original_ref": wire(ref), "action_id": row["action_id"], "receipt_id": row["receipt_id"],
                            "actual_result": row["actual_result"], "scope_ids": row["scope_ids"]}))
                        used += len(content)
                elif original["media"]:
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
            seen={reference_key(ref):key for key,item in materials.items() for ref in codec.refs(item["source_refs"])}
        # Context is deliberately contiguous as well as quote-based; sparse replies alone miss pronouns.
        cutoff = 0
        if self.conversation and context.conversation_id:
            earliest = min(row["occurred_at"] for row in rows[:len(episodes)])
            neighborhood = await self.conversation(context, end=earliest,
                start=earliest-timedelta(days=1),
                limit=max(1,self.policy.context_messages))
            cutoff = neighborhood["cutoff"]
            if not self.policy.context_messages:
                neighborhood={**neighborhood,"items":()}
            explicit_refs=sorted({event for item in materials.values() for event in _reply_event_ids(item.get("locator"))})[:32]
            if explicit_refs:
                quoted=await self.conversation(context,cutoff=cutoff,event_ids=explicit_refs,limit=min(32,len(explicit_refs)))
                existing={item["message_id"] for item in neighborhood["items"]}
                neighborhood={**neighborhood,"items":tuple(neighborhood["items"])+tuple(
                    item for item in quoted["items"] if item["message_id"] not in existing)}
            for message in neighborhood["items"]:
                for ref in codec.refs(message["source_refs"]):
                    if len(materials) >= self.policy.max_materials:
                        break
                    original = await reads.read(context, ref, {"max_characters": min(1200,max(64,text_ceiling//4))})
                    if not original["content"]:
                        continue
                    append_context_material(materials,original,extra={"message_id":message["message_id"],
                        "speaker_id":original["speaker_id"] or ("actor:"+context.actor_id if original["origin_kind"]=="actor" else None)})
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
        async with self.database.transaction(read_only=True) as conn:
            subjects = await self.identity.resolve_memory_subjects_on(conn, context, linked_ids, aliases=names)
            subjects = {item["subject_id"]: {"subject_id": item["subject_id"], "name": item["display_name"],
                        "aliases": list(item["aliases"]), "kind": item["kind"], "revision": item["revision"]} for item in subjects}
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
        records = expand_record_endpoints(await self.old_records(context, subjects, matter_ids, materials))
        referenced_subjects = {item.get("subject_id") for item in records.values()} - set(subjects) - {None}
        if referenced_subjects:
            async with self.database.transaction(read_only=True) as conn:
                related = await self.identity.resolve_memory_subjects_on(conn,context,referenced_subjects,include_group=False)
            for item in related:
                subject_id=item["subject_id"]
                subjects[subject_id]={"subject_id":subject_id,"name":item["display_name"],"kind":item["kind"],
                    "aliases":list(item["aliases"]),"revision":item["revision"]}
                targets[subject_id]={"new_material_ids":[]}
        # Real quote/reply event IDs resolve only to aliases of actual supplied material.
        events = {}
        for key, item in materials.items():
            locator = item.get("locator") or {}
            for field in ("event_id", "message_id"):
                if locator.get(field):
                    events[str(locator[field])] = key
        for item in materials.values():
            locator = item.get("locator") or {}
            item["reply_to"] = list(dict.fromkeys(events[value] for value in _reply_event_ids(locator) if value in events))
        return {"batch_id": batch_id, "schema_version": SCHEMA_VERSION,
            "context": {"actor_id": context.actor_id, "conversation_id": context.conversation_id,
                        "time_zone": self.policy.time_zone, "cutoff_at": wire(max(row["occurred_at"] for row in rows[:len(episodes)])),
                        "message_cutoff": cutoff},
            "subjects": subjects, "materials": materials, "targets": targets, "records": records,
            "read_set": [{"kind": item["kind"], "id": item["id"], "revision": item["revision"],
                          "head_revision":item.get("head_revision") or item["revision"]} for item in records.values()],
            "episodes": episodes}

    async def old_records(self, context, subjects, matter_ids, materials):
        """Per-subject candidates plus explicit matters plus lexical candidates, deduped by stable ID."""
        candidates = {}
        query = " ".join(item["text"] for item in materials.values() if item["role"] == "new")
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
                "ORDER BY cr.observed_at DESC,cr.claim_id LIMIT 4) r JOIN memory.claims c USING(claim_id)",
                (people, context.actor_id, list(context.scope_ids)))).fetchall()
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
                "ORDER BY item.observed_at DESC,item.matter_id LIMIT 4) m "
                "UNION SELECT m.* FROM memory.matters m WHERE m.actor_id=%s AND m.active "
                "AND m.matter_id=ANY(%s::text[]) AND sources.any_support_usable(m.support_groups,%s::text[])",
                (people,context.actor_id,list(context.scope_ids),context.actor_id,sorted(matter_ids),list(context.scope_ids)))).fetchall()
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
        if self.record_candidates and query:
            try:
                values=await self.record_candidates(context,query,
                    RequestBudget(datetime.now(UTC)+timedelta(seconds=2)),subject_ids=tuple(subjects),
                    matter_refs=tuple(matter_ids),kinds=("claim","hypothesis","matter"),limit=self.policy.max_old_records)
                for value in values:
                    item=wire(value)
                    if item["kind"] in {"claim","hypothesis","matter"}:
                        candidates.setdefault((item["kind"],item["id"]),item)
            except (AppError,TimeoutError):
                # Sparse exact/lexical candidates above remain available inside this batch's budget.
                pass
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
    def dependencies(frozen):
        values=(*frozen["materials"].values(),*frozen["records"].values(),*frozen.get("episodes",()))
        return unique_refs(*(unique_refs(codec.refs(item.get("source_refs")),codec.refs(item.get("derivation_refs")),
            *(codec.refs(link.get("source_refs")) for link in item.get("relations",()))) for item in values))
