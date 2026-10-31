"""Media maintenance uses the one Procrastinate scheduler, never private polling loops."""
from datetime import UTC,datetime,timedelta
from tianshu.types import ServiceClass
from tianshu.runtime.jobs import JobSpec

class Background:
    def __init__(self,runtime):
        self.runtime=runtime;self.operations={};self.states={};self.closed=False
    def start(self,name,operation,*args):
        self.operations[name]=(operation,args)
        self.states[name]={"state":"scheduled","restarts":0,"code":None}
        task="capabilities.media."+name
        if task not in self.runtime.jobs.tasks:
            async def run(**payload):await self.run(name,**payload)
            self.runtime.jobs.register(task,run)
    async def activate(self):
        for name in self.operations:await self.schedule(name,datetime.now(UTC))
    async def schedule(self,name,due):
        if self.closed:return
        async with self.runtime.database.transaction() as conn:
            await self.runtime.jobs.enqueue(conn,JobSpec("capabilities.media."+name,"media-maintenance:"+name+":"+str(int(due.timestamp())),{},ServiceClass.ELASTIC,scheduled_at=due))
    async def run(self,name):
        if self.closed:return
        from uuid import uuid4
        owner=str(uuid4())
        await self.schedule(name,datetime.now(UTC)+timedelta(seconds=10 if "worker" not in name else 2))
        async with self.runtime.database.transaction() as conn:
            row=await (await conn.execute("INSERT INTO capabilities.maintenance(name,owner,lease_until) VALUES(%s,%s,now()+interval '60 seconds') ON CONFLICT(name) DO UPDATE SET owner=excluded.owner,lease_until=excluded.lease_until WHERE capabilities.maintenance.lease_until<now() RETURNING name",(name,owner))).fetchone()
        if not row:return
        import asyncio
        async def renew():
            while True:
                await asyncio.sleep(15)
                async with self.runtime.database.transaction() as conn:await conn.execute("UPDATE capabilities.maintenance SET lease_until=now()+interval '60 seconds' WHERE name=%s AND owner=%s",(name,owner))
        heartbeat=asyncio.create_task(renew());operation,args=self.operations[name]
        self.states[name]["state"]="running"
        try:await operation(*args)
        except Exception:self.states[name].update(state="failed",code="media_background_failed");raise
        else:self.states[name].update(state="scheduled",code=None)
        finally:
            heartbeat.cancel();await asyncio.gather(heartbeat,return_exceptions=True)
            async with self.runtime.database.transaction() as conn:await conn.execute("UPDATE capabilities.maintenance SET lease_until=now() WHERE name=%s AND owner=%s",(name,owner))
    def view(self):return [{"name":name,**state} for name,state in self.states.items()]
    def healthy(self):return not self.closed and all(x["state"]!="failed" for x in self.states.values())
    async def close(self):self.closed=True
