"""Thin same-origin media commands over the media owners. No browser authority is inferred."""

import asyncio
import base64
import hashlib
import json
import uuid
from dataclasses import asdict

from ..contracts import Fault, require
from .accounts import normalize_cookie, public_account
from .config import quality, source, text
from .metadata import normalize_bilibili
from .runtime import public_job
from .repository import VISIBLE
from .subscriptions import job_payload
from .destination import snapshot
from .types import MetadataValidationError
from .rules import RuleValidationError, parse_policy
from .rule_context import build_rule_context, public_rule_context, rule_snapshot_revision
from .music.web import WebMusic
from .books.web import WebBooks
from .comics.web import WebComics

PREFIX = "/api/v1/media/"
JOB_STATES = frozenset(
    {
        "queued",
        "downloading",
        "validating",
        "waiting_metadata",
        "metadata_ready",
        "publishing",
        "asset_indexed",
        "library_verifying",
        "published",
        "completed",
        "retry_wait",
        "auth_required",
        "failed",
        "cancelled",
        "unknown",
    }
)


def page_cursor(job, states):
    return (
        base64.urlsafe_b64encode(
            json.dumps(
                {"created": int(job["created_at"]), "id": job["job_id"], "states": states},
                separators=(",", ":"),
            ).encode()
        )
        .decode()
        .rstrip("=")
    )


class WebMedia:
    def __init__(self, media, authority, commands):
        self.media = media
        self.authority,self.commands = authority,commands
        self.music = WebMusic(self)
        self.books = WebBooks(self)
        self.comics = WebComics(self)

    async def live(self, session):
        await self.authority.check(session)

    async def route(self, path, body, session):
        operation = path[len(PREFIX) :]
        require(path.startswith(PREFIX) and isinstance(body, dict), "invalid_input", 400)
        if operation == "view":
            require(body == {}, "invalid_input", 400)
            return await self.media.view()
        require(
            self.media.config is not None
            or operation in {"jobs/detail", "jobs/list", "jobs/control", "jobs/delete"},
            "media_not_configured",
            503,
        )
        await self.live(session)
        method = {
            "resolve": self.resolve,
            "enqueue": self.enqueue,
            "rules/preview": self.rules_preview,
            "subscriptions/save": self.subscription_save,
            "subscriptions/destination-preview": self.subscription_destination_preview,
            "subscriptions/control": self.subscription_control,
            "subscriptions/scan": self.subscription_scan,
            "jobs/detail": self.job_detail,
            "jobs/list": self.jobs_list,
            "jobs/control": self.job_control,
            "jobs/metadata": self.job_metadata,
            "jobs/refresh_metadata": self.refresh_metadata,
            "jobs/delete": self.job_delete,
            "accounts/import": self.account_import,
            "accounts/check": self.account_check,
            "accounts/revoke": self.account_revoke,
            "accounts/qr/start": self.qr_start,
            "accounts/qr/poll": self.qr_poll,
            "targets/test": self.target_test,
            "targets/save": self.target_save,
            "targets/remove": self.target_remove,
        }.get(operation)
        require(method is not None, "not_found", 404)
        try:
            return await method(body, session)
        except (RuleValidationError, MetadataValidationError):
            raise Fault("invalid_media_input", 400) from None

    async def receipt(self, body, name, session, operation):
        await self.live(session)
        from tianshu.runtime.api import CommandResult
        from tianshu.runtime.commands import body_hash
        from ..execution_context import CURRENT_ACTION
        action=CURRENT_ACTION.get()
        from ..execution_context import CURRENT_EXECUTION
        execution=CURRENT_EXECUTION.get()
        require(action is not None,"trusted_action_required",403)
        client_id=body.get("client_request_id") or action.intent_key
        def apply():
            with self.media.repo.transaction() as connection:
                answer=self.commands.execute_sync_on(connection,session,name,client_id,body_hash(body),lambda db:CommandResult(client_id,operation(db),action_id=action.action_id,attempt_id=execution["attempt"].attempt_id if execution else None),on_replay=lambda db,identity:CommandResult(client_id,{"_replay_action_id":identity.action_id},action_id=identity.action_id,attempt_id=identity.attempt_id))
                return answer.data
        from ..storage import run_repository
        result=await run_repository(self.commands.database,apply)
        await self.authority.check(session,action.source_refs)
        if isinstance(result,dict) and "_replay_action_id" in result:
            original=await self.media.capabilities.actions.get(result["_replay_action_id"],session)
            if original["receipt"]["execution"]!="succeeded":raise Fault("command_outcome_requires_reconciliation",409)
            return original["receipt"]["result"]
        return result

    async def target_test(self, body, session):
        require(set(body) <= {"kind", "base_url", "token", "target_id"}, "invalid_input", 400)
        connection = await self.media.local(self.media.targets.connection, body)
        libraries = await self.media.servers.libraries(connection)
        await self.live(session)
        return {"libraries": libraries}

    async def target_save(self, body, session):
        require(
            set(body)
            == {
                "target_id",
                "expected_revision",
                "label",
                "kind",
                "base_url",
                "token",
                "library_id",
                "media_root",
                "directory",
                "client_request_id",
            },
            "invalid_input",
            400,
        )
        connection = await self.media.local(self.media.targets.connection, body)
        safe = self.media.targets.safe_command(body, connection)
        result = await self.receipt(
            safe, "targets/save", session, lambda db: self.media.targets.save(body, connection, db)
        )
        await self.media.local(self.media.targets.reload)
        return result

    async def target_remove(self, body, session):
        require(
            set(body) == {"target_id", "expected_revision", "client_request_id"}
            and type(body["expected_revision"]) is int,
            "invalid_input",
            400,
        )
        result = await self.receipt(
            body, "targets/remove", session, lambda db: self.media.targets.remove(body, db)
        )
        await self.media.local(self.media.targets.reload)
        return result

    async def resolve(self, body, session):
        require(set(body) == {"url", "account_id"}, "invalid_input", 400)
        cookie = await self.media.local(self.media.accounts.cookie, body["account_id"])
        account = (
            await self.media.local(self.media.repo.get, "account", body["account_id"])
            if body["account_id"]
            else None
        )
        resolved = await self.media.resolve(body["url"], cookie)
        normalize_bilibili(resolved["snapshot"])
        await self.live(session)
        identity = str(uuid.uuid4())
        now = self.media.repo.clock()
        self.media.previews = {
            key: value for key, value in self.media.previews.items() if value["expires_at"] > now
        }
        require(len(self.media.previews) < 128, "media_preview_capacity", 429)
        self.media.previews[identity] = {
            **resolved,
            "expires_at": now + 600,
            "account_id": body["account_id"],
            "account_revision": account["revision"] if account else None,
        }
        return {"preview_id": identity, "expires_at": now + 600, "video": resolved["video"]}

    def preview(self, identity):
        require(isinstance(identity, str), "invalid_input", 400)
        preview = self.media.previews.get(identity)
        require(
            preview is not None and preview["expires_at"] > self.media.repo.clock(),
            "preview_expired",
            409,
        )
        return preview

    async def enqueue(self, body, session):
        require(
            set(body) - {"destination_subdirectory"}
            == {"preview_id", "part_cids", "account_id", "target_id", "quality", "client_request_id"},
            "invalid_input",
            400,
        )
        preview = self.preview(body["preview_id"])
        require(body["account_id"] == preview["account_id"], "preview_account_conflict", 409)
        if preview["account_id"]:
            current = await self.media.local(self.media.repo.get, "account", preview["account_id"])
            require(
                current
                and current["state"] == "ready"
                and current["revision"] == preview["account_revision"],
                "account_changed",
                409,
            )
        require(body["target_id"] in self.media.config["targets"], "target_not_configured", 409)
        require(isinstance(body["part_cids"], list), "invalid_input", 400)
        requested = quality(body["quality"])
        for part in preview["video"]["parts"]:
            if (
                part["cid"] in body["part_cids"]
                and requested["mode"] == "exact"
                and not requested["allow_fallback"]
            ):
                require(
                    any(
                        value["quality_id"] == requested["quality_id"] for value in part["formats"]
                    ),
                    "quality_unavailable",
                    409,
                )
        payload = job_payload(
            preview,
            body["part_cids"],
            body["account_id"],
            body["target_id"],
            requested,
            **snapshot(
                self.media.config["targets"][body["target_id"]],
                body.get("destination_subdirectory", ""),
            ),
        )
        result = await self.receipt(
            body,
            "enqueue",
            session,
            lambda db: {"jobs": [public_job(self.media.repo.enqueue(payload, db=db))]},
        )
        self.media.wake.set()
        return result

    async def rules_preview(self, body, session):
        require(
            set(body) - {"source", "subscription_id"} == {"preview_id", "rules"}
            and not ("source" in body and "subscription_id" in body),
            "invalid_input",
            400,
        )
        policy = parse_policy(body["rules"])
        preview = self.preview(body["preview_id"])
        source_value = source(body["source"]) if "source" in body else None
        if "subscription_id" in body:
            identity = body["subscription_id"]
            require(isinstance(identity, str) and 0 < len(identity) <= 64, "invalid_input", 400)
            subscription = await self.media.local(self.media.repo.get, "subscription", identity)
            require(subscription is not None, "not_found", 404)
            require(
                subscription["account_id"] == preview["account_id"], "preview_account_conflict", 409
            )
            source_value = subscription["source"]
        await self._rule_preview_account(preview)
        member = None
        if source_value:
            cookie = await self.media.local(self.media.accounts.cookie, preview["account_id"])
            try:
                members = await asyncio.wait_for(
                    self.media.connector.scan(
                        source_value,
                        cookie,
                        page_limit=self.media.config["scan_page_limit"],
                        item_limit=self.media.config["scan_item_limit"],
                    ),
                    self.media.config["scan_timeout_seconds"],
                )
            except TimeoutError:
                raise Fault("source_budget_exceeded", 503) from None
            member = members.get(preview["snapshot"]["bvid"])
        metadata = normalize_bilibili(preview["snapshot"])
        context = build_rule_context(metadata, preview, source_value, member)
        decision = await self.media.evaluator.evaluate(
            metadata,
            body["rules"],
            accessible=True,
            quality_satisfied=False,
            snapshot_revision=rule_snapshot_revision(preview["snapshot"], context, policy),
            context=context,
        )
        await self._rule_preview_account(preview)
        await self.live(session)
        return {
            "context": public_rule_context(metadata, context),
            "results": [
                {
                    "cid": part["cid"],
                    "decision": decision.decision,
                    "reason": decision.reason,
                    "automatic_enqueue_allowed": decision.automatic_enqueue_allowed,
                    "requires_rule_attention": decision.requires_rule_attention,
                    "trace": asdict(decision),
                }
                for part in preview["snapshot"]["parts"]
            ],
        }

    async def _rule_preview_account(self, preview):
        if preview["account_id"]:
            current = await self.media.local(self.media.repo.get, "account", preview["account_id"])
            require(
                current
                and current["state"] == "ready"
                and current["revision"] == preview["account_revision"],
                "account_changed",
                409,
            )

    async def subscription_save(self, body, session):
        result = await self.receipt(
            body,
            "subscriptions/save",
            session,
            lambda db: {"subscription": self.media.subscriptions.save(body, db)},
        )
        self.media.wake.set()
        return result

    async def subscription_destination_preview(self, body, session):
        result = await self.media.local(self.media.subscriptions.destination_preview, body)
        await self.live(session)
        return result

    async def subscription_control(self, body, session):
        return await self.receipt(
            body,
            "subscriptions/control",
            session,
            lambda db: {"subscription": self.media.subscriptions.control(body, db)},
        )

    async def subscription_scan(self, body, session):
        require(
            set(body) == {"subscription_id", "recompute", "client_request_id"}
            and type(body["recompute"]) is bool,
            "invalid_input",
            400,
        )
        # Replay check precedes external IO; a crash before the receipt can only repeat a read scan.
        request = {"operation": "subscriptions/scan", "body": body}
        result = await self.media.subscriptions.scan(
            body["subscription_id"], recompute=body["recompute"]
        )
        await self.live(session)
        return await self.receipt(body, "subscriptions/scan", session, lambda db: result)

    async def job_detail(self, body, session):
        require(set(body) == {"job_id"}, "invalid_input", 400)
        job = await self.media.local(self.media.repo.job, body["job_id"])
        require(job is not None, "not_found", 404)
        if job.get("media_kind") in {"audio", "book", "comic"}:
            return {
                "job": public_job(job),
                "metadata": None,
                "events": await self.media.local(self.media.repo.events, job["job_id"]),
            }
        metadata = normalize_bilibili(job["snapshot"], overrides=job.get("overrides"))
        return {
            "job": public_job(job),
            "events": await self.media.local(self.media.repo.events, job["job_id"]),
            "metadata": {
                "title": metadata.display_title,
                "description": metadata.display_description,
                "original_title": metadata.original_title,
                "original_description": metadata.original_description,
                "title_overridden": "title" in job.get("overrides", {}),
                "description_overridden": "description" in job.get("overrides", {}),
                "issues": list(metadata.issue_codes()),
                "cover_available": bool(job.get("cover_present", job["cover_url"] is not None)),
            },
        }

    async def jobs_list(self, body, session):
        require(
            set(body) == {"states", "cursor", "page_size"}
            and type(body["page_size"]) is int
            and 1 <= body["page_size"] <= 100,
            "invalid_input",
            400,
        )
        states = body["states"]
        require(
            states is None
            or isinstance(states, list)
            and all(isinstance(state, str) and state in JOB_STATES for state in states),
            "invalid_input",
            400,
        )
        states = sorted(set(states)) if states is not None else None
        after = None
        if body["cursor"] is not None:
            require(
                isinstance(body["cursor"], str) and len(body["cursor"]) <= 2048,
                "invalid_input",
                400,
            )
            try:
                raw = body["cursor"]
                cursor = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
                require(
                    set(cursor) == {"created", "id", "states"}
                    and type(cursor["created"]) is int
                    and str(uuid.UUID(cursor["id"])) == cursor["id"],
                    "invalid_input",
                    400,
                )
            except (ValueError, TypeError, KeyError):
                raise Fault("invalid_input", 400) from None
            require(cursor["states"] == states, "cursor_conflict", 409)
            after = (cursor["created"], cursor["id"])
        jobs, more = await self.media.local(
            self.media.repo.jobs, limit=body["page_size"], after=after, states=states
        )
        return {
            "jobs": [public_job(job) for job in jobs],
            "page": {
                "has_more": more,
                "next_cursor": page_cursor(jobs[-1], states) if more else None,
            },
        }

    async def refresh_metadata(self, body, session):
        domain = self.media.metadata_refresh
        prepared = await domain.prepare(body)
        result = await self.receipt(
            body, "jobs/refresh_metadata", session, lambda db: domain.commit(body, prepared, db)
        )
        self.media.wake.set()
        return result

    async def job_control(self, body, session):
        require(
            set(body) == {"job_id", "action", "client_request_id"}
            and body["action"] in {"cancel", "retry"},
            "invalid_input",
            400,
        )

        def operation(db):
            row = db.execute(
                'SELECT body FROM jobs WHERE id=%s AND ' + VISIBLE, (body["job_id"],)
            ).fetchone()
            require(row is not None, "not_found", 404)
            job = json.loads(row[0])
            public = public_job(job)
            require(
                public["can_cancel"] if body["action"] == "cancel" else public["can_retry"],
                "job_action_conflict",
                409,
            )
            changes = (
                {"cancel_requested": True, "code": "cancel_requested"}
                if body["action"] == "cancel"
                else {
                    "cancel_requested": False,
                    "state": "queued",
                    "code": "retry_requested",
                    "attempts": 0,
                    "library_attempts": 0,
                }
            )
            if job.get("media_kind") == "book" and body["action"] == "retry":
                changes["book_rule_snapshot"] = None
            if (
                body["action"] == "cancel"
                and body["job_id"] not in self.media.worker.active
                and not job.get("asset_operation")
            ):
                changes.update(state="cancelled", code="user_cancelled")
            elif body["action"] == "cancel" and body["job_id"] not in self.media.worker.active:
                changes.update(state="queued")
            return {"job": public_job(self.media.repo.update_job(job["job_id"], changes, db=db))}

        result = await self.receipt(body, "jobs/control", session, operation)
        if body["action"] == "cancel":
            task = self.media.worker.active.get(body["job_id"])
            if task:
                task.cancel()
        self.media.wake.set()
        return result

    async def job_metadata(self, body, session):
        require(
            set(body) == {"job_id", "title", "description", "expected_revision", "client_request_id"},
            "invalid_input",
            400,
        )

        def operation(db):
            row = db.execute(
                'SELECT body FROM jobs WHERE id=%s AND ' + VISIBLE, (body["job_id"],)
            ).fetchone()
            require(row is not None, "not_found", 404)
            job = json.loads(row[0])
            require(job["revision"] == body["expected_revision"], "version_conflict", 409)
            require(
                job.get("media_kind") not in {"audio", "book", "comic"}, "job_action_conflict", 409
            )
            require(
                job["state"] in {"waiting_metadata", "failed", "published", "completed"},
                "job_action_conflict",
                409,
            )
            overrides = {"title": body["title"], "description": body["description"]}
            normalize_bilibili(job["snapshot"], overrides=overrides)
            changes = {
                "title": body["title"],
                "overrides": overrides,
                "state": "queued",
                "stage": "metadata",
                "code": "metadata_updated",
                "package": None,
                "asset_operation": None,
                "metadata_changed": True,
                "edition": job.get("edition", 0) + 1,
                "cancel_requested": False,
            }
            if job.get("asset_receipt"):
                changes["base_edition"] = {
                    "package": job["package"],
                    "asset_operation": job["asset_operation"],
                    "asset_receipt": job["asset_receipt"],
                    "selected_cids": job["selected_cids"],
                    "downloads": job["downloads"],
                    "snapshot": job["snapshot"],
                    "quality": job["quality"],
                    "bvid": job["bvid"],
                    "target_id": job["target_id"],
                    "destination_subdirectory": job.get("destination_subdirectory", ""),
                    "destination_directory": job.get("destination_directory"),
                    "job_id": job["job_id"],
                }
            return {"job": public_job(self.media.repo.update_job(job["job_id"], changes, db=db))}

        result = await self.receipt(body, "jobs/metadata", session, operation)
        self.media.wake.set()
        return result

    async def job_delete(self, body, session):
        require(
            set(body) == {"job_id", "expected_revision", "client_request_id"}
            and isinstance(body["job_id"], str)
            and type(body["expected_revision"]) is int
            and body["expected_revision"] > 0,
            "invalid_input",
            400,
        )
        require(body["job_id"] not in self.media.worker.active, "job_not_deletable", 409)
        return await self.receipt(
            body,
            "jobs/delete",
            session,
            lambda db: self.media.repo.delete_job(body["job_id"], body["expected_revision"], db=db),
        )

    async def account_import(self, body, session):
        require(set(body) == {"label", "cookie", "client_request_id"}, "invalid_input", 400)
        label, cookie = text(body["label"], 128), normalize_cookie(body["cookie"])
        # Persist only a digest of the import command, never its plaintext cookie.
        safe = {
            "label": label,
            "cookie_digest": hashlib.sha256(cookie.encode()).hexdigest(),
            "client_request_id": body["client_request_id"],
        }
        status = await self.media.connector.account(cookie)
        return await self.receipt(
            safe,
            "accounts/import",
            session,
            lambda db: {
                "account": public_account(self.media.accounts.save(label, cookie, status, db=db))
            },
        )

    async def account_check(self, body, session):
        require(set(body) == {"account_id"}, "invalid_input", 400)
        account = await self.media.local(self.media.repo.get, "account", body["account_id"])
        require(account is not None, "not_found", 404)
        cookie = await self.media.local(self.media.accounts.cookie, body["account_id"], True)
        try:
            await self.media.connector.account(cookie)
            state, code = "ready", "ready"
        except Fault as error:
            if error.code not in {"auth_required", "account_unavailable"}:
                raise
            state, code = "auth_required", error.code
        await self.live(session)
        result = await self.media.local(
            self.media.repo.fact if state == account["state"] else self.media.repo.put,
            "account",
            account["account_id"],
            {**account, "state": state, "code": code, "checked_at": self.media.repo.clock()},
            account["revision"],
        )
        return {"account": public_account(result)}

    async def account_revoke(self, body, session):
        require(set(body) == {"account_id", "expected_revision", "client_request_id"}, "invalid_input", 400)
        return await self.receipt(
            body,
            "accounts/revoke",
            session,
            lambda db: {
                "account": public_account(
                    self.media.accounts.revoke(body["account_id"], body["expected_revision"], db=db)
                )
            },
        )

    async def qr_start(self, body, session):
        require(set(body) == {"label"}, "invalid_input", 400)
        label = text(body["label"], 128)
        now = self.media.repo.clock()
        self.media.qr = {
            key: value for key, value in self.media.qr.items() if value["expires_at"] > now
        }
        require(len(self.media.qr) < 16, "qr_capacity", 429)
        data = await self.media.connector.qr_start()
        await self.live(session)
        identity = str(uuid.uuid4())
        self.media.qr[identity] = {
            "key": data["qrcode_key"],
            "label": label,
            "expires_at": now + 180,
            "session": session,
            "last_poll": 0,
            "lock": asyncio.Lock(),
        }
        return {"qr_id": identity, "url": data["url"], "expires_at": now + 180}

    async def qr_poll(self, body, session):
        require(set(body) == {"qr_id", "client_request_id"}, "invalid_input", 400)
        qr = self.media.qr.get(body["qr_id"])
        if qr is None or qr["expires_at"] <= self.media.repo.clock():
            return {"state": "expired", "account": None}
        require(qr["session"] is session, "forbidden", 403)
        async with qr["lock"]:
            if qr.get("account"):
                return {"state": "ready", "account": qr["account"]}
            require(self.media.repo.clock() - qr["last_poll"] >= 2, "qr_poll_too_fast", 429)
            qr["last_poll"] = self.media.repo.clock()
            result = await self.media.connector.qr_poll(qr["key"])
            if result["state"] != "ready":
                return {"state": result["state"], "account": None}
            cookie = normalize_cookie(result["cookie"])
            status = await self.media.connector.account(cookie)
            saved = await self.receipt(
                body,
                "accounts/qr/poll",
                session,
                lambda db: {
                    "account": public_account(
                        self.media.accounts.save(qr["label"], cookie, status, db=db)
                    )
                },
            )
            qr["account"] = saved["account"]
            return {"state": "ready", "account": saved["account"]}
