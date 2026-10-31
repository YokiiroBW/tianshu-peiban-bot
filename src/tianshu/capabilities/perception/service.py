"""Direct multimodal input and actual interval analysis with honest consumption evidence."""
import asyncio
import base64
import io
import json
from datetime import UTC,datetime,timedelta
from uuid import uuid4
from PIL import Image,ImageSequence
from tianshu.types import MediaRef,SourceRef,Coverage,RequestBudget,ServiceClass,Receipt
from tianshu.models.types import ModelRequest,ModelMessage,ModelPart
from tianshu.runtime.serialization import wire
from ..catalog import CapabilityDefinition,Availability
from ..contracts import Fault,canonical
from ..external import execution_service_class
from ..dto import ConsumptionReceipt
from .decode import prepare,_video,_audio
from ..local_work import settled

class Perception:
    definition=CapabilityDefinition("video.observe","观看片段","读取真实音画并记录实际已处理范围。",{"type":"object","properties":{"url":{"type":"string"},"cid":{"type":"integer"},"account_id":{"type":"string"},"quality":{"type":"integer"},"media":{"type":"object"},"source":{"type":"object"},"start":{"type":"number","minimum":0},"end":{"type":"number","exclusiveMinimum":0},"question":{"type":"string"}},"required":["start","end"],"oneOf":[{"required":["media","source"]},{"required":["url"]}]},{"type":"object"},"read")
    def __init__(self,broker,artifacts,authority,database):self.broker,self.artifacts,self.authority,self.database=broker,artifacts,authority,database
    async def availability(self,context):return Availability(True,True)
    async def image_parts(self,context,media,source_refs=(),*,maximum_frames=8):
        raw,mime=await self.artifacts.resolve(context,media)
        if mime!="image/gif":return (ModelPart("image",media=media),),{"sampling":"original","frame_times":[],"omitted":[]}
        frames=[];times=[]
        with Image.open(io.BytesIO(raw)) as image:
            count=getattr(image,"n_frames",1);indices=sorted({round(i*(count-1)/max(1,maximum_frames-1)) for i in range(min(count,maximum_frames))})
            at=0.0
            for index in range(count):
                image.seek(index);duration=float(image.info.get("duration",100))/1000
                if index in indices:
                    output=io.BytesIO();image.convert("RGB").save(output,format="JPEG")
                    artifact=await self.artifacts.put(context,output.getvalue(),"image/jpeg",source_refs=source_refs,metadata={"original_media_id":media.media_id,"at_seconds":at,"frame_index":index})
                    frames.extend([ModelPart("text",text=f"GIF frame {index} at {at:.3f}s"),ModelPart("image",media=artifact.media)]);times.append(at)
                at+=duration
        return tuple(frames),{"sampling":"uniform_including_first_and_last","frame_times":times,"omitted":["frames_sampled"] if count>len(indices) else []}
    async def direct_parts(self,context,media,source_refs=()):
        if media.mime_type.startswith("image/"):return await self.image_parts(context,media,source_refs)
        if media.mime_type.startswith("audio/"):
            return (ModelPart("audio",media=media),),{"sampling":"original","omitted":[]}
        if media.mime_type.startswith("video/"):
            return (ModelPart("video",media=media),),{"sampling":"original","omitted":[]}
        raise Fault("media_not_supported",415)
    async def describe(self,context,media,*,question,function="vision",source_refs=(),budget=None):
        if media.media_id.startswith("artifact:"):
            artifact=await self.artifacts.reference(context,media.media_id)
            source_refs=tuple({(x.source_id,x.revision):x for x in (*source_refs,*artifact.source_refs)}.values())
        elif media.media_id.startswith("source:"):
            identifier,revision=media.media_id[7:].rsplit(":",1)
            source_refs=tuple({(x.source_id,x.revision):x for x in (*source_refs,SourceRef(identifier,int(revision)))}.values())
        await self.authority.check(context,source_refs)
        parts,coverage=await self.direct_parts(context,media,source_refs)
        result=await self.broker.generate(ModelRequest(intent_id=str(uuid4()),attempt_id=str(uuid4()),function=function,service_class=ServiceClass.ELASTIC,context=context,budget=budget or RequestBudget(datetime.now(UTC)+timedelta(minutes=3)),messages=(ModelMessage("user",(ModelPart("text",text=question),*parts)),),required_capabilities=frozenset({part.kind for part in parts if part.kind!="text"}),max_output_tokens=1800,source_refs=source_refs))
        return {"text":"".join(part.text or "" for part in result.parts),"coverage":coverage,"model_receipt":wire(result.receipt)}
    async def execute(self,action,attempt):
        args=dict(action.arguments)
        if args.get("url"):
            if not getattr(self,"video_originals",None):raise Fault("video_acquisition_unavailable",503)
            artifact,source=await self.video_originals.acquire(action)
            args.update(media=wire(artifact.media),source=wire(source))
        media=MediaRef(**args["media"]);source=SourceRef(**args["source"])
        await self.authority.check(action.context,(source,));mime=media.mime_type
        start=float(args["start"]);end=float(args["end"])
        if not 0<=start<end:raise Fault("invalid_range",400)
        # A page is bounded to 30 seconds; its exact continuation is persisted as evidence.
        end=min(end,start+30)
        if not mime.startswith(("video/","audio/")):raise Fault("media_not_supported",415)
        async with self.artifacts.open_media(action.context,media) as reader:
            decoded=await settled(asyncio.to_thread,_video if mime.startswith("video/") else _audio,reader,media.sha256,start,end,1_500_000)
        parts=[ModelPart("text",text=(args.get("question") or "Describe the actual interval, preserving temporal order and uncertainty.")+"\n"+decoded.get("text","") )];modalities=set();frame_times=[]
        for representation in decoded["representations"]:
            data=base64.b64decode(representation["data_base64"]);kind="audio" if representation["kind"]=="audio_clip" else "image"
            artifact=await self.artifacts.put(action.context,data,representation["media_type"],source_refs=(source,),metadata={"source_media_id":media.media_id,"at_seconds":representation.get("at_seconds")})
            if representation.get("at_seconds") is not None:
                parts.append(ModelPart("text",text="Frame at "+str(representation["at_seconds"])+" seconds"));frame_times.append(representation["at_seconds"])
            parts.append(ModelPart(kind,media=artifact.media));modalities.add(kind)
        if not modalities:raise Fault("actual_media_unavailable",503)
        result=await self.broker.generate(ModelRequest(intent_id=action.action_id,attempt_id=attempt.attempt_id,function="video.observe",service_class=execution_service_class(action),context=action.context,budget=RequestBudget(action.deadline_at or datetime.now(UTC)+timedelta(minutes=10)),messages=(ModelMessage("user",tuple(parts)),),required_capabilities=frozenset(modalities),max_output_tokens=2500,source_refs=(source,)))
        text="".join(part.text or "" for part in result.parts if part.kind=="text")
        if result.receipt.status!="succeeded" or not text:raise Fault("video_analysis_incomplete",502)
        await self.authority.check(action.context,(source,))
        analysis=await self.artifacts.put(action.context,text.encode(),"text/plain",source_refs=(source,),metadata={"kind":"video_analysis","model_attempt_id":result.receipt.attempt_id})
        interval=decoded["coverage"];continuation={"unit":"seconds","start":interval["end"],"end":args["end"]} if interval["end"]<float(args["end"]) else None
        receipt=ConsumptionReceipt(source,{"start":start,"end":args["end"]},tuple(sorted(modalities)),(interval,),tuple(frame_times),"sampled_frames_and_actual_audio",tuple(decoded["gaps"]),continuation,True,analysis.artifact_id,result.receipt.attempt_id)
        async with self.database.transaction() as conn:
            await conn.execute("INSERT INTO capabilities.consumptions(id,action_id,source_id,source_revision,body) VALUES(%s,%s,%s,%s,%s::jsonb) ON CONFLICT(id) DO NOTHING",(attempt.attempt_id,action.action_id,source.source_id,source.revision,canonical(wire(receipt))))
        return Receipt(str(uuid4()),action.action_id,attempt.attempt_id,"succeeded","none","not_requested",artifacts=(analysis,),coverage=Coverage(False,missing=tuple(decoded["gaps"]),ranges=(interval,),representation="sampled_frames_actual_audio"),result={"analysis":text,"consumption":wire(receipt)})
    async def reconcile(self,action,attempt):
        async with self.database.transaction(read_only=True) as conn:
            cursor=await conn.execute("SELECT body FROM capabilities.consumptions WHERE action_id=%s ORDER BY id DESC LIMIT 1",(action.action_id,));row=await cursor.fetchone()
        return Receipt(str(uuid4()),action.action_id,attempt.attempt_id,"succeeded" if row else "unknown","none","not_requested",result={"consumption":row["body"]} if row else {},error_code=None if row else "analysis_result_unknown")
    async def cancel(self,action,attempt):return await self.reconcile(action,attempt)
