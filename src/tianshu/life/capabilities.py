"""Actual life use cases exposed through the application's single Action catalog."""
from tianshu.sources.dependencies import unique_refs
from uuid import NAMESPACE_URL,uuid5
from dataclasses import replace
from tianshu.capabilities.catalog import Availability,CapabilityDefinition
from tianshu.capabilities.dto import ReactionIntent
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import Receipt,OriginKind
from tianshu.conversation.codec import signature,source_refs


def completed(action,attempt,data,*,write=False,refs=(),revision=None):
    return Receipt(str(uuid5(NAMESPACE_URL,"life-tool-receipt:"+action.action_id)),action.action_id,attempt.attempt_id,
        "succeeded","confirmed" if write else "none","not_requested",result={**wire(data),"revision":revision,"source_refs":wire(refs)})


def unresolved(action,attempt):
    return Receipt(str(uuid5(NAMESPACE_URL,"life-tool-unknown:"+action.action_id)),action.action_id,attempt.attempt_id,
        "unknown","unknown","not_requested",error_code="original_command_unresolved")


class CommitmentHandler:
    definition=CapabilityDefinition("life.commitment","约定和提醒","保存明确到期约定，或按原ID和修订改期、取消。时间必须含时区；scheduled表示已安排，不表示已经提醒或完成。",{
        "type":"object","properties":{"operation":{"enum":["schedule","reschedule","cancel","list"]},"id":{"type":"string"},
        "expected_revision":{"type":["integer","null"]},"title":{"type":"string"},"due_at":{"type":["string","null"]},"timezone":{"type":"string"},
        "recurrence":{"enum":[None,"daily","weekly"]},"weekdays":{"type":"array","items":{"type":"integer","minimum":0,"maximum":6}},"matter_id":{"type":["string","null"]},
        "capability_id":{"type":["string","null"]},"arguments":{"type":"object"},"grace_seconds":{"type":"integer","minimum":0,"maximum":604800},"missed_policy":{"enum":["skip","once","reschedule"]},
        "wake_condition":{"type":["object","null"],"properties":{"kind":{"enum":["user_reply","activity_completed","capability_available"]},"subject_id":{"type":"string"}},"required":["kind","subject_id"],"additionalProperties":False}},
        "required":["operation"],"additionalProperties":False},{"type":"object"},"write",permissions=frozenset({"life.manage"}),public_command={"name":"提醒","aliases":["约定"]},model_tool={"name":"life_commitment"})
    def __init__(self,life):
        self.life=life

    async def availability(self,context):
        return Availability(True,context.actor_id is not None,reason=None if context.actor_id else "actor_required")

    def command(self,action):
        args=dict(action.arguments)
        operation=args.pop("operation")
        body={**args,"client_request_id":"action:"+action.action_id,"expected_revision":args.get("expected_revision"),"source_refs":wire(action.source_refs)}
        return operation,body

    async def execute(self,action,attempt):
        operation,body=self.command(action)
        if operation=="list":
            page=await self.life.commitments(action.context)
            refs=unique_refs(tuple(ref for item in page.items for ref in source_refs(item["source_refs"])))
            return completed(action,attempt,{"commitments":page},refs=refs)
        if operation=="schedule":
            result=await self.life.save_commitment(action.context,body,new_id=str(uuid5(NAMESPACE_URL,"commitment-tool:"+action.action_id)))
        elif operation=="reschedule":
            result=await self.life.reschedule_commitment(action.context,body)
        else:
            result=await self.life.cancel_commitment(action.context,body)
        return completed(action,attempt,{"commitment":result.data,"scheduled_effect_only":True},write=True,refs=action.source_refs,revision=result.revision)

    async def reconcile(self,action,attempt):
        operation,body=self.command(action)
        if operation=="list":
            return await self.execute(action,attempt)
        name={"schedule":"save","reschedule":"reschedule","cancel":"cancel"}[operation]
        identity=await self.life.commands.replay(action.context,"life.commitments."+name,body["client_request_id"],signature(body))
        if identity is None:
            return unresolved(action,attempt)
        identifier=body.get("id") or str(uuid5(NAMESPACE_URL,"commitment-tool:"+action.action_id))
        async with self.life.database.transaction(read_only=True) as connection:
            row=await (await connection.execute("SELECT * FROM life.commitments WHERE id=%s AND actor_id=%s AND scope_ids<@%s::text[]",(identifier,action.context.actor_id,list(action.context.scope_ids)))).fetchone()
        if not row:
            return unresolved(action,attempt)
        refs=source_refs(row["source_refs"])
        await self.life.identity.check(action.context,refs)
        return completed(action,attempt,{"commitment":row,"scheduled_effect_only":True},write=True,refs=refs,revision=row["revision"])

    async def cancel(self,action,attempt):
        # Cancelling execution is different from a new request to cancel an already saved promise.
        return await self.reconcile(action,attempt)


class ReadingHandler:
    definition=CapabilityDefinition("life.reading","持续阅读","打开有权读取的来源，继续读取实际范围，或暂停/关闭阅读。返回覆盖范围，不把取到片段称为已理解。",{
        "type":"object","properties":{"operation":{"enum":["open","read","pause","resume","close","list"]},"id":{"type":"string"},"expected_revision":{"type":["integer","null"]},
        "source_ref":{"type":"object","properties":{"source_id":{"type":"string"},"revision":{"type":"integer"},"locator":{"type":"object"}},"required":["source_id","revision"]},"requested":{"type":"object"}},"required":["operation"],"additionalProperties":False},
        {"type":"object"},"write",permissions=frozenset({"life.manage"}),public_command={"name":"阅读","aliases":[]},model_tool={"name":"life_reading"})
    def __init__(self,reading):
        self.reading=reading

    async def availability(self,context):
        return Availability(True,context.actor_id is not None,reason=None if context.actor_id else "actor_required")

    async def execute(self,action,attempt):
        args=dict(action.arguments)
        operation=args.pop("operation")
        if operation=="list":
            result=await self.reading.list(action.context)
            refs=unique_refs(tuple(ref for row in result.items for ref in source_refs((row["source_ref"],))))
            return completed(action,attempt,{"readings":result},refs=refs)
        body={**args,"client_request_id":"action:"+action.action_id,"expected_revision":args.get("expected_revision")}
        if operation=="open":
            result=await self.reading.open(action.context,body)
        elif operation=="read":
            result=await self.reading.read(action.context,body)
        else:
            result=await self.reading.set_state(action.context,body,{"pause":"paused","resume":"reading","close":"closed"}[operation])
        refs=source_refs((result.data["source_ref"],)) if result.data and result.data.get("source_ref") else action.source_refs
        return completed(action,attempt,{"reading":result.data},write=True,refs=refs,revision=result.revision)

    async def reconcile(self,action,attempt):
        operation=action.arguments["operation"]
        if operation=="list":
            return await self.execute(action,attempt)
        args={key:value for key,value in action.arguments.items() if key!="operation"}
        body={**args,"client_request_id":"action:"+action.action_id,"expected_revision":args.get("expected_revision")}
        suffix={"pause":"paused","resume":"reading","close":"closed"}.get(operation,operation)
        original=await self.reading.life.commands.replay(action.context,"life.reading."+suffix,body["client_request_id"],signature(body))
        if original is None:
            return unresolved(action,attempt)
        if operation in {"read","open"}:
            return await self.execute(action,attempt)
        async with self.reading.life.database.transaction(read_only=True) as connection:
            row=await self.reading._reading(action.context,body["id"],connection)
        refs=source_refs((row["source_ref"],))
        await self.reading.life.identity.check(action.context,refs)
        return completed(action,attempt,{"reading":row},write=True,refs=refs,revision=row["revision"])

    async def cancel(self,action,attempt):
        return await self.reconcile(action,attempt)


class AvailabilityHandler:
    definition=CapabilityDefinition("life.availability","当前联系处境","仅在当前说话者明确表示自己正在忙、睡觉、外出或恢复空闲时，记录带原消息时效的临时处境。不能根据引用别人、历史材料或未回复推断；到期只恢复未知，不表示已醒或已到家。明确长期免打扰仍用原联系设置。",{
        "type":"object","properties":{"availability":{"enum":["unknown","free","busy","sleeping","away"]},"valid_for_minutes":{"type":"integer","minimum":1,"maximum":1440},"available_until":{"type":["string","null"]}},"required":["availability"],"additionalProperties":False},
        {"type":"object"},"write",permissions=frozenset({"life.manage"}),model_tool={"name":"life_availability"})

    def __init__(self,life):
        self.life=life

    async def availability(self,context):
        return Availability(True,bool(context.actor_id and context.person_id and context.conversation_id))

    async def execute(self,action,attempt):
        if action.origin_kind!=OriginKind.HUMAN:
            raise AppError("availability_human_source_required",status=403)
        from .availability import save
        refs=tuple(action.current_input_refs)
        body={**action.arguments,"client_request_id":"action:"+action.action_id}
        result=await save(self.life,action.context,body,refs)
        state=result.data
        if state is None:
            current=await self.life.contacts.preferences(action.context)
            state={"availability":current['availability'],"available_until":current['available_until']}
        return completed(action,attempt,{"contact_state":state,"basis":"human_self_report","reported_by":action.context.person_id},write=True,refs=refs,revision=result.revision)

    async def reconcile(self,action,attempt):
        body={**action.arguments,"client_request_id":"action:"+action.action_id}
        original=await self.life.commands.replay(action.context,"life.availability",body["client_request_id"],signature({"body":body,"refs":tuple(action.current_input_refs)}))
        if original is None:
            return unresolved(action,attempt)
        current=await self.life.contacts.preferences(action.context)
        return completed(action,attempt,{"contact_state":{"availability":current["availability"],"available_until":current["available_until"]},"basis":"human_self_report","reported_by":action.context.person_id},write=True,refs=action.current_input_refs,revision=original.revision)

    async def cancel(self,action,attempt):
        return await self.reconcile(action,attempt)


class WritingRequestHandler:
    definition=CapabilityDefinition("writing.request","安排章节创作","对已有章节冻结创作材料并安排写作，返回真实模型任务身份。accepted表示已安排，正文完成需查询章节；不会宣称排队就是写完。",{
        "type":"object","properties":{"id":{"type":"string"},"expected_revision":{"type":"integer"},"instruction":{"type":"string"}},"required":["id","expected_revision"],"additionalProperties":False},
        {"type":"object"},"write",permissions=frozenset({"life.manage"}),public_command={"name":"续写","aliases":[]},model_tool={"name":"writing_request"})
    def __init__(self,writing):
        self.writing=writing

    async def availability(self,context):
        return Availability(True,context.actor_id is not None,reason=None if context.actor_id else "actor_required")

    async def execute(self,action,attempt):
        body={**action.arguments,"client_request_id":"action:"+action.action_id}
        result=await self.writing.request_generation(action.context,body)
        return completed(action,attempt,{"accepted":True,"chapter":result.data,"model_attempt_id":result.attempt_id,"content_completed":False},write=True,refs=action.source_refs,revision=result.revision)

    async def reconcile(self,action,attempt):
        body={**action.arguments,"client_request_id":"action:"+action.action_id}
        original=await self.writing.life.commands.replay(action.context,"life.chapters.generate",body["client_request_id"],signature(body))
        if original is None:
            return unresolved(action,attempt)
        async with self.writing.life.database.transaction(read_only=True) as connection:
            row=await self.writing._chapter(action.context,body["id"],connection)
        await self.writing.life.identity.check(action.context,source_refs(row["source_refs"]))
        return completed(action,attempt,{"accepted":True,"chapter_id":row["id"],"state":row["state"],"model_attempt_id":row["generation_id"],"content_completed":bool(row["current_revision"])},write=True,refs=action.source_refs,revision=row["revision"])

    async def cancel(self,action,attempt):
        return await self.reconcile(action,attempt)


class ReactionSelectionHandler:
    definition=CapabilityDefinition("reactions.select","选择表情","根据此刻想表达的含义、用途和态度选择受权原始表情；没有合适素材时返回无选择，不按用户关键词机械触发。",{
        "type":"object","properties":{"meaning":{"type":"string"},"purpose":{"type":"string"},"attitude":{"type":"string"},"intensity":{"type":"number","minimum":0,"maximum":1},"ironic":{"type":"boolean"},"recent_asset_ids":{"type":"array","items":{"type":"string"}}},"required":["meaning","purpose","attitude","intensity"],"additionalProperties":False},
        {"type":"object"},"read",model_tool={"name":"reactions_select"})
    def __init__(self,reactions,artifacts):
        self.reactions,self.artifacts=reactions,artifacts

    async def availability(self,context):
        return Availability(True,context.actor_id is not None,reason=None if context.actor_id else "actor_required")

    async def execute(self,action,attempt):
        args=action.arguments
        intent=ReactionIntent(args["meaning"],args["purpose"],args["attitude"],args["intensity"],action.context.actor_id,action.context.scope_ids,args.get("ironic",False),tuple(args.get("recent_asset_ids",())))
        selected=await self.reactions.search(action.context,intent,action.source_refs)
        if selected["selected"] is None:
            return completed(action,attempt,{"selected":None,"reason":selected["reason"]},refs=action.source_refs)
        artifact=await self.artifacts.reference(action.context,selected["selected"]["media"]["media_id"])
        refs=unique_refs(tuple(ref for ref in (*action.source_refs,*artifact.source_refs)))
        result=completed(action,attempt,{"selected":selected["selected"]},refs=refs)
        return replace(result,artifacts=(artifact,))

    async def reconcile(self,action,attempt):
        return unresolved(action,attempt)

    async def cancel(self,action,attempt):
        return unresolved(action,attempt)


def register_handlers(catalog,*,life,reading,writing,reactions,artifacts):
    catalog.register(CommitmentHandler(life))
    catalog.register(AvailabilityHandler(life))
    catalog.register(ReadingHandler(reading))
    catalog.register(WritingRequestHandler(writing))
    catalog.register(ReactionSelectionHandler(reactions,artifacts))
