"""Incremental extraction and indexing from authorized originals, never a history scan."""

from datetime import UTC,datetime,timedelta
import json
from uuid import NAMESPACE_URL,uuid4,uuid5

from jsonschema import Draft202012Validator
from pgvector import Vector
from pgvector.psycopg import register_vector_async

from tianshu.models.types import ModelRequest
from tianshu.runtime.errors import AppError
from tianshu.runtime.commands import body_hash
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import ContextContribution,RequestBudget,ServiceClass,SourceRef
from . import codec


EXTRACTION = {"type":"object","required":["claims","matters"],"additionalProperties":False,"properties":{
    "claims":{"type":"array","maxItems":64,"items":{"type":"object","required":["subject_id","predicate","value","source_indexes"],
        "properties":{"subject_id":{"type":"string"},"predicate":{"type":"string"},"value":{},
            "assertion":{"enum":["self_confirmed","reported","quoted","inferred","observed"]},"polarity":{"type":"boolean"},
            "conditions":{"type":"object"},"valid_from":{"type":["string","null"]},"valid_until":{"type":["string","null"]},
            "source_indexes":{"type":"array","minItems":1,"maxItems":8,"items":{"type":"integer","minimum":0}}},"additionalProperties":False}},
    "matters":{"type":"array","maxItems":16,"items":{"type":"object","required":["matter_id","subject_id","title","state","source_indexes"],
        "properties":{"matter_id":{"type":"string"},"subject_id":{"type":"string"},"title":{"type":"string"},
            "state":{"enum":["planned","active","rescheduled","cancelled","uncertain"]},"due_at":{"type":["string","null"]},
            "summary":{"type":"string"},"conditions":{"type":"object"},
            "source_indexes":{"type":"array","minItems":1,"maxItems":8,"items":{"type":"integer","minimum":0}}},"additionalProperties":False}}}}
VALIDATOR=Draft202012Validator(EXTRACTION)


class MemoryWorkers:
    def __init__(self,memory,assembler,embeddings,steps):
        self.memory,self.assembler,self.embeddings,self.steps=memory,assembler,embeddings,steps
        self.database,self.identity,self.sources=memory.database,memory.identity,memory.sources

    async def _episode(self,episode_id,sequence):
        async with self.database.transaction() as conn:
            row=await (await conn.execute("SELECT * FROM memory.episodes WHERE episode_id=%s AND sequence=%s AND active",
                                         (episode_id,sequence))).fetchone()
            if not row:
                return None,None,()
            context=await self.identity.context_on(conn,row["principal_id"],actor_id=row["actor_id"],person_id=row["person_id"],
                conversation_id=row["conversation_id"])
            subjects=await self.identity.subjects_on(conn,context)
            await conn.execute("UPDATE memory.episodes SET extraction_state='running',extraction_started_at=now() WHERE episode_id=%s AND sequence=%s",
                               (episode_id,sequence))
        return row,context,subjects

    async def extract(self,episode_id,sequence):
        row,context,subjects=await self._episode(episode_id,sequence)
        if not row:
            return
        refs=codec.refs(row["source_refs"])
        if not refs:
            await self._state(episode_id,sequence,"no_original_material")
            return
        try:
            # Read only selected original spans, in batches. A large event is split into stable bounded steps.
            originals=await self.sources.read_many(context,tuple((ref,{"max_characters":1600}) for ref in refs[:128]))
            textual=[item for item in originals if item["content"]]
            matters=await self.memory.claims.matters(context,subject_id=context.person_id,limit=20)
            matter_ids={item.matter_id for item in matters.items}
            for offset in range(0,len(textual),4):
                chunk=textual[offset:offset+4]; selected=[]
                for item in chunk:
                    span=item["coverage"].ranges[0]
                    selected.append(SourceRef(item["source_ref"].source_id,item["source_ref"].revision,dict(span)))
                budget=RequestBudget(datetime.now(UTC)+timedelta(seconds=120))
                material={"speaker_id":context.person_id,"subjects":subjects,"actor_id":"actor:"+context.actor_id,
                    "origin_kind":row["origin_kind"],"occurred_at":row["occurred_at"],"actual_result":row["actual_result"],
                    "current_matters":matters.items,
                    "sources":[{"index":index,"content":item["content"],"coverage":item["coverage"],"origin_kind":item["origin_kind"],
                                "locator":item["locator"]} for index,item in enumerate(chunk)]}
                instruction=("从原始材料抽取当前事实与事项，按下述 JSON Schema 返回唯一 JSON 对象，不输出 markdown。"
                    "保留说话者/被谈论者、本人确认/转述/引用、否定、条件、时间与改期取消。工具、网页、角色自述和主观手记不能成为用户本人确认。"
                    "过时材料不能覆盖较新当前态；没有明确证据返回空数组；不要推断已完成。source_indexes只选本批索引。\n"+
                    json.dumps(EXTRACTION,ensure_ascii=False))
                dependencies=tuple({body_hash(ref):ref for ref in (*selected,*(ref for item in matters.items for ref in item.source_refs))}.values())
                pack=await self.assembler.assemble(context,"extract",instruction,budget,max_input_tokens=32000,reserved_output_tokens=4096,
                    contributions=(ContextContribution("original_evidence",json.dumps(wire(material),ensure_ascii=False),dependencies,
                        context.scope_ids,100,True,str(sequence)),))
                step=f"extract:{episode_id}:{sequence}:{offset}"
                request=ModelRequest(step,str(uuid5(NAMESPACE_URL,step)),"memory.extract",ServiceClass.ELASTIC,context,budget,
                    pack.messages,system=pack.system,max_output_tokens=4096,estimated_input_tokens=pack.token_usage.estimated_tokens,
                    source_refs=pack.source_refs)
                result=await self.steps.run(step,request)
                text="".join(part.text or "" for part in result.parts if part.kind=="text")
                parsed=json.loads(text)
                if not VALIDATOR.is_valid(parsed):
                    raise AppError("memory_extraction_invalid",status=502)
                async with self.database.transaction() as conn:
                    # Source locks precede the episode row lock, matching forgetting's lock order.
                    if not await self.sources.validate_persistence(conn,context,tuple(selected)):
                        raise AppError("source_unavailable",status=403)
                    fresh=await (await conn.execute("SELECT sequence,active FROM memory.episodes WHERE episode_id=%s FOR UPDATE",
                                                   (episode_id,))).fetchone()
                    if not fresh or fresh["sequence"]!=sequence or not fresh["active"]:
                        return
                    for kind in ("claims","matters"):
                        for item in parsed[kind]:
                            indexes=item.pop("source_indexes")
                            if any(index>=len(selected) for index in indexes):
                                raise AppError("memory_extraction_source_invalid",status=502)
                            evidence=tuple(selected[index] for index in dict.fromkeys(indexes))
                            value={**item,"source_refs":evidence,"observed_at":row["occurred_at"],"speaker_id":context.person_id}
                            if kind=="claims":
                                if row["origin_kind"]!="human" and value.get("assertion")=="self_confirmed":
                                    value["assertion"]="observed"
                                await self.memory.claims.put_on(conn,context,value,automatic=True)
                            else:
                                if value["matter_id"] not in matter_ids:
                                    value["matter_id"]="matter_"+body_hash({"actor":context.actor_id,"subject":value["subject_id"],"title":value["title"]})[:48]
                                await self.memory.claims.matter_on(conn,context,value,automatic=True)
            await self._state(episode_id,sequence,"partial" if any(not item["coverage"].complete for item in originals) or len(refs)>128 else "completed")
        except (AppError,TimeoutError,ValueError,TypeError) as exc:
            await self._state(episode_id,sequence,getattr(exc,"code","extraction_unavailable"))

    async def _state(self,episode_id,sequence,state):
        async with self.database.transaction() as conn:
            await conn.execute("UPDATE memory.episodes SET extraction_state=%s WHERE episode_id=%s AND sequence=%s AND active",
                               (state,episode_id,sequence))

    async def index(self,claim_id,revision):
        async with self.database.transaction(read_only=True) as conn:
            row=await (await conn.execute("SELECT r.*,c.actor_id,c.principal_id FROM memory.claims c JOIN memory.claim_revisions r "
                "USING(claim_id) WHERE c.claim_id=%s AND r.revision=%s AND c.current_revision=r.revision AND r.state='active'",
                (claim_id,revision))).fetchone()
            if not row:
                return
            context=await self.identity.context_on(conn,row["principal_id"],actor_id=row["actor_id"])
            selected=(await self.memory.claims.selected_groups(conn,context,(row,)))[0]
        if not selected:
            return
        budget=RequestBudget(datetime.now(UTC)+timedelta(seconds=120))
        try:
            vector,fingerprint=await self.embeddings.vector(context,row["content"],budget,intent=f"memory-index:{claim_id}:{revision}",source_refs=selected)
        except AppError:
            # Lexical retrieval remains available; configuration or unknown paid attempts are not blindly retried.
            return
        async with self.database.transaction() as conn:
            if not await self.sources.validate_persistence(conn,context,selected):
                return
            await register_vector_async(conn)
            await conn.execute("UPDATE memory.claim_revisions SET embedding=%s,embedding_profile=%s WHERE claim_id=%s AND revision=%s AND state='active'",
                               (Vector(vector),fingerprint,claim_id,revision))

    async def recover(self):
        async with self.database.transaction() as conn:
            rows=await (await conn.execute("SELECT episode_id,sequence FROM memory.episodes WHERE active AND (extraction_state='pending' "
                "OR (extraction_state='running' AND extraction_started_at<now()-interval '3 minutes')) "
                "ORDER BY sequence LIMIT 64")).fetchall()
            for row in rows:
                await self.memory.jobs.enqueue(conn,JobSpec("memory.extract","memory-recover:"+str(uuid4()),dict(row),ServiceClass.ELASTIC))
            bucket=int(datetime.now(UTC).timestamp())//30+1
            await self.memory.jobs.enqueue(conn,JobSpec("memory.recover","memory-recovery:"+str(bucket),{},ServiceClass.DUE,
                scheduled_at=datetime.fromtimestamp(bucket*30,UTC)))
