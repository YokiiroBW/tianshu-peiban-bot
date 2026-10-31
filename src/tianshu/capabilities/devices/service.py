"""Registered Home Assistant readings and target-state control with observed receipts."""
import asyncio,re
from datetime import UTC,datetime
from uuid import uuid4
from urllib.parse import urlsplit,quote
import aiohttp
from tianshu.types import Receipt
from tianshu.runtime.serialization import wire
from ..catalog import CapabilityDefinition,Availability
from ..contracts import Fault,canonical,digest
from ..external import ProviderError
from ..tracking import mark_submitted

class Devices:
    definition=CapabilityDefinition("device.control","控制设备","调用登记的灯/开关模板，再读取实际目标状态。",{"type":"object","required":["connection_id","template_id","expected_revision"],"properties":{"connection_id":{"type":"string"},"template_id":{"type":"string"},"expected_revision":{"type":"integer"}}},{"type":"object"},"write",permissions=frozenset({"device.control"}))
    def __init__(self,configuration,authority):self.config,self.authority=configuration,authority
    async def availability(self,context):
        rows=await self.config.list("device_connection");ready=any(x.get("enabled") and x.get("configured") for x in rows)
        return Availability(True,ready,reason=None if ready else "device_not_configured")
    async def save(self,context,body):
        parsed=urlsplit(body.get("base_url",""))
        if parsed.scheme not in {"http","https"} or not parsed.hostname or parsed.username or parsed.password or parsed.path.strip("/") or parsed.query or parsed.fragment:raise Fault("device_address_invalid",400)
        entities=body.get("entities",[]);templates=body.get("templates",[])
        if not isinstance(entities,list) or not 1<=len(entities)<=32 or len(templates)>32:raise Fault("device_entities_invalid",400)
        ids=set()
        for entity in entities:
            identifier=entity.get("entity_id","")
            if not re.fullmatch(r"(?:light|switch|sensor)\.[a-z0-9_]{1,64}",identifier) or identifier in ids:raise Fault("device_entity_invalid",400)
            ids.add(identifier)
        tids=set()
        for template in templates:
            if template.get("entity_id") not in ids or template["entity_id"].split(".")[0] not in {"light","switch"} or template.get("service") not in {"turn_on","turn_off"} or not template.get("template_id") or template["template_id"] in tids:raise Fault("device_template_invalid",400)
            tids.add(template["template_id"])
        identifier=body.get("id") or "home";old=await self.config.get("device_connection",identifier)
        ready=bool(body.get("token") or old and old.get("configured"))
        value={"base_url":body["base_url"].rstrip("/"),"entities":entities,"templates":templates,"enabled":body.get("enabled",True),"configured":ready,"state":"configured" if ready else "not_configured","reason":None if ready else "token_required","timeout_seconds":min(max(body.get("timeout_seconds",5),1),15),"status_max_age_seconds":min(max(body.get("status_max_age_seconds",120),10),3600)}
        return await self.config.save("device_connection",identifier,value,body.get("expected_revision",0),secret={"token":body["token"]} if body.get("token") else None)
    async def connection(self,identifier):
        value=await self.config.get("device_connection",identifier)
        if not value or not value.get("enabled") or not value.get("configured"):raise Fault("device_not_configured",503)
        secret=await self.config.secret("device_connection",identifier)
        return value,secret["token"]
    async def http(self,config,token,path,body=None):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=config["timeout_seconds"])) as session:
            async with session.request("POST" if body is not None else "GET",config["base_url"]+path,headers={"Authorization":"Bearer "+token},json=body,allow_redirects=False) as response:
                if response.status in {400,401,403,404,405,409,410,422}:raise ProviderError("device_rejected",502)
                if response.status!=200:raise ProviderError("device_unknown",502,effect="unknown" if body is not None else "none")
                raw=await response.content.read(1_048_577)
                if len(raw)>1_048_576:raise ProviderError("device_response_budget",502,effect="unknown" if body is not None else "none")
                import json
                return json.loads(raw)
    async def observe(self,context,connection_id):
        await self.authority.check(context);config,token=await self.connection(connection_id)
        rows=await self.http(config,token,"/api/states");lookup={x["entity_id"]:x for x in rows if isinstance(x,dict)}
        result=[]
        async with self.config.database.transaction() as conn:
            for entity in config["entities"]:
                value=lookup.get(entity["entity_id"])
                old=await (await conn.execute("SELECT revision FROM capabilities.device_readings WHERE connection_id=%s AND entity_id=%s FOR UPDATE",(connection_id,entity["entity_id"]))).fetchone()
                observation={**entity,"revision":old["revision"]+1 if old else 1,"state":value.get("state") if value else None,"attributes":{k:v for k,v in (value.get("attributes",{}) if value else {}).items() if k in {"unit_of_measurement","friendly_name","brightness","color_temp_kelvin"}},"observed_at":datetime.now(UTC).isoformat(),"code":"observed" if value else "device_missing","connection_revision":config["revision"]}
                await conn.execute("INSERT INTO capabilities.device_readings(connection_id,entity_id,revision,body) VALUES(%s,%s,%s,%s::jsonb) ON CONFLICT(connection_id,entity_id) DO UPDATE SET revision=excluded.revision,body=excluded.body",(connection_id,entity["entity_id"],observation["revision"],canonical(observation)));result.append(observation)
        await self.authority.check(context)
        return {"connection_id":connection_id,"readings":result}
    async def view(self,context):
        await self.authority.check(context)
        async with self.config.database.transaction(read_only=True) as conn:rows=await (await conn.execute("SELECT body FROM capabilities.device_readings ORDER BY connection_id,entity_id")).fetchall()
        return {"connections":await self.config.list("device_connection"),"readings":[x["body"] for x in rows],"control_capability":"device.control"}
    async def execute(self,action,attempt):
        await self.authority.check(action.context,action.source_refs);args=action.arguments
        config,token=await self.connection(args["connection_id"])
        template=next((x for x in config["templates"] if x["template_id"]==args["template_id"]),None)
        if template is None:raise Fault("device_template_invalid",400)
        async with self.config.database.transaction(read_only=True) as conn:row=await (await conn.execute("SELECT body,revision FROM capabilities.device_readings WHERE connection_id=%s AND entity_id=%s",(config["id"],template["entity_id"]))).fetchone()
        if not row or row["revision"]!=args["expected_revision"] or row["body"]["connection_revision"]!=config["revision"]:raise Fault("device_reading_changed",409)
        if datetime.fromisoformat(row["body"]["observed_at"]).timestamp()+config["status_max_age_seconds"]<datetime.now(UTC).timestamp():raise Fault("device_reading_stale",409)
        target="on" if template["service"]=="turn_on" else "off"
        if row["body"]["state"]!=target:
            await self.authority.check(action.context,action.source_refs)
            await mark_submitted(action.action_id)
            await self.http(config,token,"/api/services/"+template["entity_id"].split(".")[0]+"/"+template["service"],{"entity_id":template["entity_id"]})
            await asyncio.sleep(0.3)
        return await self.reconcile(action,attempt)
    async def reconcile(self,action,attempt):
        config,_=await self.connection(action.arguments["connection_id"])
        template=next((x for x in config["templates"] if x["template_id"]==action.arguments["template_id"]),None)
        if template is None:raise Fault("device_template_changed",409)
        answer=await self.observe(action.context,config["id"]);row=next(x for x in answer["readings"] if x["entity_id"]==template["entity_id"])
        observed=row["state"]==("on" if template["service"]=="turn_on" else "off")
        return Receipt(str(uuid4()),action.action_id,attempt.attempt_id,"succeeded" if observed else "unknown","confirmed" if observed else "unknown","not_requested",result={"accepted":True,"observed":observed,"reading":row},error_code=None if observed else "control_unverified")
    async def cancel(self,action,attempt):return await self.reconcile(action,attempt)
