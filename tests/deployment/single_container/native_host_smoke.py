"""Run installed product wheels through the real native host using synthetic inputs.

This is an opt-in integration tool, not part of the ordinary unit-test discovery.
It retains the generated configuration, isolated databases and summary in --output.
No model, QQ account or remote service is contacted.
"""

import argparse
import asyncio
from dataclasses import asdict
import hashlib
import importlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import ssl
import sys
import threading


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-source", type=Path, required=True)
    parser.add_argument("--platform-fixtures", type=Path, required=True)
    parser.add_argument(
        "--contracts", type=Path, required=True, help="text-dialogue/v1"
    )
    parser.add_argument("--memory-contracts", type=Path, required=True)
    parser.add_argument("--diagnostics-contracts", type=Path, required=True)
    parser.add_argument(
        "--memory-fixture", type=Path, help="Optional complete TS336 fixture"
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="New isolated directory"
    )
    return parser.parse_args()


def write(path, document):
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return str(path)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def installed_wheels():
    modules = (
        "services.platform.service",
        "tianshu_companion.app",
        "tianshu_memory.app",
        "tianshu_gateway.server",
        "tianshu_nonebot.onebot",
    )
    for name in modules:
        location = Path(importlib.import_module(name).__file__).resolve()
        assert location.is_relative_to(Path(sys.prefix).resolve()), (name, location)


def copy_memory_fixture(source, destination):
    """Rebase configuration paths, preserving every frozen contract byte."""
    source = source.resolve()
    original_databases = {
        str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source.rglob("*.sqlite")
    }
    shutil.copytree(source, destination)

    def rebase(value):
        if isinstance(value, str):
            return value.replace(str(source), str(destination)).replace(
                source.as_posix(), destination.as_posix()
            )
        if isinstance(value, dict):
            return {key: rebase(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rebase(item) for item in value]
        return value

    for path in destination.rglob("*.json"):
        if path.is_relative_to(destination / "contracts"):
            assert (
                path.read_bytes()
                == (source / path.relative_to(destination)).read_bytes()
            )
        else:
            write(path, rebase(json.loads(path.read_bytes())))
    specs = json.loads((destination / "native-modules.json").read_bytes())
    environments = json.loads((destination / "environments.json").read_bytes())
    for name, spec in specs.items():
        spec["env"] = environments[name]
    return specs, original_databases


async def upstream_fixture(model):
    """Only the provider is a substitute; Platform publishes the real snapshot."""
    from aiohttp import web

    release, first = asyncio.Event(), asyncio.Event()
    calls = []
    response = {
        "id": "chatcmpl-synthetic",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Synthetic"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    prefix = b'data: {"choices":[{"index":0,"delta":{"content":"Synthetic"},"finish_reason":null}]}\n\n'
    suffix = b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}\n\ndata: [DONE]\n\n'

    async def serve(request):
        body = await request.json()
        calls.append((body, request.headers.get("Authorization")))
        if not body.get("stream"):
            return web.json_response(response)
        result = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await result.prepare(request)
        await result.write(prefix)
        first.set()
        await release.wait()
        await result.write(suffix)
        return result

    app = web.Application()
    app.router.add_post("/v1/chat/completions", serve)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    origin = "http://127.0.0.1:" + str(runner.addresses[0][1]) + "/v1"
    return runner, origin, release, first, calls, response, prefix + suffix


def prepare(options, upstream):
    from fixtures import DOCUMENTS, ENV
    from web_fixtures import web_settings
    from tianshu_gateway.config import ClientGrant
    from tianshu_gateway.server import Settings
    from tests.deployment.observability.helpers import certificates

    root = options.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    tls = root / "tls"
    certificates(tls)
    public_port, onebot_port, knowledge_port = free_port(), free_port(), free_port()
    origins = {
        name: "https://" + name + ".internal"
        for name in ("platform", "gateway", "memory", "companion")
    }
    origins.update(
        knowledge="http://knowledge.internal:" + str(knowledge_port),
        onebot="http://onebot.internal:" + str(onebot_port),
    )
    tokens = {
        name: secrets.token_urlsafe(32)
        for name in ("core", "client", "upstream", "origin", "sdk", "memory")
    }
    platform = web_settings(str(root), origin="https://127.0.0.1:" + str(public_port))
    platform["mode"] = "service_https"
    platform["core"] = {
        "base_url": origins["companion"],
        "token_env": "SYNTHETIC_CORE_TOKEN",
    }
    provider = DOCUMENTS["config"]["providers"][0]
    platform["providers"][provider["provider_id"]].update(
        base_url=upstream, reviewed_addresses=["127.0.0.1"], allow_private_http=True
    )
    account = {"namespace": "qq", "immutable_account_id": "10001"}
    memory = {
        "mode": "local_fixture",
        "database_path": str(root / "memory.sqlite"),
        "contract_directory": str(options.memory_contracts.resolve()),
        "callers": {
            "companion": {
                "token": tokens["memory"],
                "issuer": "nonebot",
                "allowed_actors": ["actor-fixture"],
                "operations": ["register"],
                "event_scopes": [],
            }
        },
        "origins": {
            "synthetic-origin": {
                "issuer": "nonebot",
                "authenticated_service": "companion",
                "audience_service": "memory",
                "assertion_ref": "synthetic-origin",
                "verified_account": account,
                "principal_id": None,
                "allowed_scope": {
                    "actor_id": "actor-fixture",
                    "person_id": None,
                    "audience": "self_private",
                    "conversation_id": None,
                },
                "expires_at": "2030-01-01T00:00:00Z",
                "revoked": False,
                "verified_channel": {
                    "namespace": "qq",
                    "binding_id": "synthetic-binding",
                    "channel_conversation_id": "synthetic-private",
                    "thread_id": None,
                },
            }
        },
    }
    project = root / "project"
    project.mkdir()
    configurations = {
        "platform": platform,
        "companion": {
            "database_path": str(root / "companion.sqlite"),
            "contracts_path": str(options.contracts.resolve()),
            "bindings": {},
            "roles": {},
            "services": {},
        },
        "memory": memory,
        "knowledge": {
            "database_path": str(root / "knowledge.sqlite"),
            "knowledge": {
                "projects": {
                    "fixture": {
                        "root": str(project),
                        "host": "local",
                        "default_branch": "main",
                        "urls": [],
                    }
                },
                "clients": {
                    "fixture": {
                        "credential_sha256": hashlib.sha256(
                            secrets.token_bytes(32)
                        ).hexdigest(),
                        "projects": ["fixture"],
                        "permissions": ["status"],
                    }
                },
            },
        },
        "gateway": asdict(
            Settings(
                str(options.contracts.resolve()),
                str(root / "gateway.sqlite"),
                origins["platform"],
                "secret-ref:fixture/platform",
                "SYNTHETIC_ORIGIN",
                {
                    "secret-ref:fixture/platform": "SYNTHETIC_PLATFORM",
                    "secret-ref:fixture/client": "SYNTHETIC_CLIENT",
                    provider["credential_ref"]: "SYNTHETIC_UPSTREAM",
                },
                [
                    {
                        "base_url": origins["platform"],
                        "addresses": ["127.0.0.1"],
                        "allow_private_http": False,
                    },
                    {
                        "base_url": upstream,
                        "addresses": ["127.0.0.1"],
                        "allow_private_http": True,
                    },
                ],
                [
                    ClientGrant(
                        "companion",
                        "secret-ref:fixture/client",
                        provider["provider_id"],
                        7,
                        True,
                    )
                ],
            )
        ),
        "onebot": {},
    }
    environments = {}
    for name in configurations:
        log = root / ("logs-" + name)
        log.mkdir()
        environments[name] = {
            "TIANSHU_LOG_DIR": str(log),
            "TIANSHU_DIAGNOSTICS_TOKEN": secrets.token_urlsafe(32),
            "TIANSHU_DIAGNOSTICS_CONTRACT_DIR": str(
                options.diagnostics_contracts.resolve()
            ),
        }
    environments["platform"].update(ENV, SYNTHETIC_CORE_TOKEN=tokens["core"])
    environments["gateway"].update(
        SYNTHETIC_PLATFORM=ENV["TS012_GATEWAY"],
        SYNTHETIC_CLIENT=tokens["client"],
        SYNTHETIC_UPSTREAM=tokens["upstream"],
        SYNTHETIC_ORIGIN=tokens["origin"],
    )
    environments["onebot"]["SYNTHETIC_ONEBOT_TOKEN"] = tokens["sdk"]
    specs = {
        name: {
            "settings": write(root / (name + ".json"), value),
            "origin": origins[name],
            "env": environments[name],
        }
        for name, value in configurations.items()
    }
    for name in ("memory", "knowledge"):
        specs[name].update(
            contracts=str(options.diagnostics_contracts.resolve()),
            log_directory=environments[name]["TIANSHU_LOG_DIR"],
        )
    specs["knowledge"]["client"] = "fixture"
    specs["onebot"].update(
        data_directory=str(root / "onebot"),
        token_env="SYNTHETIC_ONEBOT_TOKEN",
        listen_host="127.0.0.1",
    )
    source_databases = None
    if options.memory_fixture:
        copied, source_databases = copy_memory_fixture(
            options.memory_fixture, root / "complete-memory"
        )
        specs.update(copied)
    config = {
        "version": 1,
        "state_file": str(root / "host-state.json"),
        "shutdown_seconds": 20,
        "probe_interval_seconds": 1,
        "modules": specs,
        "frontend": {
            "host": "127.0.0.1",
            "port": public_port,
            "tls_certfile": str(tls / "guard.pem"),
            "tls_keyfile": str(tls / "guard.key"),
        },
    }
    path = write(root / "host.json", config)
    return path, tokens, account, source_databases


async def model_roundtrip(host, public, root, token, provider_token, upstream):
    from fixtures import DOCUMENTS, bearer, config
    from deploy.single_container.applications import app_platform
    import httpx

    def publish(app):
        platform = app_platform(app)
        result = platform.models.publish(bearer("ADMIN"), config(upstream=upstream[1]))
        assert result["published"]
        return platform.origins.issue(bearer("ADMIN"), "config-entry")["assertion_ref"]

    origin = await host.exchange.invoke("platform", publish)
    await host.exchange.invoke(
        "gateway",
        lambda app: host.modules["gateway"].environment.update(SYNTHETIC_ORIGIN=origin),
    )
    headers = {
        "Authorization": "Bearer " + token,
        "X-Tianshu-Config-Version": "7",
        "X-Tianshu-Workload": "companion.text",
    }
    public_origin = json.loads((root / "platform.json").read_bytes())["web"]["origin"]
    for streaming in (False, True):
        identity = "synthetic-stream" if streaming else "synthetic-json"
        body = {**DOCUMENTS["native_request"], "stream": streaming}
        request_headers = {
            **headers,
            "X-Request-ID": identity,
            "X-Tianshu-Turn-ID": identity,
        }
        if streaming:
            async with public.stream(
                "POST",
                public_origin + "/v1/chat/completions",
                json=body,
                headers=request_headers,
            ) as response:
                assert response.status_code == 200, await response.aread()
                async with asyncio.timeout(5):
                    iterator = response.aiter_raw()
                    first = await anext(iterator)
                assert first and upstream[3].is_set() and not upstream[2].is_set()
                upstream[2].set()
                received = first + b"".join([chunk async for chunk in iterator])
                assert received == upstream[6], received
        else:
            response = await public.post(
                public_origin + "/v1/chat/completions",
                json=body,
                headers=request_headers,
            )
            assert response.status_code == 200, response.text
            assert response.json() == upstream[5]
        async with httpx.AsyncClient(
            transport=host.exchange.async_transport("gateway"),
            base_url=host.modules["gateway"].spec["origin"],
        ) as client:
            receipt = await client.get(
                "/internal/v1/model-requests/" + identity,
                headers={"Authorization": "Bearer " + token},
            )
            assert receipt.status_code == 200, receipt.text
            assert receipt.json()["outcome"] == "succeeded", receipt.text
    assert len(upstream[4]) == 2, upstream[4]
    assert all(
        authorization == "Bearer " + provider_token for _, authorization in upstream[4]
    )
    print(
        "real Platform snapshot -> Gateway -> local provider: JSON/first-stream-block/terminal receipts passed",
        flush=True,
    )


async def run(options):
    import httpx
    from websockets.asyncio.client import connect
    from deploy.single_container.host import BusinessHost
    from deploy.single_container.host_settings import load_settings
    from deploy.single_container.applications import app_platform
    from nonebot.internal.driver._lifespan import Lifespan
    from fixtures import DOCUMENTS
    from tianshu_memory.knowledge_http import ACTION_PATH

    installed_wheels()
    upstream = await upstream_fixture(DOCUMENTS["config"]["providers"][0]["model_id"])
    path, tokens, account, source_databases = prepare(options, upstream[1])
    settings = load_settings(path)
    host = BusinessHost(settings)
    lifecycle = []
    original_start, original_stop = Lifespan.startup, Lifespan.shutdown

    async def started(self):
        lifecycle.append(("start", threading.get_ident()))
        return await original_start(self)

    async def stopped(self, **kwargs):
        lifecycle.append(("stop", threading.get_ident()))
        return await original_stop(self, **kwargs)

    Lifespan.startup, Lifespan.shutdown = started, stopped
    peer = None
    summary = {}
    try:
        await host.open()
        owners = {
            name: await host.exchange.invoke(name, lambda app: threading.get_ident())
            for name in settings["modules"]
        }
        assert (
            len(set(owners.values())) == 6
            and threading.get_ident() not in owners.values()
        ), owners
        summary.update(owners=owners, probes=host.results)
        print(
            "six actual owner threads",
            owners,
            "native probes",
            host.results,
            flush=True,
        )
        for name, spec in settings["modules"].items():
            async with httpx.AsyncClient(
                transport=host.exchange.async_transport(name), base_url=spec["origin"]
            ) as client:
                live = await client.get("/health/live")
                assert live.status_code == 200, (name, live.text)
                if options.memory_fixture and name in {"memory", "knowledge"}:
                    ready = await client.get(
                        "/health/ready",
                        headers={
                            "Authorization": "Bearer "
                            + spec["env"]["TIANSHU_DIAGNOSTICS_TOKEN"]
                        },
                    )
                    assert ready.status_code == 200, (name, ready.text)
                    summary[name + "_readiness"] = ready.json()
                    print(name, "complete Assembly ready200", ready.text, flush=True)
        if not options.memory_fixture:
            async with httpx.AsyncClient(
                transport=host.exchange.async_transport("memory"),
                base_url=settings["modules"]["memory"]["origin"],
            ) as client:
                body = {
                    "command": {
                        "schema_version": 1,
                        "request_id": "native-register",
                        "origin": {"assertion_ref": "synthetic-origin"},
                        "idempotency_key": "native-register",
                        "deadline_at": "2030-01-01T00:00:00Z",
                    },
                    "account": account,
                }
                for token, status in ((tokens["memory"], 200), ("wrong", 401)):
                    response = await client.post(
                        "/internal/v1/identity/register",
                        json=body,
                        headers={"Authorization": "Bearer " + token},
                    )
                    assert response.status_code == status, response.text
        async with httpx.AsyncClient(
            transport=host.exchange.async_transport("knowledge"),
            base_url=settings["modules"]["knowledge"]["origin"],
        ) as client:
            response = await client.post(ACTION_PATH, json={})
            assert response.status_code == 401, response.text
        root = options.output.resolve()
        origin = json.loads((root / "platform.json").read_bytes())["web"]["origin"]
        async with httpx.AsyncClient(
            verify=ssl.create_default_context(cafile=root / "tls/ca.pem"),
            trust_env=False,
        ) as public:
            response = await public.post(origin + "/v1/chat/completions", json={})
            assert response.status_code == 401, response.text
            page = await public.get(origin + "/")
            assert (
                page.status_code == 200 and "Synthetic local web fixture" in page.text
            )
            await model_roundtrip(
                host, public, root, tokens["client"], tokens["upstream"], upstream
            )
        port = settings["modules"]["onebot"]["origin"].rsplit(":", 1)[1]
        peer = await connect(
            "ws://127.0.0.1:" + port + "/onebot/v11/ws",
            additional_headers={
                "Authorization": "Bearer " + tokens["sdk"],
                "X-Self-ID": "10001",
            },
        )

        async def respond():
            async for raw in peer:
                message = json.loads(raw)
                assert message["action"] == "get_login_info", message
                await peer.send(
                    json.dumps(
                        {
                            "status": "ok",
                            "retcode": 0,
                            "data": {"user_id": 10001, "nickname": "Synthetic SDK"},
                            "echo": message["echo"],
                        }
                    )
                )

        responder = asyncio.create_task(respond())
        instance = await host.exchange.invoke(
            "onebot", lambda app: app.state.lifecycle.instance_id
        )

        def access_key(app):
            from tianshu_nonebot.storage import read_access_key

            return read_access_key(root / "onebot/adapter.sqlite3", environ={})

        key = await host.exchange.invoke("onebot", access_key)

        async def owned_rpc(app):
            adapters = app_platform(app).bot_adapters
            assert instance in adapters.owned_adapter_instances
            return await adapters._plugin(
                {
                    "id": "synthetic-owned-connection",
                    "instance_id": instance,
                    "address": settings["modules"]["onebot"]["origin"],
                    "access_key": key,
                    "pins": None,
                    "ca_pem": None,
                },
                "/capabilities",
                {},
            )

        async with asyncio.timeout(5):
            while True:
                caps = await host.exchange.invoke("platform", owned_rpc)
                if caps["accounts"]:
                    break
                await asyncio.sleep(0.01)
        assert (
            caps["instance_id"] == instance
            and caps["adapter"] == "onebot"
            and caps["accounts"][0]["id"] == "10001"
        ), caps
        assert (
            await host.exchange.invoke(
                "onebot",
                lambda app: host.modules["onebot"].listener.server.config.lifespan,
            )
            == "off"
        )
        assert (
            await host.exchange.invoke(
                "onebot", lambda app: host.modules["onebot"].listener.server.config.host
            )
            == "127.0.0.1"
        )
        assert lifecycle == [("start", owners["onebot"])], lifecycle
        await peer.close()
        await responder
        peer = None
        summary.update(
            public_tls_unauthorized=401,
            public_web=200,
            knowledge_unauthorized=401,
            owned_onebot_rpc=True,
            synthetic_websocket=True,
            real_platform_snapshot=True,
            json_and_stream_receipts=True,
        )
    finally:
        try:
            if peer is not None:
                await peer.close()
            if host.started:
                assert await host.close(timeout=30), host.results
        finally:
            Lifespan.startup, Lifespan.shutdown = original_start, original_stop
            upstream[2].set()
            await upstream[0].cleanup()
    assert lifecycle == [("start", owners["onebot"]), ("stop", owners["onebot"])], (
        lifecycle
    )
    if source_databases is not None:
        for relative, digest in source_databases.items():
            assert (
                hashlib.sha256(
                    (options.memory_fixture / relative).read_bytes()
                ).hexdigest()
                == digest
            )
    summary.update(
        clean_shutdown=True, single_onebot_lifespan=True, no_remote_model_or_qq=True
    )
    write(options.output / "validation.json", summary)
    print(
        "actual native BusinessHost validation passed; artifacts:",
        options.output.resolve(),
        flush=True,
    )


def main():
    options = arguments()
    sys.path[:0] = [
        str(options.host_source.resolve()),
        str(options.platform_fixtures.resolve()),
    ]
    os.environ["TS012_CONTRACT_DIR"] = str(options.contracts.resolve())
    asyncio.run(run(options))


if __name__ == "__main__":
    main()
