"""Observed plugin child health shared by API and the sole existing plugin host."""

import asyncio


class RuntimeState:
    def __init__(self, host):
        self.host = host
        self.task = None

    def owner(self):
        ports = getattr(self.host, "domain_ports", None)
        actions = getattr(ports, "actions", None)
        return actions.status() if actions is not None else {}

    async def publish(self):
        owner = self.owner()
        if not owner.get("owner_id"):
            return
        samples = []
        if owner.get("ready") and self.host.started and not self.host.closing:
            samples = [
                {
                    "id": identifier,
                    "revision": revision,
                    "generation": process.runtime_id,
                    "pid": process.process.pid,
                }
                for identifier, (process, revision) in tuple(self.host.workers.items())
                if process.alive
            ]
        if samples:
            await self.host.local(self.host.repository.observe_runtime, owner["owner_id"], samples)

    async def started(self, identifier, process, revision):
        owner = self.owner()
        if owner.get("ready"):
            await self.host.local(
                self.host.repository.observe_runtime,
                owner["owner_id"],
                [
                    {
                        "id": identifier,
                        "revision": revision,
                        "generation": process.runtime_id,
                        "pid": process.process.pid,
                    }
                ],
                starting=True,
            )

    def start(self):
        if self.task is None or self.task.done():
            self.task = self.host.spawn(self.loop())

    async def loop(self):
        while self.host.started and not self.host.closing:
            try:
                await self.publish()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Failure does not renew health. The observation expires within
                # 15s, independently of the execution owner's broader lease.
                pass
            await asyncio.sleep(5)

    async def forget(self, identifier, process):
        owner_id = self.owner().get("owner_id")
        if owner_id:
            await self.host.local(
                self.host.repository.forget_runtime,
                identifier,
                owner_id,
                process.runtime_id,
            )
