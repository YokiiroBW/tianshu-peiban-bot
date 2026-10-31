"""Verified GSCore semantic queries; returned fragments do not prove full completion."""
import base64,io,json,re
from pathlib import Path
from uuid import uuid4
import httpx
from PIL import Image
from tianshu.types import Receipt,Coverage
from ..catalog import CapabilityDefinition,Availability
from ..contracts import Fault,digest
from ..external import ProviderError
from ..tracking import mark_submitted
MAPPING=json.loads(Path(__file__).with_name("gscore-commands.json").read_text(encoding="utf-8"))
class Games:
    definition=CapabilityDefinition("game.query","游戏资料","按已核验语义命令读取 GSCore 实际返回片段。",{"type":"object","properties":{"game":{"enum":sorted({x["game"] for x in MAPPING["commands"]})},"operation":{"enum":["guide","team","team_statistics"]},"character":{"type":"string","maxLength":40}},"required":["game","operation"],"additionalProperties":False},{"type":"object"})
    def __init__(self,configuration,artifacts,authority):self.config,self.artifacts,self.authority=configuration,artifacts,authority
    async def availability(self,context):
        value=await self.config.get("game_connection",context.actor_id or "default")
        return Availability(True,bool(value and value.get("configured") and value.get("enabled",True)),reason=None if value and value.get("configured") else "gscore_not_configured")
    def command(self,args):
        mapping=next((x for x in MAPPING["commands"] if x["game"]==args["game"] and x["operation"]==args["operation"]),None);character=args.get("character","")
        if mapping is None or not re.fullmatch(r"[\w\u4e00-\u9fff .·-]{0,40}",character) or mapping["character_required"] and not character.strip():raise Fault("game_query_invalid",400)
        return mapping["template"].format(character=character.strip(),optional_character=" "+character.strip() if character.strip() else "")
    async def execute(self,action,attempt):
        command=self.command(action.arguments);config=await self.config.get("game_connection",action.actor_id or "default")
        if not config or not config.get("configured"):raise Fault("gscore_not_configured",503)
        secret=await self.config.secret("game_connection",config["id"]);identity="tianshu-skills:"+digest(action.actor_id)[:24]
        body={"bot_id":"TianshuSkills","bot_self_id":"","msg_id":action.action_id,"user_type":"direct","group_id":None,"user_id":identity,"sender":{},"user_pm":6,"content":[{"type":"text","data":command}]}
        await self.authority.check(action.context,action.source_refs);await mark_submitted(action.action_id)
        async with httpx.AsyncClient(timeout=25,follow_redirects=False) as client:
            async with client.stream("POST",config["base_url"].rstrip("/")+"/api/send_msg",json=body,headers={"X-WS-Token":secret["token"]}) as response:
                if response.status_code!=200:raise ProviderError("gscore_unavailable",502,effect="unknown")
                raw=bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw)>8_000_000:raise ProviderError("gscore_response_budget",413,effect="unknown")
        answer=json.loads(raw);data=answer.get("data",{})
        if answer.get("status_code")!=200 or any(data.get(k)!=v for k,v in {"bot_id":"TianshuSkills","bot_self_id":"","msg_id":action.action_id,"target_type":"direct","target_id":identity}.items()):raise ProviderError("gscore_unconfirmed_output",502,effect="unknown")
        artifacts=[];texts=[];unsupported=[]
        fragments=data.get("content",[])
        if not isinstance(fragments,list) or len(fragments)>16:raise ProviderError("gscore_response_invalid",502,effect="unknown")
        for fragment in fragments:
            kind,value=fragment.get("type"),fragment.get("data")
            if kind in {"text","markdown"} and isinstance(value,str):texts.append(value[:8000])
            elif kind=="image" and isinstance(value,str) and value.startswith("base64://"):
                try:
                    image=base64.b64decode(value[9:],validate=True)
                    with Image.open(io.BytesIO(image)) as checked:
                        if checked.format!="PNG" or checked.width*checked.height>32_000_000:raise ValueError()
                        checked.verify()
                    artifacts.append(await self.artifacts.put(action.context,image,"image/png",source_refs=action.source_refs,metadata={"kind":"gscore_fragment","query":dict(action.arguments),"protocol_version":MAPPING["version"]}))
                except (ValueError,OSError):unsupported.append({"type":"image","reason":"unsupported_or_invalid_png"})
            else:unsupported.append({"type":kind,"reason":"unsupported_content"})
        await self.authority.check(action.context,action.source_refs)
        return Receipt(str(uuid4()),action.action_id,attempt.attempt_id,"succeeded","none","not_requested",artifacts=tuple(artifacts),coverage=Coverage(False,missing=("full_handler_completion",),representation="first_fragment_only"),result={"text":"\n".join(texts),"complete":False,"unsupported_content":unsupported,"query":dict(action.arguments),"team_statistics_meaning":"matrix_appearance_high_score_statistics" if action.arguments["operation"]=="team_statistics" else None})
    async def reconcile(self,action,attempt):return Receipt(str(uuid4()),action.action_id,attempt.attempt_id,"unknown","unknown","not_requested",error_code="gscore_no_query_handle")
    async def cancel(self,action,attempt):return await self.reconcile(action,attempt)
