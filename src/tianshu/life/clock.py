"""Persisted local-day rhythm; sleeping and idle gaps do not poll the model."""
from datetime import UTC,datetime,time,timedelta
from zoneinfo import ZoneInfo
from tianshu.runtime.api import CommandResult
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.types import ServiceClass
from tianshu.conversation.codec import context_from_record,document,signature
from .validation import revision,timezone
from .planning import signal


def hour(value):
    try:
        parsed=time.fromisoformat(value)
        if parsed.tzinfo is not None or parsed.second or parsed.microsecond:
            raise ValueError
        return parsed
    except (ValueError,TypeError):
        raise AppError("invalid_routine_time") from None


def awake_and_due(row,now):
    local=now.astimezone(ZoneInfo(row["timezone"]))
    wake,sleep=hour(row["wake_at"]),hour(row["sleep_at"])
    current=local.time().replace(tzinfo=None)
    awake=(wake<=current<sleep) if wake<sleep else (current>=wake or current<sleep)
    if awake:
        due=local+timedelta(seconds=row["idle_seconds"])
        next_sleep=datetime.combine(local.date(),sleep,local.tzinfo)
        if next_sleep<=local:
            next_sleep+=timedelta(days=1)
        due=min(due,next_sleep)
    else:
        due=datetime.combine(local.date(),wake,local.tzinfo)
        if due<=local:
            due+=timedelta(days=1)
    return awake,due.astimezone(UTC)


class LifeClock:
    def __init__(self,database,commands,jobs,identity,sources):
        self.database,self.commands,self.jobs,self.identity,self.sources=database,commands,jobs,identity,sources

    async def activate_later(self,context,actor_id,*,revision,connection):
        from tianshu.runtime.serialization import wire
        await self.jobs.enqueue(connection,JobSpec("life.activate",f"life-activate:{actor_id}:{revision}",{"actor_id":actor_id,"context":wire(context)},ServiceClass.INTERACTIVE,subject_id=actor_id))

    async def activate(self,actor_id,context):
        context=await self.identity.authorize_actor(context_from_record(context),actor_id)
        await self.ensure(context)

    async def ensure(self,context):
        await self.identity.check(context)
        async with self.database.transaction(read_only=True) as probe:
            old=await (await probe.execute("SELECT context FROM life.routines WHERE actor_id=%s",(context.actor_id,))).fetchone()
        if old and old["context"].get("conversation_id"):
            context=await self.identity.authorize_conversation(context,old["context"]["conversation_id"])
        async with self.database.transaction() as connection:
            if not await self.sources.validate_persistence(connection,context,()):
                raise AppError("actor_unavailable",status=403)
            row=await (await connection.execute("INSERT INTO life.routines(actor_id,context,next_due_at) VALUES (%s,%s,now()) ON CONFLICT(actor_id) DO UPDATE SET context=excluded.context RETURNING *",(context.actor_id,document(context)))).fetchone()
            if row and row["enabled"]:
                await self._enqueue(connection,row)

    async def view(self,context):
        await self.identity.check(context)
        async with self.database.transaction(read_only=True) as connection:
            row=await (await connection.execute("SELECT actor_id,revision,enabled,timezone,wake_at,sleep_at,idle_seconds,next_due_at,last_tick_at FROM life.routines WHERE actor_id=%s",(context.actor_id,))).fetchone()
        return row or {"actor_id":context.actor_id,"revision":None,"enabled":True,"timezone":"Asia/Shanghai","wake_at":"08:00","sleep_at":"23:00","idle_seconds":5400,"next_due_at":None,"last_tick_at":None}

    async def save(self,context,body):
        await self.identity.check(context)
        wake,sleep=body.get("wake_at"),body.get("sleep_at")
        if hour(wake)==hour(sleep):
            raise AppError("invalid_routine_window")
        zone=timezone(body.get("timezone"))
        idle=body.get("idle_seconds")
        if type(idle) is not int or not 900<=idle<=86400 or type(body.get("enabled")) is not bool:
            raise AppError("invalid_routine")
        async def apply(connection):
            if not await self.sources.validate_persistence(connection,context,()):
                raise AppError("actor_unavailable",status=403)
            old=await (await connection.execute("SELECT * FROM life.routines WHERE actor_id=%s FOR UPDATE",(context.actor_id,))).fetchone()
            revision(old,body.get("expected_revision"))
            current=old["revision"]+1 if old else 1
            row=await (await connection.execute("INSERT INTO life.routines(actor_id,revision,enabled,timezone,wake_at,sleep_at,idle_seconds,context,next_due_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now()) ON CONFLICT(actor_id) DO UPDATE SET revision=excluded.revision,enabled=excluded.enabled,timezone=excluded.timezone,wake_at=excluded.wake_at,sleep_at=excluded.sleep_at,idle_seconds=excluded.idle_seconds,context=excluded.context,next_due_at=excluded.next_due_at RETURNING *",(context.actor_id,current,body["enabled"],zone,wake,sleep,idle,document(context)))).fetchone()
            if row["enabled"]:
                await self._enqueue(connection,row)
            await connection.execute("UPDATE life.actor_state SET timezone=%s,revision=revision+1 WHERE actor_id=%s",(zone,context.actor_id))
            return CommandResult(body["client_request_id"],{"actor_id":context.actor_id,"revision":current},current)
        return await self.commands.execute(context,"life.routines.save",body["client_request_id"],signature(body),apply)

    async def _enqueue(self,connection,row):
        await self.jobs.enqueue(connection,JobSpec("life.clock",f"life-clock:{row['actor_id']}:{row['revision']}:{row['next_due_at'].isoformat()}",{"actor_id":row["actor_id"],"expected_revision":row["revision"],"due_at":row["next_due_at"].isoformat()},ServiceClass.ELASTIC,row["next_due_at"],row["actor_id"]))

    async def tick(self,actor_id,expected_revision,due_at):
        async with self.database.transaction(read_only=True) as connection:
            row=await (await connection.execute("SELECT * FROM life.routines WHERE actor_id=%s",(actor_id,))).fetchone()
        if not row or not row["enabled"] or row["revision"]!=expected_revision or row["next_due_at"].isoformat()!=due_at:
            return
        context=context_from_record(row["context"])
        await self.identity.check(context)
        now=datetime.now(UTC)
        awake,due=awake_and_due(row,now)
        async with self.database.transaction() as connection:
            if not await self.sources.validate_persistence(connection,context,()):
                return
            fresh=await (await connection.execute("SELECT * FROM life.routines WHERE actor_id=%s FOR UPDATE",(actor_id,))).fetchone()
            if not fresh["enabled"] or fresh["revision"]!=expected_revision or fresh["next_due_at"].isoformat()!=due_at or fresh["next_due_at"]>now:
                return
            fresh={**fresh,"next_due_at":due}
            await connection.execute("UPDATE life.routines SET next_due_at=%s,last_tick_at=%s WHERE actor_id=%s",(due,now,actor_id))
            await self._enqueue(connection,fresh)
            if awake:
                await signal(connection,self.jobs,context,"rhythm:"+due_at)

    async def recover(self):
        async with self.database.transaction() as connection:
            rows=await (await connection.execute("SELECT * FROM life.routines WHERE enabled=true")).fetchall()
            for row in rows:
                await self._enqueue(connection,row)
