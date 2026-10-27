"""One business process, with native application ownership and no internal TCP."""

import argparse
import asyncio
import os
import signal
import time

from .applications import NativeModule, app_platform
from .host_settings import MODULES, load_settings, module_environment
from .runtime import write_state
from .threaded_exchange import ThreadedExchange


class BusinessHost:
    def __init__(self, config, *, module_factory=NativeModule):
        self.config = config
        self.exchange = ThreadedExchange()
        self.modules, self.environments = {}, {}
        self.pending_probes = {}
        self.stopping = False
        self.close_task = None
        self.previous_environment = None
        self.started = False
        self.results = {
            name: {"state": "starting", "health": {"ok": False, "code": "starting"}}
            for name in config["modules"]
        }
        # Native Gateway startup renews its Platform authority. JSON object order
        # is configuration spelling, not a dependency declaration. OneBot's owned
        # instance is bound while constructing Platform; other native modules keep
        # the existing product assembly order.
        names = [name for name in ("onebot", *MODULES) if name in config["modules"]]
        for name in names:
            spec = config["modules"][name]
            environment = module_environment(spec)
            self.environments[name] = environment
            module = module_factory(
                name,
                spec,
                environment,
                frontend=config.get("frontend"),
                gateway_origin=config["modules"]["gateway"]["origin"],
            )
            self.modules[name] = module

            async def build(ports, *, name=name, module=module):
                instance = None
                if name == "platform" and "onebot" in self.modules:
                    instance = await self.exchange.invoke(
                        "onebot", lambda app: app.state.lifecycle.instance_id
                    )
                app = await module(ports)
                if instance is not None:
                    app_platform(app).bot_adapters.bind_owned_adapter(
                        instance,
                        ports.session_factory("onebot"),
                        origin=self.config["modules"]["onebot"]["origin"].rstrip("/"),
                    )
                return app

            self.exchange.register_factory(
                name,
                build,
                "aiohttp" if name in {"platform", "gateway"} else "asgi",
                spec["origin"],
            )

    def publish(self, status=None):
        ready = bool(self.results) and all(
            value["health"]["ok"] for value in self.results.values()
        )
        write_state(
            self.config["state_file"],
            {
                "version": 1,
                "pid": os.getpid(),
                "updated_at": time.time(),
                "status": status
                or ("stopping" if self.stopping else "ready" if ready else "degraded"),
                "modules": self.results,
            },
        )

    async def invoke(self, name, operation, **kwargs):
        module = self.modules[name]
        return await self.exchange.invoke(
            name, lambda app: getattr(module, operation)(app, **kwargs)
        )

    async def open(self):
        self.publish("starting")
        # Platform still owns its existing process-environment credential resolver.
        # Set it once before creating any owner threads. Every other module receives
        # its independent explicit mapping; no request ever swaps process globals.
        self.previous_environment = dict(os.environ)
        os.environ.clear()
        os.environ.update(self.environments["platform"])
        # Partial startup owns real threads/resources too. A failed later module
        # must not skip shutdown or restore globals while earlier owners live.
        self.started = True
        try:
            await self.exchange.open(on_failure=self._startup_failed)
            # Expose no public listener until every module completed native startup.
            await asyncio.gather(
                *(self.invoke(name, "start_public") for name in self.modules)
            )
            await self.probe_once()
        except BaseException:
            if self.started:
                await self.close()
            self.publish("failed")
            raise
        return self

    async def probe_once(self, *, timeout=3):
        if self.stopping:
            return
        for name in self.modules:
            if name not in self.pending_probes:
                self.pending_probes[name] = asyncio.create_task(
                    self.invoke(name, "health")
                )
        pending = set(self.pending_probes.values())
        if pending:
            # Cancelling invoke can wait for a blocked owner. Retain one real
            # in-flight probe per module and keep this coordinator responsive.
            await asyncio.wait(pending, timeout=timeout)
        for name, task in tuple(self.pending_probes.items()):
            health = {"ok": False, "code": "probe_timeout"}
            if task.done():
                del self.pending_probes[name]
                try:
                    health = task.result()
                except Exception:  # noqa: BLE001 - probes publish only a closed safe verdict
                    health = {"ok": False, "code": "probe_failed"}
            self.results[name] = {
                "state": "ready" if health["ok"] else "degraded",
                "health": health,
            }
        self.publish()

    async def _phase(self, operation, **kwargs):
        results = await asyncio.gather(
            *(
                self.invoke(name, operation, **kwargs)
                for name in self.exchange.started_modules()
            ),
            return_exceptions=True,
        )
        return all(
            result is not False and not isinstance(result, BaseException)
            for result in results
        )

    async def _startup_failed(self):
        self.stopping = True
        self.publish("stopping")
        budget = self.config["shutdown_seconds"]
        await self._phase("stop_accepting")
        await self._phase("drain_ingress", timeout=budget)
        await self._phase("quiesce")
        await self._phase("drain", timeout=budget)
        await self._phase("close_public")

    async def _close(self):
        budget = self.config["shutdown_seconds"]
        clean = await self._phase("stop_accepting")
        clean = await self._phase("drain_ingress", timeout=budget) and clean
        # All producers stop together, while callback routes and dependencies
        # remain available. Closing domains one at a time would break A→B→A.
        clean = await self._phase("quiesce") and clean
        clean = await self._phase("drain", timeout=budget) and clean
        if self.pending_probes:
            await asyncio.gather(*self.pending_probes.values(), return_exceptions=True)
            self.pending_probes.clear()
        clean = await self._phase("close_public") and clean
        try:
            while not await self.exchange.close(grace=budget, cleanup_timeout=budget):
                self.publish("stopping")
                await asyncio.sleep(0.1)
        finally:
            # Exchange raises cleanup errors only after joining every owner.
            if self.previous_environment is not None:
                os.environ.clear()
                os.environ.update(self.previous_environment)
                self.previous_environment = None
        self.publish("stopped" if clean else "failed")
        return clean

    async def close(self, *, timeout=None):
        self.stopping = True
        self.publish("stopping")
        if self.close_task is None:
            self.close_task = asyncio.create_task(self._close())
        done, _ = await asyncio.wait(
            {self.close_task},
            timeout=self.config["shutdown_seconds"] if timeout is None else timeout,
        )
        return self.close_task.result() if done else False


async def run(config):
    host = BusinessHost(config)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    previous = {}
    for name in ("SIGTERM", "SIGINT"):
        number = getattr(signal, name)
        previous[number] = signal.getsignal(number)
        signal.signal(number, lambda *_: loop.call_soon_threadsafe(stop.set))
    try:
        await host.open()
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), config["probe_interval_seconds"])
            except TimeoutError:
                await host.probe_once()
    finally:
        try:
            if host.started:
                # A database call cannot be killed safely. Keep finalizers alive;
                # PID 1 owns the final process deadline and any forced-stop verdict.
                while not await host.close():
                    if host.close_task.done():
                        break
                    await asyncio.sleep(0.1)
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)
    return 0 if host.close_task is not None and host.close_task.result() else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run(load_settings(args.config)))
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 - process boundary must not disclose private configuration
        # Native diagnostics own details. Never dump configuration/secrets on PID 1.
        print('{"event":"business_host.failed"}', flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
