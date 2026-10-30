"""Compose native product applications on their owning module loops."""

import asyncio
import json
import re
import ssl
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp

from .edge import Ingress, Listener


def onebot_token(spec, environment):
    """Read the same configured SDK credential used by the OneBot listener."""
    if "token_file" in spec:
        return Path(spec["token_file"]).read_text(encoding="utf-8").strip()
    return environment.get(spec["token_env"])


def onebot_onboarding(spec, environment):
    """Read the owned adapter identity without exposing its key to the browser."""
    from tianshu_nonebot.storage import read_access_key

    settings = json.loads(Path(spec["settings"]).read_bytes())
    access_key = read_access_key(
        Path(spec["data_directory"]) / "adapter.sqlite3",
        settings.get("storage_backends", {}),
        environ=environment,
    )
    return {
        "access_key": access_key,
        "ws_port": urlsplit(spec["origin"]).port or 18765,
    }


class NativeModule:
    def __init__(self, name, spec, environment, *, frontend=None, gateway_origin=None):
        self.name, self.spec, self.environment = name, spec, environment
        self.frontend, self.gateway_origin = frontend, gateway_origin
        self.ports = self.app = self.listener = self.ingress = None
        self.api_listener = None
        self.probe_token = None
        self.onebot_access_token = None
        self.assembly = None
        self.diagnostics = None
        self.flow_reader = None
        # Aiohttp caches its default TLS context at import time. Construct the
        # Gateway's external trust context before Platform becomes the process
        # environment, preserving this module's own explicit CA configuration.
        self.network_tls = (
            ssl.create_default_context(
                cafile=environment.get("SSL_CERT_FILE"),
                capath=environment.get("SSL_CERT_DIR"),
            )
            if name == "gateway"
            else None
        )

    async def __call__(self, ports):
        self.ports = ports
        if self.name == "platform":
            self.app = self._platform()
        elif self.name == "gateway":
            self.app = self._gateway()
        elif self.name in {"memory", "knowledge"}:
            self.app = self._memory()
        elif self.name == "companion":
            from tianshu_companion.app import create_app
            from tianshu_companion.health import TOKEN_ENV

            self.app = create_app(
                config_path=self.spec["settings"],
                internal_clients=ports,
                environ=self.environment,
            )
            self.diagnostics = self.app.state.log
            self.probe_token = self.environment.get(TOKEN_ENV)
        elif self.name == "onebot":
            self.app = self._onebot()
        else:
            raise ValueError("Unknown native module")
        return self.app

    def _platform(self):
        from services.platform import diagnostics, diagnostics_config
        from services.platform.__main__ import SHUTDOWN_FLUSH_SECONDS, build_sink
        from services.platform.diagnostics_recovery import DiagnosticsRecovery
        from services.platform.server import create_app, create_console
        from services.platform.service import Platform

        settings = json.loads(Path(self.spec["settings"]).read_bytes())
        sink = build_sink(settings)
        self.diagnostics = sink
        diagnostics.activate(sink)
        diagnostics.event("runtime.starting", "INFO", "started")
        platform = None
        try:
            platform = Platform(settings, internal_clients=self.ports)
            self.console = create_console(platform)
            if "flow_query" in self.spec:
                from .flow_reader import FlowEventReader

                self.flow_reader = FlowEventReader.from_settings(
                    self.spec["flow_query"]
                )
                self.console.flow_graph.event_reader = self.flow_reader
            app = create_app(
                platform, console=self.console, trusted_local=self.ports.owns
            )
            self.ingress = Ingress(
                self.ports, self.gateway_origin, platform_origin=self.spec["origin"]
            )
            app.middlewares.insert(0, self.ingress.middleware)
        except BaseException:
            if self.flow_reader is not None:
                self.flow_reader.close()
            if platform is not None:
                platform.close()
            sink.close(SHUTDOWN_FLUSH_SECONDS)
            raise
        recovery = DiagnosticsRecovery(sink)

        async def recovery_context(app):
            recovery.start()
            diagnostics.event("runtime.started", "INFO", "succeeded")
            try:
                yield
            finally:
                await recovery.stop()
                diagnostics.event("runtime.stopping", "INFO", "started")

        async def close_log(app):
            # Native close_local_work has already drained and closed Platform.
            await self.ingress.close()
            if self.flow_reader is not None:
                await self.flow_reader.aclose()
            diagnostics.event("runtime.stopped", "INFO", "succeeded")
            sink.flush(SHUTDOWN_FLUSH_SECONDS)
            sink.close(SHUTDOWN_FLUSH_SECONDS)

        app.cleanup_ctx.append(recovery_context)
        app.on_cleanup.append(close_log)
        self.probe_token = self.environment.get(
            diagnostics_config.resolve_ready_token_env(settings)
        )
        return app

    def _gateway(self):
        from tianshu_gateway.observability import Observability
        from tianshu_gateway.server import create_app, load_settings

        settings = load_settings(Path(self.spec["settings"]).read_bytes())
        observation = None
        if settings.observability is not None:
            observation = Observability(
                settings.observability, environ=self.environment
            )
            self.probe_token = self.environment.get(
                settings.observability.probe_token_env
            )
        self.diagnostics = observation
        return create_app(
            settings,
            observation,
            internal_clients=self.ports,
            environ=self.environment,
            network_ssl=self.network_tls,
        )

    def _memory(self):
        from tianshu_memory import app as memory
        from tianshu_memory import knowledge_http
        from tianshu_memory.diagnostics import (
            CHAT_SERVICE,
            KNOWLEDGE_SERVICE,
            Diagnostics,
        )
        from tianshu_memory.server_runtime import (
            Binding,
            build_assembly,
            diagnostics_contract_path,
            normalize_authority,
            read_configuration,
        )

        spec = self.spec
        config_path, contract_path = spec["settings"], spec.get("contracts")
        raw = read_configuration(config_path)
        if "log_directory" in spec:
            raw["log_directory"] = spec["log_directory"]
        contract_path = diagnostics_contract_path(raw, config_path, contract_path)
        service = CHAT_SERVICE if self.name == "memory" else KNOWLEDGE_SERVICE
        diagnostics = Diagnostics(
            service,
            raw,
            config_path=config_path,
            contract_path=contract_path,
            environ=self.environment,
        )
        self.diagnostics = diagnostics
        parsed = urlsplit(spec["origin"])
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        binding = Binding(
            "127.0.0.1", port, {normalize_authority(parsed.hostname, port)}
        )
        # Factories create one independently owned HTTPX transport per client generation.
        clients = {
            name: (lambda name=name: self.ports.sync_transport(name))
            for name in ("platform", "companion", "memory", "knowledge", "gateway")
        }

        def build():
            options = {
                "internal_clients": clients,
                "storage_environ": self.environment,
                "diagnostics": diagnostics,
                "diagnostics_environ": self.environment,
            }
            if self.name == "memory":
                return memory.runtime_app(config_path, contract_path, **options)
            return knowledge_http.runtime_app(
                config_path,
                contract_path,
                client=spec["client"],
                port=port,
                authorities=binding.authority_names(),
                **options,
            )

        self.assembly = build_assembly(
            service,
            build,
            config_path=config_path,
            contract_path=contract_path,
            binding=binding,
            diagnostics=diagnostics,
            probe_settings=lambda assembly: assembly.app.state.probe_settings(assembly),
        )
        app = self.assembly.app
        previous = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(application):
            try:
                async with previous(application) as state:
                    await self.assembly.astarted()
                    try:
                        yield state
                    finally:
                        await self.assembly.astopping()
            finally:
                await self.assembly.astopped()

        app.router.lifespan_context = lifespan
        self.probe_token = self.environment.get(diagnostics.token_env)
        return app

    def _onebot(self):
        from tianshu_nonebot.onebot import create_host

        spec = self.spec
        raw = json.loads(Path(spec["settings"]).read_bytes())
        token = onebot_token(spec, self.environment)
        self.onebot_access_token = token
        port = urlsplit(spec["origin"]).port or 18765
        return create_host(
            host=spec.get("listen_host", "0.0.0.0"),
            port=port,
            data_dir=Path(spec["data_directory"]),
            access_token=token,
            storage_backends=raw.get("storage_backends", {}),
            storage_environ=self.environment,
        )

    async def start_public(self, app):
        if self.name == "platform":
            from services.platform.server import create_app

            access = self.console.access
            additional_tls = access.current is not None and self.frontend is not None
            if additional_tls and access.config["port"] == self.frontend["port"]:
                if access.tls is None:
                    raise ValueError("WebAccess and API listeners need distinct ports")
                # Older hosts also supplied a bootstrap frontend while native
                # HTTPS WebAccess owned that same port. Keep that one listener.
                additional_tls = False
            if access.current is not None:
                public = create_app(
                    app_platform(app), console=self.console, public=True
                )
                public.middlewares.insert(0, self.ingress.middleware)
                self.listener = Listener(
                    public,
                    host=access.config["host"],
                    port=access.config["port"],
                    tls=access.tls,
                    own_lifecycle=True,
                )
            else:
                if self.frontend is None:
                    raise ValueError("A bootstrap frontend is required")
                self.listener = self.tls_listener(app)
            await self.listener.start()
            if additional_tls:
                # The exchange already owns this private application's startup.
                # Keep the native HTTP console and a separate real TLS SDK edge.
                self.api_listener = self.tls_listener(app)
                await self.api_listener.start()
        elif self.name == "onebot":

            async def server_call(operation, *arguments):
                return await self.ports.router.invoke(
                    "platform",
                    lambda target: getattr(
                        app_platform(target).bot_adapters.onebot_servers, operation
                    )(*arguments),
                )

            self.listener = OneBotListener(
                app,
                urlsplit(self.spec["origin"]).port or 18765,
                host=self.spec.get("listen_host", "0.0.0.0"),
                token=self.onebot_access_token,
                authorize=lambda server_id, token, account: server_call(
                    "authorize", server_id, token, account
                ),
                reserve=lambda server_id, token, account: server_call(
                    "reserve", server_id, token, account
                ),
                claim=lambda server_id, token, account, reservation: server_call(
                    "claim", server_id, token, account, reservation
                ),
                release=lambda server_id, reservation: server_call(
                    "release", server_id, reservation
                ),
            )
            await self.listener.start()

    def onebot_server_status(self):
        return self.listener.connected_status() if self.listener else {}

    def tls_listener(self, app):
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(self.frontend["tls_certfile"], self.frontend["tls_keyfile"])
        return Listener(
            app,
            host=self.frontend["host"],
            port=self.frontend["port"],
            tls=tls,
        )

    async def stop_accepting(self, app):
        if self.ingress is not None:
            self.ingress.stopping = True
        results = await asyncio.gather(
            *(
                listener.stop_accepting()
                for listener in (self.listener, self.api_listener)
                if listener is not None
            ),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result

    async def drain_ingress(self, app, *, timeout):
        return (
            await self.ingress.drain(timeout=timeout)
            if self.ingress is not None
            else True
        )

    async def quiesce(self, app):
        if self.name == "platform":
            from services.platform.server import quiesce

            await quiesce(app)
        elif self.name in {"companion", "onebot"}:
            await app.state.lifecycle.quiesce()
        elif self.name in {"memory", "knowledge"}:
            operation = getattr(app.state, "quiesce", None)
            if operation is not None:
                await operation()

    async def drain(self, app, *, timeout):
        if self.name in {"companion", "onebot"}:
            return await app.state.lifecycle.drain(timeout=timeout)
        if self.name in {"memory", "knowledge"}:
            await app.state.drain()
        return True

    async def close_public(self, app):
        results = await asyncio.gather(
            *(
                listener.close()
                for listener in (self.api_listener, self.listener)
                if listener is not None
            ),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result

    async def health(self, app):
        path = "/health/live" if self.name == "onebot" else "/health/ready"
        headers = (
            {"Authorization": "Bearer " + self.probe_token} if self.probe_token else {}
        )
        factory = self.ports.session_factory(self.name)
        async with (
            factory(timeout=aiohttp.ClientTimeout(total=3)) as client,
            client.get(
                self.spec["origin"].rstrip("/") + path, headers=headers
            ) as response,
        ):
            # Read only the existing closed probe; never publish private failure details.
            await response.read()
            return {
                "ok": response.status == 200,
                "code": "http_" + str(response.status),
            }

    def diagnostic_instance(self, app):
        """Project the native sink identity on its owner loop, without exposing it."""
        value = getattr(self.diagnostics, "instance_id", None)
        return value if isinstance(value, str) else None

    def runtime_metrics(self, app):
        """Read the product's safe projection on its existing owner loop."""
        return app.state.runtime_metrics() if self.name == "companion" else None


def app_platform(app):
    from services.platform.server import PLATFORM

    return app[PLATFORM]


class OneBotListener:
    """External OneBot WebSocket; exchange alone owns the native ASGI lifespan."""

    def __init__(
        self,
        app,
        port,
        *,
        host="0.0.0.0",
        token=None,
        authorize=None,
        reserve=None,
        claim=None,
        release=None,
    ):
        self.app, self.port, self.host = app, port, host
        self.server = self.task = None
        self.stopping = False
        self.token, self.authorize = token, authorize
        self.reserve, self.claim, self.release = reserve, claim, release
        self.connected = {}

    def connected_status(self):
        return {server_id: value[0] for server_id, value in self.connected.items()}

    async def __call__(self, scope, receive, send):
        if self.stopping:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1013})
            elif scope["type"] == "http":
                await send(
                    {"type": "http.response.start", "status": 503, "headers": []}
                )
                await send({"type": "http.response.body", "body": b""})
            return
        prefix = "/onebot/v11/ws/"
        if scope["type"] != "websocket" or not scope.get("path", "").startswith(prefix):
            await self.app(scope, receive, send)
            return

        suffix = scope["path"].removeprefix(prefix)
        headers = scope.get("headers", [])
        auth = [value for key, value in headers if key.lower() == b"authorization"]
        accounts = [value for key, value in headers if key.lower() == b"x-self-id"]
        if (
            not re.fullmatch(r"[0-9a-f]{32}", suffix)
            or len(auth) != 1
            or len(accounts) != 1
            or self.authorize is None
            or self.reserve is None
            or self.claim is None
            or self.release is None
            or not self.token
        ):
            await send({"type": "websocket.close", "code": 1008})
            return
        try:
            scheme, _, token = auth[0].decode("ascii").partition(" ")
            account = accounts[0].decode("ascii")
            server_id = "onebot:" + suffix
            allowed = scheme.lower() == "bearer" and await self.authorize(
                server_id, token, account
            )
        except Exception:
            allowed = False
        if not allowed:
            await send({"type": "websocket.close", "code": 1008})
            return

        forwarded = dict(scope)
        forwarded["path"] = "/onebot/v11/ws"
        forwarded["raw_path"] = b"/onebot/v11/ws"
        forwarded["headers"] = [
            (key, value) for key, value in headers if key.lower() != b"authorization"
        ] + [(b"authorization", b"Bearer " + self.token.encode("ascii"))]
        marker = object()
        accepted = False

        async def forward(message):
            nonlocal accepted
            if message["type"] == "websocket.accept":
                try:
                    reservation = await self.reserve(server_id, token, account)
                except Exception:
                    reservation = None
                if not reservation:
                    await send({"type": "websocket.close", "code": 1008})
                    raise ConnectionAbortedError("OneBot server identity changed")
                try:
                    await send(message)
                    try:
                        claimed = await self.claim(
                            server_id, token, account, reservation
                        )
                    except Exception:
                        claimed = False
                    if not claimed:
                        await send({"type": "websocket.close", "code": 1008})
                        raise ConnectionAbortedError("OneBot server identity changed")
                finally:
                    try:
                        await self.release(server_id, reservation)
                    except Exception:
                        # A lost release expires automatically in the owner.
                        pass
                self.connected[server_id] = (account, marker)
                accepted = True
            else:
                await send(message)

        try:
            await self.app(forwarded, receive, forward)
        except ConnectionAbortedError:
            pass
        finally:
            if accepted and self.connected.get(server_id) == (account, marker):
                self.connected.pop(server_id, None)

    async def start(self):
        import uvicorn

        self.server = uvicorn.Server(
            uvicorn.Config(
                self,
                host=self.host,
                port=self.port,
                lifespan="off",
                access_log=False,
                log_level="warning",
                timeout_graceful_shutdown=30,
            )
        )
        self.task = asyncio.create_task(self.server.serve())
        while not self.server.started:
            if self.task.done():
                await self.task
                raise RuntimeError("OneBot listener did not start")
            await asyncio.sleep(0.01)

    async def stop_accepting(self):
        self.stopping = True
        for server in getattr(self.server, "servers", ()):
            server.close()
            await server.wait_closed()

    async def close(self):
        if self.task is not None:
            self.server.should_exit = True
            await self.task
