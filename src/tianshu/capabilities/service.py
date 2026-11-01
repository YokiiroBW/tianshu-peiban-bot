"""One capabilities composition: model tools, schedules and explicit commands share Actions."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import time
from uuid import uuid4
from tianshu.types import Action, Attempt, OriginKind, Receipt, MediaRef, SourceRef, Coverage
from tianshu.runtime.serialization import wire
from tianshu.runtime.commands import CommandStore
from .actions import Actions
from .artifacts import Artifacts
from .catalog import Catalog, CapabilityDefinition, Availability
from .configuration import Configuration
from .contracts import Fault, digest
from .tracking import TrackedBroker
from .speech.service import Speech
from .images.service import Images
from .perception.service import Perception
from .reactions.service import Reactions
from .content.service import Content
from .devices.service import Devices
from .games.service import Games
from .operations import MATURE_OPERATIONS, READ_OPERATIONS, CLIENT_ID_OPERATIONS


class Operation:
    """An explicit command binding to a real domain use case, never a CRUD success stub."""

    def __init__(self, owner, domain, operation, *, permission=None):
        self.owner, self.domain, self.operation = owner, domain, operation
        self.effect = (
            "read"
            if (domain, operation)
            in {
                ("content", "discover"),
                ("content", "continue"),
                ("speech", "transcribe"),
                ("speech", "voices.search"),
                ("reactions", "search"),
                ("devices", "refresh"),
            }
            else "write"
        )
        self.definition = CapabilityDefinition(
            domain + "." + operation.replace("/", "."),
            domain + " / " + operation,
            "Execute the registered " + domain + " operation " + operation + ".",
            {"type": "object"},
            {"type": "object"},
            self.effect,
            permissions=frozenset({permission}) if permission else frozenset(),
            long_running=(
                domain in {"images", "reactions"}
                and operation
                not in {
                    "engines.save",
                    "profiles.save",
                    "workflows.import",
                    "assets.update",
                    "assets.batch",
                    "assets.delete",
                    "packs.update",
                }
            )
            or (domain == "speech" and operation in {"preview", "transcribe", "voices.clone"})
            or (domain == "plugins" and operation in {"install", "upgrade"})
            or (
                domain in {"media", "books", "music", "comics"}
                and operation
                in {
                    "resolve",
                    "accounts/check",
                    "targets/test",
                    "library/import",
                    "library/rebuild",
                    "library/scan",
                    "connections/probe",
                    "sources/import",
                    "sources/refresh",
                    "feeds/refresh",
                    "subscriptions/scan",
                }
            ),
        )

    async def availability(self, context):
        # Configuration operations are executable even while a provider lacks an account.
        if not self.owner.prepared:
            return Availability(True, False, reason="capabilities_starting")
        if (
            self.domain in {"media", "music"}
            and self.owner.media.config is None
            and not (self.domain == "media" and self.operation.startswith("jobs/"))
        ):
            return Availability(True, False, reason="media_not_configured")
        if self.domain == "infrastructure" and self.operation not in {"save", "catalog/refresh"}:
            ready = self.owner.infrastructure.config is not None
            return Availability(
                True, ready, reason=None if ready else "infrastructure_not_configured"
            )
        return Availability(True, True)

    async def execute(self, action, attempt):
        arguments = dict(action.arguments)
        if "command_ref" in arguments:
            arguments = await self.owner.configuration.command(action.context, arguments)
        result = await self.owner.operate(
            self.domain, self.operation, arguments, action.context, action, attempt
        )
        if isinstance(result, Receipt):
            return result
        return Receipt(
            str(uuid4()),
            action.action_id,
            attempt.attempt_id,
            "succeeded",
            "none" if self.effect == "read" else "confirmed",
            "not_requested",
            result=wire(result),
        )

    async def reconcile(self, action, attempt):
        # A completed configuration command replays from Actions. Lost external operations
        # require their actual provider/business handle, never a repeated command.
        if self.domain == "infrastructure" and self.operation.startswith("control/"):
            value = await self.owner.infrastructure.control.local(
                self.owner.infrastructure.control.lookup, client_id=action.intent_key
            )
            return Receipt(
                str(uuid4()),
                action.action_id,
                attempt.attempt_id,
                "unknown" if value.get("state") == "unknown" else "running",
                "unknown",
                "not_requested",
                result={"operation": value},
            )
        return Receipt(
            str(uuid4()),
            action.action_id,
            attempt.attempt_id,
            "unknown",
            "unknown",
            "not_requested",
            error_code="operation_requires_domain_reconciliation",
        )

    async def cancel(self, action, attempt):
        return await self.reconcile(action, attempt)


class Capabilities:
    def __init__(
        self,
        runtime,
        settings,
        *,
        authority,
        sources,
        life,
        expression,
        result_events=None,
        authorize_actor=None,
        authorize_conversation=None,
        actor_directory=None,
        worker=None,
        artifacts=None,
        weather=None,
    ):
        self.runtime, self.settings, self.authority, self.sources, self.life, self.expression = (
            runtime,
            dict(settings),
            authority,
            sources,
            life,
            expression,
        )
        self.worker = runtime.role == "worker"
        if worker is True and not self.worker:
            raise Fault("plugin_worker_role_required", 503)
        self.authorize_actor, self.actor_directory = authorize_actor, actor_directory
        state = Path(settings.get("state_directory", runtime.files.root.parent / "capabilities"))
        if not state.is_absolute():
            raise Fault("capability_state_directory_required", 503)
        self.state_directory = state
        self.configuration = (
            weather.configuration
            if weather is not None
            else Configuration(runtime.database, runtime.credentials)
        )
        self.catalog = Catalog()
        self.actions = Actions(
            runtime.database, runtime.jobs, self.catalog, authority, result_events
        )
        self.actions.argument_resolver = self.configuration.command
        self.actions.worker = self.worker
        self.actions.sources = sources
        self.models = TrackedBroker(runtime.models)
        self.artifacts = artifacts or Artifacts(runtime.database, runtime.files, authority, sources)
        self.speech = Speech(self.configuration, self.models, self.artifacts)
        self.images = Images(self.configuration, self.models, self.artifacts, life, authority)
        self.perception = Perception(self.models, self.artifacts, authority, runtime.database)
        self.reactions = Reactions(
            self.configuration, self.artifacts, self.models, self.perception, authority
        )
        self.devices = Devices(self.configuration, authority)
        self.games = Games(self.configuration, self.artifacts, authority)
        from .weather import WeatherService

        self.weather = weather or WeatherService(
            runtime,
            authority=authority,
            sources=sources,
            configuration=self.configuration,
            models=self.models,
        )
        self.weather.bind_actions(self.actions)
        from .catalog_configuration import CatalogConfiguration

        self.catalog_configuration = CatalogConfiguration(
            self.catalog, self.configuration, self.models, sources, authority
        )
        self.catalog.configuration = self.catalog_configuration
        from .resources import Resources

        self.resources = Resources(
            self.configuration, self.models, sources, authority, self.actions
        )
        from .resources.archive import Archive

        self.archive = Archive(self.configuration, self.models, sources, authority, self.catalog)
        from concurrent.futures import ThreadPoolExecutor

        self.file_executor = ThreadPoolExecutor(
            max_workers=settings.get("file_workers", 2),
            thread_name_prefix="tianshu-capability-files",
        )
        self.file_slots = asyncio.Semaphore(settings.get("file_workers", 2))
        from .media.books.reading import ReadingAccess

        self.reading = ReadingAccess(None, authority, sources)
        from .projects.service import Projects

        self.projects = Projects(
            runtime,
            settings.get("projects", {}),
            authority=authority,
            sources=sources,
            artifacts=self.artifacts,
            local=self.local,
            catalog=self.catalog,
            authorize_conversation=authorize_conversation,
        )
        self.prepared = False
        self.initialized = False
        self.media = self.plugins = self.content = self.infrastructure = None
        for handler in (self.speech, self.images, self.perception, self.devices, self.games):
            identifier = handler.definition.id
            handler.definition = replace(
                handler.definition,
                model_tool={"name": identifier.replace(".", "_")},
                long_running=identifier
                in {"images.generate", "video.observe", "speech.synthesize"},
            )
            self.catalog.register(handler)

    async def prepare(self):
        if self.prepared:
            return
        self.state_directory.mkdir(parents=True, exist_ok=True)
        await self.configuration.initialize()
        await asyncio.to_thread(self._owners)
        self.prepared = True
        self.register_operations()
        self.actions.prepare_handler = self.prepare_handler

    async def initialize(self):
        if self.initialized:
            return
        await self.prepare()
        await self.actions.start()
        self.initialized = True
        if self.worker:
            await self.media.start()
            await self.media.background.activate()
            self.plugin_host.worker_enabled = True
            await self.plugin_host.start()
            await self.infrastructure.start()

    def _owners(self):
        from .media.runtime import Media
        from .media.web import WebMedia
        from .media.books.reading import ReadingAccess
        from .plugins.manager import PluginManager
        from .plugins.service import Plugins
        from .plugins.web import WebPlugins
        from .media.comics.plugin import ComicFileWorkers
        from .infrastructure.runtime import Infrastructure
        from .infrastructure.web import WebInfrastructure

        settings = {**self.settings, "database_path": str(self.state_directory / "library")}
        self.media = Media(settings, runtime=self.runtime, clock=time.time, local=self.local)
        self.media.capabilities = self
        from .content.video import VideoOriginals

        self.perception.video_originals = VideoOriginals(self)
        from .content.browser import Browser

        self.content = Content(
            self.configuration,
            self.sources,
            self.authority,
            self.media,
            browser=Browser(
                self.configuration,
                self.authority,
                self.settings.get("content", {}).get("browser", {}),
            ),
        )
        self.plugin_host = PluginManager(
            settings,
            runtime=self.runtime,
            local=self.local,
            content_discovery=self.content.discovery,
            comic_files=ComicFileWorkers.from_settings(settings),
        )
        self.projects.bind_content(self.content)
        self.plugins = Plugins(
            self.plugin_host,
            self.catalog,
            self.authority,
            self.models,
            self.artifacts,
            self.sources,
            self.expression,
            self.runtime.jobs,
            self.runtime.database,
        )
        self.plugins.actions = self.actions
        self.plugins.configuration = self.configuration
        self.web_plugins = WebPlugins(self.plugins, self.authorize_actor, self.actor_directory)
        self.web_media = WebMedia(self.media, self.authority, CommandStore(self.runtime.database))
        self.reading.bind(self.media.books.reading)
        self.catalog.refresher = self.plugins.refresh
        self.infrastructure = Infrastructure(settings, local=self.local, runtime=self.runtime)
        self.web_infrastructure = WebInfrastructure(self.infrastructure, self.authority, self.local)

    async def local(self, operation, *args, **kwargs):
        from .storage import DomainStore, run_repository

        owner = getattr(operation, "__self__", None)
        if isinstance(getattr(owner, "storage", None), DomainStore):
            return await run_repository(self.runtime.database, operation, *args, **kwargs)
        # File work has its own bounded executor; no database connection is held while
        # waiting for this slot. Cancellation retains ownership until file work stops.
        from contextvars import copy_context

        context = copy_context()
        async with self.file_slots:
            future = asyncio.get_running_loop().run_in_executor(
                self.file_executor, lambda: context.run(operation, *args, **kwargs)
            )
            try:
                return await asyncio.shield(future)
            except asyncio.CancelledError:
                await asyncio.gather(asyncio.shield(future), return_exceptions=True)
                raise

    def register_tasks(self):
        from .media.jobs import process_job
        from .plugins.service import process_event
        from .infrastructure.jobs import process_job as infrastructure_job

        self.actions.register_tasks()

        async def media_job(**payload):
            await process_job(self, **payload)

        async def plugin_event(**payload):
            await process_event(self.plugins, **payload)

        self.runtime.jobs.register("capabilities.media_job", media_job)
        self.runtime.jobs.register("capabilities.media.library_job", media_job)
        self.runtime.jobs.register("capabilities.plugin_event", plugin_event)

        async def plugin_schedule(**payload):
            await self.plugin_host.scheduler.execute(**payload)

        self.runtime.jobs.register("capabilities.plugin_schedule", plugin_schedule)

        async def infrastructure(**payload):
            await infrastructure_job(self, **payload)

        self.runtime.jobs.register("capabilities.infrastructure_job", infrastructure)
        # Register the full durable task set before process readiness/opening queues.
        for name in (
            "book_audio",
            "book_subscriptions",
            "book_sources",
            "comic_extensions",
            "comic_cleanup",
            "comic_subscriptions",
            "subscriptions",
            "retention",
            "music_playlists",
            "music_imports",
            "music_navidrome",
            "music_maintenance",
            "music_lyrics",
            "infrastructure_observe",
        ):

            async def maintenance(_name=name, wake_generation=None):
                owner = (
                    getattr(self.infrastructure, "background", None)
                    if _name == "infrastructure_observe"
                    else self.media.background
                )
                if owner is not None and _name in owner.operations:
                    await owner.dispatch(_name, wake_generation=wake_generation)

            async def maintenance_work(_name=name, wake_generation=None):
                owner = (
                    getattr(self.infrastructure, "background", None)
                    if _name == "infrastructure_observe"
                    else self.media.background
                )
                if owner is not None and _name in owner.operations:
                    await owner.run(_name, wake_generation=wake_generation)

            if "capabilities.media." + name not in self.runtime.jobs.tasks:
                self.runtime.jobs.register("capabilities.media." + name, maintenance)
            if "capabilities.media.work." + name not in self.runtime.jobs.tasks:
                self.runtime.jobs.register("capabilities.media.work." + name, maintenance_work)

    def register_operations(self):
        from .plugins.control import OPERATIONS

        extras = {
            "plugins": tuple(OPERATIONS),
            "speech": (
                "connections.save",
                "connections.connect",
                "connections.complete",
                "connections.refresh",
                "connections.disconnect",
                "profiles.save",
                "dictionaries.save",
                "preview",
                "transcribe",
                "voices.search",
                "voices.clone",
            ),
            "images": ("engines.save", "workflows.import", "profiles.save"),
            "reactions": (
                "packs.import",
                "packs.update",
                "packs.export",
                "assets.batch",
                "assets.update",
                "assets.delete",
                "index",
                "annotate",
                "search",
            ),
            "content": ("discover", "continue", "providers.save"),
            "devices": ("connections.save", "refresh"),
            "capabilities": (
                "upload",
                "catalog/settings.save",
                "catalog/sources.save",
                "catalog/sources.refresh",
            ),
            "game": ("connections.save",),
            "weather": ("connections.save",),
            "resources": ("assets/connections.save", "archive/connections.save"),
        }
        for domain, operations in {**MATURE_OPERATIONS, **extras}.items():
            for op in operations:
                if (
                    op in READ_OPERATIONS.get(domain, set())
                    or domain == "plugins"
                    and op in {"invoke", "cancel", "reconcile", "resume"}
                ):
                    continue
                key = domain + "." + op.replace("/", ".")
                if key in self.catalog.handlers:
                    continue
                permission = (
                    "external.manage"
                    if domain in {"infrastructure", "weather", "resources"}
                    else "config.publish"
                    if domain == "capabilities"
                    and op.startswith("catalog/")
                    or domain in {"plugins", "reactions"}
                    and op != "search"
                    or op.endswith((".save", "/save"))
                    or op == "providers.save"
                    else None
                )
                self.catalog.register(Operation(self, domain, op, permission=permission))
        self.content.definition = replace(
            self.content.definition, model_tool={"name": "content_read"}
        )
        self.catalog.register(self.content)
        for identifier in ("content.discover", "content.continue"):
            handler = self.catalog.handlers[identifier]
            handler.definition = replace(
                handler.definition,
                model_tool={"name": identifier.replace(".", "_")},
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "mode": {"enum": ["research", "explore", "continue"]},
                        "sources": {
                            "type": "array",
                            "items": {"enum": ["web", "media", "bilibili"]},
                        },
                        "exclude_keys": {"type": "array", "items": {"type": "string"}},
                        "provider_id": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    },
                }
                if identifier == "content.discover"
                else {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "object",
                            "properties": {
                                "source_id": {"type": "string"},
                                "revision": {"type": "integer"},
                            },
                            "required": ["source_id", "revision"],
                        },
                        "requested": {"type": "object"},
                    },
                    "required": ["source"],
                },
            )
        from .media.jobs import Acquisition

        self.catalog.register(Acquisition(self))
        from .infrastructure.jobs import InfrastructureOperation

        self.catalog.register(InfrastructureOperation(self))

    async def prepare_handler(self, action):
        if action.capability_id.startswith("plugin:"):
            await self.plugins.refresh(action.context)

    async def discover(self, context):
        self.ready()
        await self.authority.check(context)
        return await self.catalog.discover(context)

    def ready(self):
        if not self.prepared:
            raise Fault("capabilities_starting", 503)

    async def operate(self, domain, operation, body, context, action=None, attempt=None):
        from .execution_context import CURRENT_CONTEXT

        token = CURRENT_CONTEXT.set(context)
        try:
            return await self._operate(domain, operation, body, context, action, attempt)
        finally:
            CURRENT_CONTEXT.reset(token)

    async def _operate(self, domain, operation, body, context, action=None, attempt=None):
        self.ready()
        await self.authority.check(context, action.source_refs if action else ())
        client_id = body.get("client_request_id") or (action.intent_key if action else None)
        clean = {k: v for k, v in body.items() if k != "client_request_id"}
        if operation in CLIENT_ID_OPERATIONS.get(domain, ()):
            clean["client_request_id"] = client_id
        if domain == "capabilities" and operation.startswith("catalog/"):
            if operation == "catalog/view":
                return await self.catalog_configuration.view(context)
            if operation == "catalog/settings.save":
                return await self.catalog_configuration.save_policy(context, clean)
            if operation == "catalog/sources.save":
                return await self.catalog_configuration.save_source(context, clean)
            if operation == "catalog/sources.refresh":
                return await self.catalog_configuration.refresh(context, clean, action, attempt)
        if domain == "projects":
            return await self.projects.read(operation, context, clean)
        if domain == "resources":
            if operation == "archive/view":
                return await self.archive.view(context)
            if operation == "archive/connections.save":
                return await self.archive.save(context, clean)
            if operation == "assets/view":
                return await self.resources.view(context)
            if operation == "assets/connections.save":
                return await self.resources.save(context, clean)
            if operation.startswith("assets/"):
                return await self.resources.read(context, operation.split("/", 1)[1], clean)
        if domain == "weather":
            if operation == "view":
                return await self.weather.view(context)
            if operation == "search":
                return await self.weather.search(context, clean["query"])
            if operation == "current":
                return await self.weather.observe(context, clean["location_id"])
            if operation == "connections.save":
                return await self.weather.save(context, clean)
        if domain == "plugins":
            if operation not in {"configure", "enable", "disable", "bindings", "upgrade"}:
                clean.pop("expected_revision", None)
            if operation in {"install", "create", "invoke", "resume"}:
                clean["client_request_id"] = client_id
            return await self.web_plugins.route(operation, clean, context)
        if domain in {"media", "books", "music", "comics"}:
            adapter = self.web_media if domain == "media" else getattr(self.web_media, domain)
            return await adapter.route(
                "/api/v1/media/" + operation if domain == "media" else operation, clean, context
            )
        if domain == "infrastructure":
            if operation in {"save", "control/plan", "control/action", "control/execute"}:
                clean["client_id"] = client_id
            return await self.web_infrastructure.route(
                "/api/v1/infrastructure/" + operation, clean, context
            )
        if domain == "capabilities" and operation == "upload":
            if context.origin_kind != OriginKind.HUMAN:
                raise Fault("human_upload_required", 403)
            source = SourceRef(**clean["source"])
            original = await self.sources.read(
                context, source, {"position": 0, "max_characters": 1}
            )
            if not original.get("media"):
                raise Fault("upload_original_missing", 409)
            media = MediaRef(**original["media"])
            inspection = (original.get("locator") or {}).get("media_inspection")
            missing = tuple(inspection.get("missing", ())) if inspection else ()
            artifact = await self.artifacts.record(
                context,
                media,
                source_refs=(source,),
                metadata={
                    "kind": "upload",
                    "filename": clean["filename"],
                    "media_inspection": inspection,
                },
            )
            return Receipt(
                str(uuid4()),
                action.action_id,
                attempt.attempt_id,
                "succeeded",
                "confirmed",
                "not_requested",
                artifacts=(artifact,),
                coverage=Coverage(
                    not missing and not (inspection or {}).get("error_code"),
                    missing=missing,
                    representation="original_metadata",
                ),
                result={
                    "artifact": wire(artifact),
                    "media": wire(media),
                    "source_ref": wire(source),
                    "media_inspection": inspection,
                },
            )
        if domain == "speech":
            return await self.speech_operation(operation, clean, context, action, attempt)
        if domain == "images":
            if operation == "view":
                return await self.images.view(context)
            if operation == "engines.save":
                return await self.images.save_engine(clean)
            if operation == "workflows.inspect":
                from .images.workflow import Workflow

                return Workflow.inspect(clean["graph"])
            if operation == "workflows.import":
                return await self.images.import_workflow(clean)
            if operation == "profiles.save":
                return await self.images.save_profile(clean, context)
            if operation == "preview":
                preview_action = action or Action(
                    str(uuid4()),
                    "preview",
                    "images.generate",
                    context.actor_id,
                    context,
                    clean,
                    context.origin_kind,
                )
                return await self.images.prepare(
                    preview_action,
                    attempt or Attempt(str(uuid4()), preview_action.action_id),
                    plan=False,
                )
            if operation in {"get", "cancel", "reconcile"}:
                return await getattr(self.actions, operation)(clean["action_id"], context)
            view = await self.images.view(context)
            if operation == "jobs":
                return {"items": view["jobs"], "next_cursor": None}
            if operation == "album":
                return {"items": view["album"], "next_cursor": None}
        if domain == "reactions":
            if operation == "view":
                return await self.reactions.view(
                    context, cursor=clean.get("cursor"), limit=clean.get("limit", 100)
                )
            if operation == "packs.import":
                return await self.reactions.import_pack(context, clean)
            if operation == "packs.update":
                return await self.reactions.update_pack(context, clean)
            if operation == "assets.batch":
                return await self.reactions.batch(context, clean)
            if operation == "packs.export":
                return {
                    "artifact": wire(await self.reactions.export_pack(context, clean["pack_id"]))
                }
            if operation == "assets.update":
                return await self.reactions.update(context, clean)
            if operation == "assets.delete":
                return await self.reactions.delete(context, clean)
            if operation == "index":
                return await self.reactions.index(context, clean)
            if operation == "annotate":
                return await self.reactions.annotate(context, clean)
            if operation == "search":
                from .dto import ReactionIntent

                return await self.reactions.search(
                    context,
                    ReactionIntent(
                        actor_id=context.actor_id,
                        scope_ids=context.scope_ids,
                        **{k: v for k, v in clean.items() if k not in {"actor_id", "scope_ids"}},
                    ),
                    source_refs=action.source_refs if action else (),
                )
        if domain == "content":
            if operation == "view":
                return {
                    "providers": await self.configuration.list("content_provider"),
                    "modes": ["research", "explore", "continue"],
                }
            if operation == "discover":
                return await self.content.discover(context, clean)
            if operation == "continue":
                if not clean.get("source"):
                    raise Fault("continuation_source_required", 400)
                return await self.sources.read(
                    context, SourceRef(**clean["source"]), clean.get("requested", {})
                )
            if operation == "providers.save":
                return await self.content.save_provider(context, clean)
        if domain == "devices":
            if operation == "view":
                return await self.devices.view(context)
            if operation == "connections.save":
                return await self.devices.save(context, clean)
            if operation == "refresh":
                return await self.devices.observe(context, clean.get("connection_id", "home"))
        if domain == "game" and operation == "connections.save":
            old = await self.configuration.get("game_connection", context.actor_id or "default")
            return await self.configuration.save(
                "game_connection",
                context.actor_id or "default",
                {
                    "base_url": clean["base_url"],
                    "configured": bool(clean.get("token") or old and old.get("configured")),
                    "enabled": clean.get("enabled", True),
                },
                clean.get("expected_revision", 0),
                secret={"token": clean["token"]} if clean.get("token") else None,
            )
        raise Fault("not_found", 404)

    async def speech_operation(self, operation, body, context, action, attempt):
        if operation == "view":
            return await self.speech.view(context)
        if operation == "connections.save":
            return await self.speech.save_connection(body)
        if operation == "profiles.save":
            return await self.speech.save_profile(body, context)
        if operation == "dictionaries.save":
            return await self.speech.save_dictionary(body)
        if operation == "connections.connect":
            return await self.speech.oauth.begin(
                context,
                body["connection_id"],
                body["redirect_uri"],
                client_id=body.get("oauth_client_id"),
            )
        if operation == "connections.complete":
            return await self.speech.oauth.complete(context, body["state"], body["code"])
        if operation == "connections.disconnect":
            current = await self.configuration.get("speech_connection", body["connection_id"])
            if not current:
                raise Fault("not_found", 404)
            return await self.configuration.save(
                "speech_connection",
                current["id"],
                {
                    **current,
                    "configured": False,
                    "state": "disconnected",
                    "reason": "oauth_required",
                },
                body["expected_revision"],
                secret={},
            )
        actual = replace(action, arguments=body)
        if operation == "preview":
            return await self.speech.execute(actual, attempt)
        if operation == "transcribe":
            return await self.speech.transcribe(actual, attempt)
        if operation == "voices.search":
            return await self.speech.voices(actual, attempt)
        if operation == "voices.clone":
            return await self.speech.clone(actual, attempt)
        if operation == "connections.refresh":
            return await self.speech.refresh(actual, attempt)
        raise Fault("not_found", 404)

    def bind_expression(self, expression):
        self.expression = expression
        if self.plugins is not None:
            self.plugins.expression = expression

    def bind_result_events(self, events):
        self.actions.events = events

    async def withdraw_sources(self, connection, source_refs, reason="source_revoked"):
        from .withdrawal import withdraw

        value = await withdraw(self, connection, source_refs, reason)
        await self.projects.withdraw(connection, source_refs, reason)
        return value

    async def close(self):
        # Cleanup also covers a prepared composition or partially failed initialize.
        try:
            await self.actions.close()
            if getattr(self, "plugin_host", None) is not None and self.worker:
                await self.plugin_host.close()
            if self.media is not None:
                await self.media.close()
            if self.infrastructure is not None:
                await self.infrastructure.close()
        finally:
            self.initialized = False
            self.prepared = False
            self.file_executor.shutdown(wait=True, cancel_futures=True)
