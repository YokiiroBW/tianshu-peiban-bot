"""Real local-user application/CLI helpers; credentials and approvals never come from a fake adapter."""

import asyncio
import copy
import json
import secrets
import subprocess
import sys
import time

from tianshu_companion.clients import command, utc
from tianshu_memory.app import configured_app
from tianshu_memory.user_actions import LocalUserApplication, credential_digest
from tianshu_memory.workflow import TrustedWorkflow
from ts050_source_support import SourceChain, reserve


class ApprovalChain(SourceChain):
    migrate_users = True

    def register_local_user(self, principal, admissions, permissions=()):
        credential = "local-synthetic-user-" + secrets.token_urlsafe(40)
        self.tokens["LOCAL_" + principal] = (
            credential  # Included in the existing secret-leak check.
        )
        self.set_env("TS050_LOCAL_" + principal.upper(), credential)
        self.memory_config.setdefault("local_users", {})[principal] = {
            "credential_sha256": credential_digest(credential),
            "account": self.account,
            "actors": sorted({a["scope"]["actor_id"] for a in admissions}),
            "revision_scopes": [a["scope"] for a in admissions],
            "profile_permissions": list(permissions),
            "disabled": False,
        }
        self.write_memory_config()
        self.check(
            "local_user_registered_after_real_mapping",
            principal=principal,
            scopes=[a["scope"] for a in admissions],
            permissions=list(permissions),
            credential="independent random synthetic user credential; not recorded",
        )

    async def user_action(self, action, principal, *, cli=False, credential=None):
        credential = self.tokens["LOCAL_" + principal] if credential is None else credential
        if cli:
            path = self.directory / ("user-action-" + secrets.token_hex(6) + ".json")
            path.write_text(json.dumps(action, ensure_ascii=False), "utf-8")
            self.set_env("TS050_CURRENT_LOCAL_USER", credential)
            result = await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable,
                    "-B",
                    "-c",
                    "from tianshu_memory.cli import main; main()",
                    "--config",
                    str(self.memory_config_path),
                    "user-action",
                    str(path),
                    "--principal",
                    principal,
                    "--credential-env",
                    "TS050_CURRENT_LOCAL_USER",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            value = json.loads(result.stdout)
        else:
            value = await asyncio.to_thread(
                LocalUserApplication(self.memory, self.memory_config_path).execute,
                action,
                principal=principal,
                credential=credential,
            )
        self.trace.setdefault("local_user_actions", []).append(
            {
                "principal": principal,
                "entry": "CLI cli:main" if cli else "LocalUserApplication.execute",
                "action": copy.deepcopy(action),
                "result": {"approval_ref": value} if isinstance(value, str) else value,
                "external_user": "explicit complete synthetic local operation; not model consent",
            }
        )
        return value

    def revision_request(self, admission, record_id, *, kind="forget", version=1):
        return {
            "command": command(
                admission["accepted_origin"], "local-revision:" + secrets.token_hex(8), time.time()
            ),
            "record_id": record_id,
            "expected_version": version,
            "revision_kind": kind,
            "confirmation_ref": "confirmation:" + secrets.token_hex(16),
            "evidence_refs": [admission["source"]],
            "replacement_statement": None,
        }

    async def approve_forget(self, principal, admission, record_id, *, cli=True):
        request = self.revision_request(admission, record_id)
        approval = await self.user_action(
            {
                "operation": "confirm_revision",
                "request": request,
                "expires_at": utc(time.time() + 120),
            },
            principal,
            cli=cli,
        )
        response = await self.client.post(
            self.memory_url + "/internal/v1/memory/revise",
            json=request,
            headers={"Authorization": self.bearer("CORE_MEMORY")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.check(
            "confirmed_then_actual_HTTP_forget",
            approval=approval,
            request=request,
            response=response.json(),
        )
        return request, response.json()

    def profile_draft(
        self,
        admission,
        *,
        subject=None,
        category="interest",
        sharing="public_preference",
        statement="下午偶尔喜欢咖啡。",
    ):
        scope = admission["scope"]
        return {
            "source_scope": scope,
            "subject": subject or {"kind": "person", "person_id": scope["person_id"]},
            "sharing": sharing,
            "conversation_id": scope["conversation_id"] if sharing == "group_only" else None,
            "category": category,
            "field_key": "coffee_" + category,
            "units": [
                {
                    "statement": statement,
                    "conditions": ["合成明确陈述的范围内"],
                    "negations": [],
                    "valid_time": "current synthetic preference",
                    "uncertainty": "uncertain",
                    "reality": "real",
                    "sources": [admission["source"]],
                }
            ],
        }

    async def publish(self, principal, admission, draft, *, cli_approve=False):
        ref = await self.user_action(
            {
                "operation": "approve_profile",
                "origin": admission["accepted_origin"],
                "draft": draft,
                "expires_at": utc(time.time() + 600),
            },
            principal,
            cli=cli_approve,
        )
        action = {
            "operation": "publish_profile",
            "origin": admission["accepted_origin"],
            "draft": draft,
            "approval_ref": ref,
        }
        published = await self.user_action(action, principal, cli=not cli_approve)
        return ref, published, action

    async def profile_query(self, admission, target, categories, *, budget=None):
        return await self.core.memory.profiles(
            admission["accepted_origin"],
            admission["scope"],
            target,
            "咖啡",
            categories,
            budget or {"tokens": 4096, "bytes": 8192},
        )

    async def restart_memory_and_core(self):
        core_port, memory_port = (
            self.core_socket.getsockname()[1],
            self.memory_socket.getsockname()[1],
        )
        await self.core_server.close()
        await self.memory_server.close()
        self.memory_socket = reserve(memory_port)
        self.memory_app = configured_app()
        self.memory = self.memory_app.state.memory
        self.workflow = TrustedWorkflow(self.memory)
        self.memory_server = await self.start_asgi(self.memory_app, "memory", self.memory_socket)
        self.core_socket = reserve(core_port)
        await self.start_core()
