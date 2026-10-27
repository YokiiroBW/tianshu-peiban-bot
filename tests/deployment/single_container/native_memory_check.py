"""Exercise installed P/C/M applications through the real six-owner native host.

This opt-in acceptance script uses only synthetic, copied state. It observes the
host's real byte exchange; it does not install ASGI peer substitutes, replace
product clients, approve memories, or overlay product source. The provider made
by the existing smoke helper must receive zero requests in this check.
"""

import argparse
import asyncio
import copy
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time
from urllib.parse import urlsplit


sys.dont_write_bytecode = True


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-source", type=Path, default=Path("/opt/tianshu/application"))
    parser.add_argument("--platform-fixtures", type=Path, default=Path("/validation/platform/tests/backend"))
    parser.add_argument("--contracts", type=Path, default=Path("/verification/contracts/text-dialogue/v1"))
    parser.add_argument("--memory-contracts", type=Path, default=Path("/verification/memory-fixture/contracts/text-dialogue/v1"))
    parser.add_argument("--diagnostics-contracts", type=Path, default=Path("/verification/contracts/diagnostics/v1"))
    parser.add_argument("--memory-fixture", type=Path, default=Path("/verification/memory-fixture"))
    parser.add_argument("--output", type=Path, required=True, help="New isolated directory, retained for inspection")
    options = parser.parse_args()
    options.web_access = options.http_web_access = False
    return options


def require(condition, label):
    if not condition:
        raise AssertionError(label)


def read(path):
    return json.loads(Path(path).read_bytes())


def load_smoke(options):
    # These are host implementation and fixture helpers, never product sources.
    sys.path.insert(0, str(options.host_source.resolve()))
    sys.path.insert(1, str(options.platform_fixtures.resolve()))
    os.environ["TS012_CONTRACT_DIR"] = str(options.contracts.resolve())
    helper = options.host_source / "tests/deployment/single_container/native_host_smoke.py"
    spec = importlib.util.spec_from_file_location("native_memory_smoke_helper", helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Recorder:
    """Record incoming native exchanges without changing transport or bodies."""

    def __init__(self):
        self.lock = threading.Lock()
        self.phase = "startup"
        self.rows = []

    def stage(self, name):
        with self.lock:
            self.phase = name
        print(json.dumps({"stage": name}, ensure_ascii=False), flush=True)

    def attach(self, host):
        for name, owner in host.exchange._owners.items():
            original = owner.local.request

            async def observed(target, method, url, headers, body, *, original=original, name=name):
                def decode(value):
                    return value.decode("latin1") if isinstance(value, bytes) else str(value)

                correlation = next(
                    (decode(value) for key, value in headers if decode(key).lower() == "x-tianshu-correlation-id"),
                    None,
                )
                with self.lock:
                    row = {
                        "stage": self.phase,
                        "owner": name,
                        "thread": threading.get_ident(),
                        "method": method,
                        "path": urlsplit(str(url)).path,
                        "correlation_id": correlation,
                    }
                    self.rows.append(row)
                start = time.monotonic()
                try:
                    response = await original(target, method, url, headers, body)
                    row["status"] = response.status
                    return response
                except BaseException as error:
                    row["error_type"] = type(error).__name__
                    raise
                finally:
                    row["headers_ms"] = round((time.monotonic() - start) * 1000, 3)

            owner.local.request = observed

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.rows)


def configure(options, smoke, host_path, tokens):
    from fixtures import ENV
    from source_fixtures import SOURCE_DOCS, source_settings

    bindings = read(options.memory_fixture / "synthetic-peer-bindings.json")
    require(bindings["synthetic_only"] is True, "fixture must explicitly be synthetic")
    config = read(host_path)
    specs = config["modules"]
    for name, origin in bindings["origins"].items():
        specs[name]["origin"] = origin

    platform_path = Path(specs["platform"]["settings"])
    platform = read(platform_path)
    sources = source_settings(str(options.output.resolve()), audience="self_private")
    # The smoke helper already registered the console's operator-owned input.
    # Keep that entire registration, including operator-owned actor entries,
    # under distinct IDs before installing the connector-owned source helper.
    web_inputs = []
    web_actor_ids = {}
    for old_id in platform["web"]["input_entries"]:
        new_id = "native-memory-web-" + old_id
        require(new_id not in platform["input_entries"], "distinct console input fixture ID")
        entry = platform["input_entries"].pop(old_id)
        actor_entries = []
        for old_actor_id in entry["actor_entries"]:
            if old_actor_id not in web_actor_ids:
                new_actor_id = "native-memory-web-" + old_actor_id
                require(new_actor_id not in platform["entries"], "distinct console actor fixture ID")
                platform["entries"][new_actor_id] = copy.deepcopy(platform["entries"][old_actor_id])
                web_actor_ids[old_actor_id] = new_actor_id
            actor_entries.append(web_actor_ids[old_actor_id])
        entry["actor_entries"] = actor_entries
        platform["input_entries"][new_id] = entry
        web_inputs.append(new_id)
    platform["web"]["input_entries"] = web_inputs
    platform["input_entries"].update(sources["input_entries"])
    platform["entries"].update({key: sources["entries"][key] for key in ("actor-a", "actor-b")})
    for name in ("connector", "companion", "memory"):
        prior_actions = platform["principals"][name]["actions"]
        platform["principals"][name] = copy.deepcopy(sources["principals"][name])
        platform["principals"][name]["actions"] = list(dict.fromkeys(prior_actions + platform["principals"][name]["actions"]))
    # The same registered Companion principal resolves the Platform-issued actor
    # assertion and requests input access. Memory uses its separate resolver.
    platform["principals"]["companion"]["actions"].append("origin.resolve")
    platform["principals"]["companion"]["resolver"] = {"caller": "platform", "purpose": "dialogue"}
    platform["core"]["base_url"] = bindings["origins"]["companion"]
    specs["platform"]["env"].update(
        TS012_MEMORY=bindings["tokens"]["source_access"],
        TS012_MEMORY_RESOLVER=bindings["tokens"]["origin_resolver"],
    )
    smoke.write(platform_path, platform)

    data = copy.deepcopy(SOURCE_DOCS["self_private/request"]["input"])
    channel = data["message_key"]["channel"]
    require(channel["namespace"] == "web", "published synthetic source must use web, not a real QQ account")
    companion_path = Path(specs["companion"]["settings"])
    companion = read(companion_path)
    companion.update(
        bindings={channel["binding_id"]: {
            "service": "platform", "namespace": channel["namespace"],
            "audience": "self_private", "actor_ids": ["actor:a", "actor:b"],
            "classification": {"value": "real", "basis": "registered_input_mode", "policy_ref": "fixture:real", "policy_version": 1},
        }},
        roles={"actor:a": {"version": 1, "persona": "Synthetic native role A"}},
        config_version=7,
        policy={"silence_ms": 2000},
        automatic_memory_candidates=True,
        callers={
            "platform": {"token_env": "SYNTHETIC_CORE_TOKEN", "issuer": "platform", "origin_service": "native_platform_origin"},
            "memory": {"token_env": "NATIVE_MEMORY_SOURCE_FACTS"},
        },
        services={
            "native_platform_origin": {"url": bindings["origins"]["platform"], "token_env": "TS012_COMPANION"},
            "memory": {"url": bindings["origins"]["memory"], "token_env": "NATIVE_MEMORY_COMPANION"},
        },
    )
    specs["companion"]["env"].update(
        SYNTHETIC_CORE_TOKEN=tokens["core"],
        TS012_COMPANION=ENV["TS012_COMPANION"],
        NATIVE_MEMORY_SOURCE_FACTS=bindings["tokens"]["source_facts"],
        NATIVE_MEMORY_COMPANION=bindings["tokens"]["memory_companion"],
    )
    smoke.write(companion_path, companion)
    # Gateway is present as the real sixth owner, but this acceptance check does
    # not generate: keep its authority origin aligned with Platform's origin.
    gateway_path = Path(specs["gateway"]["settings"])
    gateway = read(gateway_path)
    # Settings spelling follows the installed native smoke helper, not a guessed
    # replacement schema; recursively replace only its old Platform origin.
    def rebase(value):
        if isinstance(value, str):
            return value.replace("https://platform.internal", bindings["origins"]["platform"])
        if isinstance(value, list):
            return [rebase(item) for item in value]
        if isinstance(value, dict):
            return {key: rebase(item) for key, item in value.items()}
        return value
    smoke.write(gateway_path, rebase(gateway))
    smoke.write(Path(host_path), config)
    return bindings, data


async def json_call(host, owner, target, path, token, payload):
    import httpx

    async def operation(app):
        async with httpx.AsyncClient(
            transport=host.modules[owner].ports.async_transport(target),
            base_url=host.config["modules"][target]["origin"], trust_env=False,
        ) as client:
            response = await client.post(path, json=payload, headers={"Authorization": "Bearer " + token})
            return {"status": response.status_code, "body": response.json()}
    return await host.exchange.invoke(owner, operation)


async def role_grant(host, bindings, expected, enabled, request_id, *, token=None):
    result = await json_call(
        host, "platform", "memory", "/internal/v1/role-runtime/authorize",
        token or bindings["tokens"]["platform_memory_admin"],
        {"request_id": request_id, "actor_id": "actor:a", "expected_version": expected, "enabled": enabled, "legacy": True},
    )
    if token is None:
        require(result == {"status": 200, "body": {"actor_id": "actor:a", "version": expected + 1, "enabled": enabled}}, "native role grant result")
    return result


async def query(host, admission, correlation):
    async def operation(app):
        from tianshu_companion import observability as obs
        with obs.correlation_scope(correlation):
            return await app.state.core.memory.query_context(
                admission["accepted_origin"], admission["scope"], "乌龙茶 synthetic native memory",
                {"tokens": 8192, "bytes": 8192}, retrieval_mode="factual",
            )
    return await host.exchange.invoke("companion", operation)


async def expect_forbidden(operation, label):
    from tianshu_companion.contracts import Fault
    try:
        await operation()
    except Fault as error:
        result = {"code": error.code, "http_status": (error.cause or {}).get("http_status"), "unknown": error.unknown}
        require(result == {"code": "forbidden", "http_status": 403, "unknown": False}, label)
        return result
    raise AssertionError(label + ": accepted")


async def execute(options):
    smoke = load_smoke(options)
    smoke.installed_wheels()
    locations = {name: str(Path(importlib.import_module(name).__file__).resolve()) for name in (
        "services.platform.service", "tianshu_companion.app", "tianshu_memory.app", "tianshu_gateway.server", "tianshu_nonebot.onebot",
    )}
    upstream = await smoke.upstream_fixture("synthetic-unused-native-memory")
    runner, upstream_origin, release, _, provider_calls, _, _ = upstream
    host = None
    recorder = Recorder()
    summary = {"ok": False, "installed_modules": locations, "checks": {}, "limitations": [
        "The internal native host uses bounded HTTP bytes between six real application owners; internal TCP/TLS is not claimed.",
        "The synthetic web input follows real registration, identity, fanout, mapping, outbox and source authority. It is not a real QQ conversation.",
        "Generation is deliberately unconfigured; raw material capture is required, but model output, confirmed learned facts, relationship writeback, schedules, life/diary, media and production PostgreSQL are not claimed by this script.",
        "No product source overlay or peer ASGI substitute is installed. Route observation wraps the original native dispatcher without changing response/body/authentication.",
    ]}
    original_databases = None
    try:
        host_path, tokens, _, original_databases = smoke.prepare(options, upstream_origin)
        bindings, data = configure(options, smoke, host_path, tokens)
        from deploy.single_container.host import BusinessHost, load_settings
        from deploy.single_container.applications import app_platform
        from fixtures import bearer
        from source_fixtures import current_request, register
        from services.platform.contracts import utc

        host = BusinessHost(load_settings(host_path))
        recorder.attach(host)
        await host.open()
        owners = {}
        for name in host.modules:
            owners[name] = await host.exchange.invoke(name, lambda app: threading.get_ident())
        require(len(owners) == 6 and len(set(owners.values())) == 6 and threading.get_ident() not in owners.values(), "six distinct native owner threads")
        summary["owners"] = owners

        recorder.stage("role-admission")
        rejected = await role_grant(host, bindings, 0, True, "native-memory:wrong-role", token=bindings["tokens"]["memory_companion"])
        require(rejected == {"status": 403, "body": {"code": "forbidden"}}, "Companion cannot act as Memory role administrator")
        admitted = await role_grant(host, bindings, 0, True, "native-memory:adopt-static")
        summary["checks"]["role_admission"] = {"wrong_principal": rejected, "registered_admin": admitted}

        recorder.stage("platform-dispatch-companion-identity")
        marker = "我喜欢乌龙茶，synthetic native memory callback 唯一验证材料。"
        data["parts"] = [{"kind": "text", "text": marker}]
        data["sent_at"] = utc(time.time())

        async def dispatch(app):
            platform = app_platform(app)
            ingest = await platform.store.run_local(register, platform, time.time(), "self_private", ["actor:a"], data)
            return await platform.sources.dispatch(bearer("CONNECTOR"), ingest)
        accepted = await host.exchange.invoke("platform", dispatch)
        require(accepted["effective_actor_ids"] == ["actor:a"], "one intended actor admitted")
        outcomes = accepted["outcomes"]
        require(len(outcomes) == 1 and outcomes[0]["admission"] is not None and outcomes[0]["receipt"] is not None, "actual Companion admission and receipt")
        admission = outcomes[0]["admission"]
        require(admission["scope"]["person_id"] is not None and admission["scope"]["conversation_id"] is not None, "native Memory identity and Companion conversation")
        summary["checks"]["admission"] = {"state": outcomes[0]["state"], "scope": admission["scope"], "binding_version": admission["binding_version"]}

        recorder.stage("automatic-background-material-write")
        deadline = time.monotonic() + 40
        background = None
        while time.monotonic() < deadline:
            def status(app):
                core = app.state.core
                return {
                    "turns": [{key: turn.get(key) for key in ("id", "phase", "failure", "model_calls", "scope_version")} for turn in core.store.list("turns")],
                    "outbox": [{key: item.get(key) for key in ("id", "state", "attempts", "last_error", "receipt")} for item in core.store.list("outbox")],
                }
            background = await host.exchange.invoke("companion", status)
            if any(item["state"] == "delivered" for item in background["outbox"]):
                break
            await asyncio.sleep(0.2)
        summary["checks"]["background"] = background
        require(any(item["state"] == "delivered" and item["receipt"] is not None for item in background["outbox"]), "automatic outbox must receive real Memory material receipt")
        require(any(row["owner"] == "memory" and row["path"] == "/internal/v1/memory/candidates/materials" and row.get("status") == 200 for row in recorder.snapshot()), "background write must reach the original Memory materials endpoint")

        recorder.stage("companion-memory-source-callback-query")
        correlation = secrets.token_hex(16)
        context = await query(host, admission, correlation)
        episodes = [item for item in context["evidence"] if item["kind"] == "episode"]
        require(any(item["text"] == marker and item["owner_scope"] == admission["scope"] for item in episodes), "Memory must return the actual admitted raw source in the same scope")
        traced = [row for row in recorder.snapshot() if row["correlation_id"] == correlation]
        for owner, path in (
            ("memory", "/internal/v2/memory/context/query"),
            ("companion", "/internal/v1/source-facts/read"),
            ("platform", "/internal/v1/source-access/read"),
            ("platform", "/internal/v1/origins/resolve"),
        ):
            require(any(row["owner"] == owner and row["path"] == path and row.get("status") == 200 for row in traced), "real callback with inherited correlation: " + owner + path)
        summary["checks"]["query"] = {"correlation_id": correlation, "episode_count": len(episodes), "source_sha256": hashlib.sha256(marker.encode()).hexdigest(), "callbacks": traced}

        recorder.stage("fresh-bundle-verification")
        async def verify(app):
            return await app.state.core.memory.verify_context(admission["accepted_origin"], admission["scope"], context["bundle"], [])
        verified = await host.exchange.invoke("companion", verify)
        summary["checks"]["verify"] = {"passed": True, "result": verified}

        recorder.stage("role-revoke-denies-existing-identity")
        await role_grant(host, bindings, 1, False, "native-memory:disable-static")
        summary["checks"]["role_revocation"] = await expect_forbidden(lambda: query(host, admission, secrets.token_hex(16)), "revoked role must be denied")
        await role_grant(host, bindings, 2, True, "native-memory:restore-static")
        restored = await query(host, admission, secrets.token_hex(16))
        require(any(item.get("text") == marker for item in restored["evidence"]), "re-enabled role can read the preserved source")

        recorder.stage("source-entry-revoke-denies-existing-bundle")
        async def revoke(app):
            platform = app_platform(app)
            await platform.store.run_local(platform.origins.revoke, bearer("ADMIN"), "entry", "actor-a")
        await host.exchange.invoke("platform", revoke)
        summary["checks"]["source_revocation"] = await expect_forbidden(lambda: query(host, admission, secrets.token_hex(16)), "revoked Platform entry must be denied")
        summary["checks"]["source_revoked_verify"] = await expect_forbidden(lambda: host.exchange.invoke("companion", verify), "revoked entry must deny verification of the previously valid bundle")
        current = await json_call(host, "memory", "platform", "/internal/v1/source-access/read", bindings["tokens"]["source_access"], current_request(accepted))
        require(current["status"] == 200 and current["body"]["grants"] and all(item["state"] == "denied" for item in current["body"]["grants"]), "Platform source-current must explicitly deny the revoked entry")
        summary["checks"]["source_current_after_revoke"] = current
        require(len(provider_calls) == 0, "no provider request is allowed in this acceptance check")
        summary["checks"]["provider_calls"] = 0
        summary["ok"] = True
    except BaseException as error:
        summary["failure"] = {"stage": recorder.phase, "type": type(error).__name__, "code": getattr(error, "code", None)}
        if isinstance(error, AssertionError):
            summary["failure"]["assertion"] = str(error)
        raise
    finally:
        recorder.stage("shutdown")
        if host is not None:
            try:
                summary["closed"] = await host.close(timeout=30)
                if not summary["closed"]:
                    summary["ok"] = False
            except BaseException as error:
                summary["closed"] = False
                summary["shutdown_error_type"] = type(error).__name__
                summary["ok"] = False
        release.set()
        await runner.cleanup()
        rows = recorder.snapshot()
        if "owners" in summary:
            summary["checks"]["dispatch_owner_threads"] = all(row["thread"] == summary["owners"][row["owner"]] for row in rows)
            if not summary["checks"]["dispatch_owner_threads"]:
                summary["ok"] = False
        if original_databases is not None:
            unchanged = all(hashlib.sha256((options.memory_fixture / relative).read_bytes()).hexdigest() == digest for relative, digest in original_databases.items())
            summary["checks"]["original_fixture_databases_unchanged"] = unchanged
            if not unchanged:
                summary["ok"] = False
        if options.output.is_dir():
            smoke.write(options.output / "native-memory-requests.json", rows)
            smoke.write(options.output / "native-memory-validation.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    require(summary["ok"], "native memory closure did not pass all required checks")


if __name__ == "__main__":
    asyncio.run(execute(arguments()))
