"""Persistent intents, invocation contexts and conservative interruption semantics."""

import asyncio
import base64
import copy
import re
import secrets
import time

from ..contracts import Fault, canonical, require
from .process import RemoteError
from .schema import capabilities, check, fill_defaults, merge


class Executions:
    def __init__(self, host):
        self.host, self.repo = host, host.repository
        self.contexts, self.running, self.authorizers = {}, {}, {}

    def refusal(self, identifier):
        """The code a blocked instance returns while its lifecycle switches, else None."""
        admission = getattr(self.host, "admission", {})
        return admission.get(identifier) if hasattr(admission, "get") else None

    async def admit(
        self, identifier, action_id, value, intent, principal, *, authorize=None, binding_id=None,
        defer=False,
    ):
        await self.host.ensure_active()
        require(
            isinstance(intent, str) and 1 <= len(intent) <= 128 and isinstance(value, dict),
            "invalid_input",
            400,
        )
        if authorize:
            require(await self.host.check_authorizer(authorize), "session_expired", 401)
        async with self.host.operations_lock:
            instance = await self.host.instance(identifier)
            # New admission is refused while this instance's lifecycle switches;
            # already accepted calls keep their original attempt and identity.
            refusal = self.refusal(identifier)
            require(not refusal, refusal or "instance_reloading", 409)
            require(instance["desired_state"] == "enabled", "instance_disabled", 409)
            package = await self.host.package(instance["package_id"], instance["version"])
            action = next(
                (item for item in package["description"]["actions"] if item["id"] == action_id),
                None,
            )
            require(action is not None, "not_found", 404)
            if authorize and hasattr(authorize, "authorize_action"):
                await self.host.check_authorizer(authorize.authorize_action, identifier, action_id, binding_id)
            require(
                all(
                    port["id"] in self.host.available_capabilities and port["major"] == 1
                    for port in action["required_capabilities"]
                ),
                "capability_unavailable",
                409,
            )
            value = fill_defaults(action["input_schema"], value)
            check(action["input_schema"], value)
            snapshot = copy.deepcopy(instance)
            if binding_id:
                binding = next(
                    (item for item in instance["bindings"] if item["binding_id"] == binding_id),
                    None,
                )
                require(binding and binding["enabled"], "permission_denied", 403)
                require(
                    "entries" not in binding
                    or {"kind": "action", "id": action_id} in binding["entries"],
                    "permission_denied",
                    403,
                )
                snapshot["config"] = merge(snapshot["config"], binding["overrides"])
            snapshot["secret_snapshot"] = await self.host.local(
                self.host.vault.snapshot, identifier
            )
            document, created = await self.host.local(
                self.repo.accept, snapshot, action, value, intent, principal, binding=binding_id
            )
        if created and not defer:
            self.dispatch(document["id"], authorize)
        return self.public(document)

    async def invoke(
        self, identifier, action_id, value, intent, principal, *, authorize=None, binding_id=None
    ):
        """Accept a call and start it immediately, as every existing caller expects."""
        return await self.admit(
            identifier, action_id, value, intent, principal,
            authorize=authorize, binding_id=binding_id,
        )

    def dispatch(self, identifier, authorize=None):
        """Start the accepted call; callers that must not race a switch defer this."""
        if authorize is not None:
            self.authorizers[identifier] = authorize
        task = asyncio.create_task(self.execute(identifier))
        self.running[identifier] = task
        task.add_done_callback(lambda task, id=identifier: self.running.pop(id, None))
        return task

    async def context(self, instance, call):
        package = await self.host.package(instance["package_id"], instance["version"])
        reference = secrets.token_urlsafe(32)
        snapshot = instance.get("secret_snapshot")
        if snapshot is None:
            snapshot = await self.host.local(self.host.vault.snapshot, instance["id"])
        ports = capabilities(package["manifest"]) & self.host.available_capabilities
        envelope = {
            "instance_id": instance["id"],
            "context_ref": reference,
            "config_revision": instance["revision"],
            "config": instance["config"],
            "secret_configured": {name: True for name in snapshot},
            "capabilities": sorted(ports),
        }
        self.contexts[reference] = {
            "instance": instance["id"],
            "call": call,
            "package": package,
            "ports": ports,
            "secrets": snapshot,
            # The invocation's finally block revokes this handle. A host model
            # queue has no implicit one-hour TTL; every callback reauthorizes.
            "expires": float("inf"),
        }
        if call:
            document = await self.host.local(self.repo.call, call)
            envelope.update(invocation_id=call, intent_key=document["intent_key"])
        return envelope

    def revoke(self, identifier):
        for reference, context in tuple(self.contexts.items()):
            if context["instance"] == identifier:
                self.contexts.pop(reference, None)

    async def execute(self, identifier, method="plugin.invoke"):
        document = await self.host.local(self.repo.call, identifier)
        instance = document["snapshot"]
        reference = None
        async with self.host.locks[instance["id"]]:
            try:
                current = await self.host.instance(instance["id"])
                # Re-read inside the lock: a version switch may have settled this
                # call under its original identity. It is never replayed against
                # the previous or the replacement version behind the caller's back.
                latest = await self.host.local(self.repo.call, identifier)
                if latest["execution"] != "accepted":
                    return
                if latest["package_version"] != current["version"]:
                    await self.host.local(
                        self.repo.update_call,
                        identifier,
                        execution="interrupted",
                        effect=(
                            "unknown"
                            if latest["write"] and latest["dispatch"] != "not_sent"
                            else "not_applied"
                            if latest["write"]
                            else "none"
                        ),
                        error={
                            "code": "outcome_unknown"
                            if latest["write"] and latest["dispatch"] != "not_sent"
                            else "instance_upgraded",
                            "retriable": False,
                        },
                    )
                    return
                # A version switch, reload or uninstall refuses new admission for
                # this instance only; accepted calls above are settled by identity.
                refusal = self.refusal(instance["id"])
                require(not refusal, refusal or "instance_reloading", 409)
                require(current["desired_state"] == "enabled", "instance_disabled", 409)
                authorization = self.authorizers.get(identifier)
                if authorization:
                    require(await self.host.check_authorizer(authorization), "session_expired", 401)
                package = await self.host.package(
                    instance["package_id"], document["package_version"]
                )
                action = next(
                    item
                    for item in package["description"]["actions"]
                    if item["id"] == document["action"]
                )
                process = await self.host.worker(instance)
                envelope = await self.context(instance, identifier)
                reference = envelope["context_ref"]
                params = {
                    "call": envelope,
                    "action": document["action"],
                    "input": document["input"],
                }
                if method != "plugin.invoke":
                    params["recovery"] = {
                        "input": document["input"],
                        "checkpoint": document["checkpoint"],
                        "previous_receipt": document.get("recovery_receipt", self.public(document)),
                        "dispatch": {"state": document["dispatch"]},
                    }
                await self.host.local(
                    self.repo.update_call,
                    identifier,
                    execution="running",
                    dispatch="unknown" if document["write"] else "not_sent",
                )
                from ..tracking import mark_submitted
                await mark_submitted(identifier)
                result = await process.call(method, params, timeout=action["timeout_seconds"])
                require(
                    result.get("effect")
                    in ({"none"} if action["effect"] == "read" else {"applied", "not_applied"}),
                    "plugin_error",
                    502,
                )
                check(action["output_schema"], result["result"])
                await self.host.local(
                    self.repo.update_call,
                    identifier,
                    execution="succeeded",
                    effect=result["effect"],
                    result=result["result"],
                    external=result.get("external"),
                    presentation=result.get("presentation"),
                    error=None,
                )
            except asyncio.CancelledError:
                if reference:
                    self.contexts.pop(reference, None)
                process=self.host.workers.get(instance["id"])
                if process:
                    try:await process[0].call("plugin.cancel",{"invocation_id":identifier},timeout=3)
                    except (Fault,RemoteError,TimeoutError,OSError):await self.host.stop_worker(instance["id"],"invocation_cancel_unresponsive")
                await self.host.local(
                    self.repo.update_call,
                    identifier,
                    execution="cancelled",
                    effect="unknown" if document["write"] else "none",
                    error={"code": "cancelled", "retriable": False},
                )
            except (
                Fault,
                RemoteError,
                TimeoutError,
                OSError,
                ValueError,
                KeyError,
                StopIteration,
            ) as error:
                data = error.data if isinstance(error, RemoteError) else {}
                code = data.get(
                    "code",
                    error.code
                    if isinstance(error, Fault)
                    else "timeout"
                    if isinstance(error, TimeoutError)
                    else "plugin_error",
                )
                latest = await self.host.local(self.repo.call, identifier)
                default_effect = "unknown" if latest["dispatch"] != "not_sent" else "not_applied"
                effect = data.get("effect", default_effect if document["write"] else "none")
                if document["write"] and effect == "none":
                    effect = default_effect
                if effect == "unknown":
                    code = "outcome_unknown"
                await self.host.local(
                    self.repo.update_call,
                    identifier,
                    execution="failed",
                    effect=effect,
                    error={
                        "code": code,
                        "retriable": bool(data.get("retriable", False)) and not document["write"],
                    },
                    external=data.get("external"),
                )
                # A timed-out coroutine must not continue its side effects behind the UI.
                if isinstance(error, TimeoutError):
                    await self.host.stop_worker(instance["id"], "deadline_exceeded")
            finally:
                if reference:
                    self.contexts.pop(reference, None)
                self.authorizers.pop(identifier, None)

    def public(self, document):
        if document is None:
            raise Fault("not_found", 404)
        result = {
            key: value
            for key, value in document.items()
            if key not in {"snapshot", "config", "principal"}
        }
        snapshot = document.get("snapshot", {}).get("secret_snapshot", {})
        values = [
            self.host.vault.unseal(document["instance_id"], name, base64.b64decode(value))
            for name, value in snapshot.items()
        ]
        return self.redact(result, values)

    @staticmethod
    def redact(value, secrets):
        if isinstance(value, str):
            for secret in secrets:
                if secret:
                    value = value.replace(secret, "[redacted]")
            return value
        if isinstance(value, dict):
            return {
                key: "[redacted]"
                if re.search(r"password|authorization|credential|secret|token", key, re.I)
                else Executions.redact(item, secrets)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [Executions.redact(item, secrets) for item in value]
        return value

    async def callback(self, instance, method, params):
        context = self.contexts.get(params.get("context_ref"))
        require(
            context and context["instance"] == instance and context["expires"] > time.time(),
            "permission_denied",
            403,
        )
        call = context["call"]
        current = await self.host.instance(instance)
        if call:
            require(current["desired_state"] == "enabled", "instance_disabled", 409)
            authorization = self.authorizers.get(call)
            if authorization:
                require(await self.host.check_authorizer(authorization), "session_expired", 401)
                if method == "host.capability" and hasattr(authorization, "authorize_capability"):
                    await self.host.check_authorizer(
                        authorization.authorize_capability,
                        params.get("capability"),
                        params.get("operation"),
                        params.get("input", {}),
                    )
        secret_values = [
            self.host.vault.unseal(instance, name, base64.b64decode(value))
            for name, value in context["secrets"].items()
        ]
        if method == "host.log":
            require(
                params.get("level") in {"info", "warning", "error"}
                and isinstance(params.get("event"), str)
                and re.fullmatch(r"[a-zA-Z0-9_.-]{1,96}", params["event"]),
                "invalid_input",
                400,
            )
            fields = self.redact(params.get("fields", {}), secret_values)
            require(len(canonical(fields).encode()) <= 4096, "budget_exceeded", 413)
            await self.host.local(
                self.repo.log,
                instance,
                {
                    "level": params["level"],
                    "event": params["event"],
                    "fields": fields,
                    "invocation_id": call,
                },
            )
            return {"logged": True}
        if method == "host.progress":
            require(
                call and type(params.get("completed")) is int and params["completed"] >= 0,
                "invalid_input",
                400,
            )
            total = params.get("total")
            require(
                total is None or type(total) is int and total >= params["completed"],
                "invalid_input",
                400,
            )
            progress = {
                "completed": params["completed"],
                "total": total,
                "message": self.redact(str(params.get("message", ""))[:256], secret_values),
            }
            await self.host.local(self.repo.update_call, call, progress=progress)
            return {"updated": True}
        require(
            method == "host.capability"
            and params.get("major") == 1
            and params.get("capability") in context["ports"],
            "capability_unavailable",
            409,
        )
        from .ports import capability

        result = await capability(
            self, context, params["capability"], params["operation"], params["input"]
        )
        return (
            self.redact(result, secret_values)
            if params["capability"] == "content.discovery"
            else result
        )

    async def cancel(self, identifier):
        await self.host.ensure_active()
        document = await self.host.local(self.repo.call, identifier)
        require(document is not None, "not_found", 404)
        task = self.running.get(identifier)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.authorizers.pop(identifier, None)
            document = await self.host.local(self.repo.call, identifier)
            if document["execution"] == "accepted":
                await self.host.local(
                    self.repo.update_call,
                    identifier,
                    execution="cancelled",
                    effect="not_applied" if document["write"] else "none",
                    error={"code": "cancelled", "retriable": False},
                )
        return self.public(await self.host.local(self.repo.call, identifier))

    async def recover(self, identifier, principal, authorize, *, resume=False):
        await self.host.ensure_active()
        document = await self.host.local(self.repo.call, identifier)
        require(
            document is not None
            and document["execution"] in {"failed", "interrupted", "cancelled"},
            "conflict",
            409,
        )
        require(document["principal"] == principal, "permission_denied", 403)
        package = await self.host.package(document["package_id"], document["package_version"])
        action = next(
            item for item in package["description"]["actions"] if item["id"] == document["action"]
        )
        require(
            action.get("resume_handler" if resume else "reconcile_handler"),
            "capability_unavailable",
            409,
        )
        require(identifier not in self.running, "operation_in_progress", 409)
        await self.host.local(
            self.repo.update_call,
            identifier,
            execution="accepted",
            recovery_receipt=self.public(document),
        )
        self.authorizers[identifier] = authorize
        task = asyncio.create_task(
            self.execute(identifier, "plugin.resume" if resume else "plugin.reconcile")
        )
        self.running[identifier] = task
        task.add_done_callback(lambda task: self.running.pop(identifier, None))
        return self.public(document)

    async def close(self):
        tasks = tuple(self.running.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
