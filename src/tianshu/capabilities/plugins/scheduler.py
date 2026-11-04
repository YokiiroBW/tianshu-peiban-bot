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
            if current and (current.get("dispatch") or current.get("previous_dispatch")):
                # An explicit edit may not erase a tick that was reserved or
                # admitted under the preceding schedule revision.
                document["previous_dispatch"] = current.get("dispatch") or current["previous_dispatch"]
        from ..execution_context import CURRENT_ACTION, current_generation
        action=CURRENT_ACTION.get()
        require(action is not None,"trusted_action_required",403)
        generation = current_generation()
        require(generation is not None, "action_generation_unavailable", 503)
        from tianshu.runtime.serialization import wire
        document["context"]=wire(action.context)
        document["data_generation"] = generation
        document["required_permission"] = "config.publish" if action.capability_id.startswith("plugins.") else None
        await self.host.local(repo.save_schedule,document,current["revision"] if current else None,self.enqueue)

        return {"schedule_revision": document["revision"], "removed": not document["enabled"]}

    async def seed(self, instance, description):
        from ..execution_context import CURRENT_ACTION
        grant=CURRENT_ACTION.get()
        if grant is None:return
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
        generation = schedule.get("wake_generation", 0)
        key = schedule["id"] + ":" + str(schedule["revision"]) + ":" + str(schedule["next_run"])
        payload = {"schedule_id": schedule["id"], "revision": schedule["revision"], "due": schedule["next_run"]}
        if generation:
            key += ":wake:" + str(generation)
            payload["wake_generation"] = generation
        self.host.runtime.jobs.enqueue_sync(connection, JobSpec(
            "capabilities.plugin_schedule", key, payload, service_class=ServiceClass.DUE,
            scheduled_at=datetime.fromtimestamp(schedule["next_run"],timezone.utc),
            data_generation=schedule["data_generation"]))

    async def activate(self):
        jobs=self.host.runtime.jobs
        if "capabilities.plugin_schedule" not in jobs.tasks:jobs.register("capabilities.plugin_schedule",self.execute,retryable=True)
        for schedule in await self.host.local(self.host.repository.list,"schedules"):
            if schedule.get("state") not in {"waiting_binding", "waiting_instance", "blocked"}:
                await self.host.local(self.host.repository.defer_schedule,schedule,self.enqueue)

    async def instance_changed(self, instance_id):
        """Wake only timers waiting on this instance's binding/enable state."""
        for schedule in await self.host.local(self.host.repository.list, "schedules"):
            if (schedule["instance_id"] != instance_id or not schedule["enabled"]
                    or schedule.get("state") not in {"waiting_binding", "waiting_instance"}):
                continue
            schedule.update(state="scheduled", code=None,
                            wake_generation=schedule.get("wake_generation", 0) + 1)
            await self.host.local(self.host.repository.save_schedule, schedule,
                                  schedule["revision"], self.enqueue)

    async def execute(self,schedule_id,revision,due,wake_generation=0):
        from tianshu.types import Action,TrustedContext,OriginKind,ServiceClass
        from tianshu.runtime.serialization import wire
        from tianshu.runtime.errors import AppError
        from .actor_bridge import binding_visible
        repo=self.host.repository;schedule=await self.host.local(repo.get,"schedules",schedule_id)
        if (not schedule or not schedule["enabled"] or schedule["revision"]!=revision
                or schedule["next_run"] != due
                or schedule.get("wake_generation", 0) != wake_generation):return
        context=dict(schedule.get("context") or {})
        require(context,"schedule_context_unavailable",503)
        context["permissions"]=frozenset(context["permissions"]);context["scope_ids"]=tuple(context["scope_ids"]);context["audience"]=tuple(context["audience"]);context["origin_kind"]=OriginKind.TIMER
        context=TrustedContext(**context)
        # Renew only the timer's original grant; an unrelated epoch change is
        # not a new execution, and losing its scope still stops the timer.
        try:
            context = await self.host.domain_ports.authority.refresh_scheduled_context(
                context, permission=schedule.get("required_permission"), require_actor=False)
            await self.host.domain_ports.authority.check(context)
        except AppError as error:
            if error.status not in {401, 403, 410} and error.code != "actor_disabled":
                raise
            schedule.update(state="blocked", code=error.code)
            await self.host.local(repo.wait_schedule, schedule, revision)
            return
        instance=await self.host.instance(schedule["instance_id"])
        previous_dispatch = schedule.get("previous_dispatch")
        if previous_dispatch:
            from ..serialization import context_from
            previous_context = context_from(previous_dispatch["context"])
            try:
                previous_context = await self.host.domain_ports.authority.refresh_scheduled_context(
                    previous_context, permission=previous_dispatch.get("required_permission"), require_actor=False)
                previous = await self.host.domain_ports.actions.find(
                    previous_dispatch["capability_id"], previous_dispatch["action_key"], previous_context)
            except AppError as error:
                if error.status not in {401, 403, 410} and error.code != "actor_disabled":
                    raise
                schedule.update(state="blocked", code=error.code)
                await self.host.local(repo.wait_schedule, schedule, revision)
                return
            if previous is not None:
                schedule["last_invocation"] = previous["receipt"]["action_id"]
            elif previous_dispatch.get("admitted"):
                schedule.update(state="blocked", code="scheduled_action_missing")
                await self.host.local(repo.wait_schedule, schedule, revision)
                return
            elif schedule.get("last_invocation") == previous_dispatch["action_key"]:
                schedule["last_invocation"] = None
            schedule.pop("previous_dispatch")
            await self.host.local(repo.wait_schedule, schedule, revision)
        binding=next((b for b in instance["bindings"] if b["enabled"] and binding_visible(context, b)
                      and {"kind":"action","id":schedule["action"]} in b.get("entries",[])),None)
        reserved = schedule.get("dispatch")
        if reserved and reserved.get("due") != due:
            raise Fault("schedule_dispatch_changed", 409)
        capability = (reserved["capability_id"] if reserved else
                      "plugin:"+instance["id"]+":"+binding["binding_id"]+":"+schedule["action"]
                      if binding else None)
        key = reserved["action_key"] if reserved else "schedule:"+digest([schedule_id,revision,due])

        # Observation must precede a replacement binding or a second invoke.
        existing = (await self.host.domain_ports.actions.find(capability, key, context)
                    if capability else None)
        if existing is None and reserved and reserved.get("admitted"):
            schedule.update(state="blocked", code="scheduled_action_missing")
            await self.host.local(repo.wait_schedule, schedule, revision)
            return
        if existing is None and instance["desired_state"] != "enabled":
            schedule.update(state="waiting_instance", code="instance_disabled")
            await self.host.local(repo.wait_schedule, schedule, revision)
            return
        current_capability = ("plugin:"+instance["id"]+":"+binding["binding_id"]+":"+schedule["action"]
                              if binding else None)
        if existing is None and (not binding or current_capability != capability):
            schedule.update(state="waiting_binding", code="schedule_binding_unavailable")
            await self.host.local(repo.wait_schedule, schedule, revision)
            return

        now=time.time()
        previous=schedule.get("last_invocation")
        busy=False
        if previous and previous != key:
            # Failure to observe an old invocation is not proof that it ended.
            prior_receipt = (await self.host.domain_ports.actions.get(previous,context))["receipt"]
            busy = (prior_receipt["execution"] in {"pending", "running", "unknown"}
                    or prior_receipt.get("effect") == "unknown")
        late=now-due>60
        should_run=not (late and schedule["trigger"]["misfire"]=="skip")
        if busy:
            if schedule["trigger"]["overlap"]=="queue_one":schedule["pending"]=True
            should_run=False
        elif schedule.get("pending"):schedule["pending"]=False;should_run=True
        if existing is not None:
            # A crash may have occurred after admission and before the timer
            # checkpoint. Observe that original action even after an epoch edit.
            schedule["last_invocation"] = existing["receipt"]["action_id"]
        elif should_run:
            await self.host.domain_ports.discover(context)
            if reserved is None:
                # Reserve this tick's exact binding and action before admission.
                # A restart observes the same key even if bindings were edited.
                reserved = {"due": due, "capability_id": capability, "action_key": key, "admitted": False,
                            "context": wire(context), "required_permission": schedule.get("required_permission")}
                schedule["dispatch"] = reserved
                schedule["last_invocation"] = key
                await self.host.local(repo.wait_schedule, schedule, revision)
            action=Action(key,key,capability,context.actor_id,context,schedule["input"],OriginKind.TIMER)
            result=await self.host.domain_ports.actions.invoke(action,background=True,service_class=ServiceClass.DUE,
                                                               generation=schedule["data_generation"])
            schedule["last_invocation"]=result.action_id
            reserved["admitted"] = True
            await self.host.local(repo.wait_schedule, schedule, revision)
        schedule["context"] = wire(context)
        schedule.pop("dispatch", None)
        schedule.update(state="scheduled", code=None)
        schedule["next_run"]=next_run(schedule["trigger"],max(time.time(),due))
        schedule["wake_generation"] = 0
        await self.host.local(repo.save_schedule,schedule,revision,self.enqueue)
