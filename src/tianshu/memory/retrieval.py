"""Authorized native lexical/vector candidates, bounded fusion, MMR and optional rerank."""

from datetime import UTC, datetime
import math
from uuid import uuid4
import aiohttp

from pgvector import Vector
from pgvector.psycopg import register_vector_async

from tianshu.models.types import ModelRequest
from tianshu.runtime.errors import AppError
from tianshu.types import ContextContribution, ServiceClass
from .claims import ELIGIBLE_SCOPES, lexical
from . import codec


AUTHORIZED = ("c.actor_id=%s AND r.state='active' AND "+ELIGIBLE_SCOPES+
    " AND sources.any_support_usable(r.support_groups,%s::text[]) "
    "AND (r.valid_from IS NULL OR r.valid_from<=%s) AND (r.valid_until IS NULL OR r.valid_until>%s)")
JOIN = " FROM memory.claims c JOIN memory.claim_revisions r ON r.claim_id=c.claim_id AND r.revision=c.current_revision "


def similarity(left,right):
    if left.get("vector") is not None and right.get("vector") is not None:
        a,b=left["vector"],right["vector"]
        denominator=math.sqrt(sum(v*v for v in a)*sum(v*v for v in b))
        return sum(a*b for a,b in zip(a,b))/denominator if denominator else 0.0
    a,b=set(left["lexical"].split()),set(right["lexical"].split())
    return len(a&b)/max(len(a|b),1)


def mmr(candidates,limit,weight=0.72):
    remaining=list(candidates); chosen=[]; maxima={row["claim_id"]:0.0 for row in remaining}
    while remaining and len(chosen)<limit:
        selected=max(remaining,key=lambda row:weight*row["score"]-(1-weight)*maxima[row["claim_id"]])
        remaining.remove(selected); chosen.append(selected)
        for row in remaining:
            key=row["claim_id"]
            maxima[key]=max(maxima[key],similarity(row,selected))
    return chosen


class MemoryRetrieval:
    def __init__(self,memory,embeddings,settings=None):
        self.memory,self.embeddings=memory,embeddings
        self.database,self.models=memory.database,memory.models
        self.settings=settings or {}

    async def search(self,context,query,budget,*,limit=12):
        budget.check()
        await self.memory.identity.check_context(context)
        if not context.actor_id or not query:
            return (),()
        limit=min(max(int(limit),1),32); missing=[]; vector=None; fingerprint=None
        if self.settings.get("vector",True) and budget.remaining()>2:
            try:
                vector,fingerprint=await self.embeddings.vector(context,query,budget,intent="memory-query:"+str(uuid4()),
                    query=True,service_class=ServiceClass.INTERACTIVE)
            except (AppError,TimeoutError,aiohttp.ClientError) as exc:
                missing.append(getattr(exc,"code","embedding_unavailable"))
        now=datetime.now(UTC)
        parameters=(context.actor_id,list(context.scope_ids),list(context.scope_ids),now,now)
        tokens=lexical(query).split()[:128]
        # Lexemes contain only alphanumeric/CJK characters; quote each tsquery term explicitly.
        tsquery=" | ".join("'"+token.replace("'","''")+"'" for token in tokens)
        async with self.database.transaction(budget,read_only=True) as conn:
            lexical_rows=[]
            if tsquery:
                lexical_rows=await (await conn.execute("SELECT r.*,c.actor_id,ts_rank_cd(r.search_vector,to_tsquery('simple',%s)) AS relevance "
                    +JOIN+" WHERE "+AUTHORIZED+" AND r.search_vector@@to_tsquery('simple',%s) "
                    "ORDER BY relevance DESC,r.observed_at DESC,c.claim_id LIMIT 96",(tsquery,*parameters,tsquery))).fetchall()
            vector_rows=[]
            if vector is not None:
                await register_vector_async(conn)
                vector_rows=await (await conn.execute("SELECT r.*,c.actor_id,r.embedding::real[] AS vector,"
                    "1-(r.embedding<=>%s) AS relevance "+JOIN+" WHERE "+AUTHORIZED+
                    " AND r.embedding_profile=%s AND r.embedding IS NOT NULL ORDER BY r.embedding<=>%s LIMIT 96",
                    (Vector(vector),*parameters,fingerprint,Vector(vector)))).fetchall()
            fused={}
            for rows in (lexical_rows,vector_rows):
                for rank,row in enumerate(rows):
                    current=fused.setdefault(row["claim_id"],{**row,"score":0.0})
                    current["score"]+=1/(60+rank+1)
                    if row.get("vector") is not None:
                        current["vector"]=row["vector"]
            maximum=max((row["score"] for row in fused.values()),default=1)
            for row in fused.values():
                row["score"]/=maximum
            ranked=mmr(sorted(fused.values(),key=lambda row:row["score"],reverse=True)[:128],max(limit,24))
            supports=await self.memory.claims.selected_groups(conn,context,ranked)
        ranked=[{**row,"selected_refs":refs} for row,refs in zip(ranked,supports) if refs is not None]
        if self.settings.get("rerank",False) and ranked and budget.remaining()>3:
            request=ModelRequest("memory-rerank:"+str(uuid4()),str(uuid4()),"rerank",ServiceClass.INTERACTIVE,context,budget,(),
                required_capabilities=frozenset({"rerank"}),max_output_tokens=0,
                native_payload={"query":query,"documents":[row["content"] for row in ranked],"top_n":limit},
                source_refs=tuple(ref for row in ranked for ref in row["selected_refs"]))
            try:
                result=await self.models.generate(request)
                indexes=(result.native_response or {}).get("results",()) if result.receipt.status=="succeeded" else ()
                ordered=[]; seen=set()
                for item in indexes:
                    index=item.get("index") if isinstance(item,dict) else None
                    if type(index) is int and 0<=index<len(ranked) and index not in seen:
                        seen.add(index); ordered.append(ranked[index])
                if ordered:
                    ranked=ordered+[row for index,row in enumerate(ranked) if index not in seen]
                else:
                    missing.append("rerank_unavailable")
            except (AppError,TimeoutError,aiohttp.ClientError):
                missing.append("rerank_unavailable")
        selected=ranked[:limit]
        checked=await self.memory.sources.check_many(context,tuple(row["selected_refs"] for row in selected))
        return tuple(ContextContribution("memory_claim",row["content"],tuple(row["selected_refs"]),
            tuple(context.scope_ids),60,False,str(row["revision"])) for row,valid in zip(selected,checked) if valid),tuple(missing)
