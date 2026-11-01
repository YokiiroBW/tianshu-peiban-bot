"""Durable schedules with bounded catch-up and explicit overlap semantics."""

import asyncio
from datetime import datetime
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..contracts import Fault, digest, require
from .schema import check, DOCUMENTS


def next_run(trigger, now):
    if trigger["kind"] == "interval":
        return now + trigger["seconds"]
    try:
        zone = ZoneInfo(trigger["timezone"])
    except ZoneInfoNotFoundError:
        raise Fault("config_invalid", 400) from None
    fields = trigger["cron"].split()
    require(len(fields) == 5, "invalid_input", 400)
    ranges = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
    sets = []
    for field, (minimum, maximum) in zip(fields, ranges):
        values = set()
        for part in field.split(","):
            pieces = part.split("/")
            require(len(pieces) <= 2, "invalid_input", 400)
            step = int(pieces[1]) if len(pieces) == 2 else 1
            require(step > 0, "invalid_input", 400)
            limits = pieces[0].split("-")
            if pieces[0] == "*":
                start, end = minimum, maximum
            elif len(limits) == 2:
                start, end = map(int, limits)
            else:
                start = end = int(limits[0])
            require(minimum <= start <= end <= maximum, "invalid_input", 400)
            values.update(range(start, end + 1, step))
        sets.append(values)
    # One year is bounded and supports leap-day schedules without replaying old ticks.
    candidate = int(now // 60) * 60 + 60
    for _ in range(527040):
        date = datetime.fromtimestamp(candidate, zone)
        weekday = (date.weekday() + 1) % 7
        day_match = date.day in sets[2]
        week_match = weekday in sets[4] or weekday == 0 and 7 in sets[4]
        day_ok = (
            day_match and week_match
            if fields[2] == "*" or fields[4] == "*"
            else day_match or week_match
        )
        if date.minute in sets[0] and date.hour in sets[1] and date.month in sets[3] and day_ok:
            return candidate
        candidate += 60
    raise Fault("invalid_input", 400)


class Scheduler:
    def __init__(self, host):
        self.host = host

    async def manage(self, instance, operation, value):
        await self.host.ensure_active()
        repo = self.host.repository
        identifier = digest([instance, value["schedule_id"]])
        current = await self.host.local(repo.get, "schedules", identifier)
        require(
            value.get("expected_revision") == (current["revision"] if current else None),
            "revision_conflict",
            409,
        )
        if operation == "schedule.remove":
            require(current, "not_found", 404)
            document = {**current, "enabled": False, "revision": current["revision"] + 1}
        else:
            schema = next(
                item for item in DOCUMENTS if item["$id"].endswith("contributions.schema.json")
            )
            check(
                schema["properties"]["schedules"]["items"]["properties"]["default"],
                value["trigger"],
            )
            owner = await self.host.instance(instance)
            package = await self.host.package(owner["package_id"], owner["version"])
            action = next(
                (
                    item
                    for item in package["description"]["actions"]
                    if item["id"] == value["action"]
                ),
                None,
            )
            require(action, "not_found", 404)
            check(action["input_schema"], value["input"])
            document = {
                "id": identifier,
                "instance_id": instance,
                "schedule_id": value["schedule_id"],
                "action": value["action"],
                "input": value["input"],
                "trigger": value["trigger"],
                "revision": (current["revision"] if current else 0) + 1,
                "enabled": True,
                "next_run": next_run(value["trigger"], time.time()),
                "pending": False,
                "last_invocation": current.get("last_invocation") if current else None,
            }
        from ..execution_context import CURRENT_ACTION
        action=CURRENT_ACTION.get()
        require(action is not None,"trusted_action_required",403)
        from tianshu.runtime.serialization import wire
        document["context"]=wire(action.context)
        await self.host.local(repo.save_schedule,document,current["revision"] if current else None,self.enqueue)

        return {"schedule_revision": document["revision"], "removed": not document["enabled"]}

    async def seed(self, instance, description):
        from ..execution_context import CURRENT_ACTION
        grant=CURRENT_ACTION.get()
        if grant is None or grant.context.actor_id is None:return
        for item in description.get("schedules", []):
            current = await self.host.local(
                self.host.repository.get, "schedules", digest([instance, item["id"]])
            )
            if current is None:
                await self.manage(
                    instance,
                    "schedule.put",
                    {
                        "schedule_id": item["id"],
                        "action": item["action"],
                        "input": item["input"],
                        "trigger": item["default"],
                        "expected_revision": None,
                    },
                )

    def enqueue(self,connection,schedule):
        if not schedule["enabled"]:return
        from datetime import datetime,timezone
        from tianshu.runtime.jobs import JobSpec
        from tianshu.types import ServiceClass
        self.host.runtime.jobs.enqueue_sync(connection,JobSpec("capabilities.plugin_schedule",schedule["id"]+":"+str(schedule["revision"])+":"+str(schedule["next_run"]),{"schedule_id":schedule["id"],"revision":schedule["revision"],"due":schedule["next_run"]},service_class=ServiceClass.DUE,scheduled_at=datetime.fromtimestamp(schedule["next_run"],timezone.utc)))

    async def activate(self):
        jobs=self.host.runtime.jobs
        if "capabilities.plugin_schedule" not in jobs.tasks:jobs.register("capabilities.plugin_schedule",self.execute)
        for schedule in await self.host.local(self.host.repository.list,"schedules"):
            await self.host.local(self.host.repository.defer_schedule,schedule,self.enqueue)

    async def execute(self,schedule_id,revision,due):
        from dataclasses import replace
        from ..serialization import action_from
        from tianshu.types import Action,TrustedContext,OriginKind
        repo=self.host.repository;schedule=await self.host.local(repo.get,"schedules",schedule_id)
        if not schedule or not schedule["enabled"] or schedule["revision"]!=revision:return
        context=dict(schedule.get("context") or {})
        require(context,"schedule_context_unavailable",503)
        context["permissions"]=frozenset(context["permissions"]);context["scope_ids"]=tuple(context["scope_ids"]);context["audience"]=tuple(context["audience"]);context["origin_kind"]=OriginKind.TIMER
        context=TrustedContext(**context)
        await self.host.domain_ports.authority.check(context)
        instance=await self.host.instance(schedule["instance_id"])
        if instance["desired_state"]!="enabled":return
        await self.host.domain_ports.discover(context)
        binding=next((b for b in instance["bindings"] if b["enabled"] and b["scope"].get("character_id")==context.actor_id and {"kind":"action","id":schedule["action"]} in b.get("entries",[])),None)
        require(binding,"schedule_binding_unavailable",409)
        capability="plugin:"+instance["id"]+":"+binding["binding_id"]+":"+schedule["action"]
        now=time.time()
        previous=schedule.get("last_invocation")
        busy=False
        if previous:
            try:busy=(await self.host.domain_ports.actions.get(previous,context))["receipt"]["execution"] in {"pending","running","unknown"}
            except Fault:busy=False
        late=now-due>60
        should_run=not (late and schedule["trigger"]["misfire"]=="skip")
        if busy:
            if schedule["trigger"]["overlap"]=="queue_one":schedule["pending"]=True
            should_run=False
        elif schedule.get("pending"):schedule["pending"]=False;should_run=True
        key="schedule:"+digest([schedule_id,revision,due])
        action=Action(key,key,capability,context.actor_id,context,schedule["input"],OriginKind.TIMER)
        if should_run:
            from tianshu.types import ServiceClass
            result=await self.host.domain_ports.actions.invoke(action,background=True,service_class=ServiceClass.DUE)
            schedule["last_invocation"]=result.action_id
        schedule["next_run"]=next_run(schedule["trigger"],max(time.time(),due))
        await self.host.local(repo.save_schedule,schedule,revision,self.enqueue)
