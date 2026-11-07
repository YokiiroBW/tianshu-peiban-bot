"""Media composition and lifecycle. The HTTP surface and workers share one ledger."""

import asyncio
import hashlib
import json
import uuid
from pathlib import Path

from jsonschema import Draft202012Validator

from ..contracts import Fault, require
from .accounts import Accounts
from .bilibili import Bilibili
from .servers import MediaServers
from .targets import Targets
from .config import SOURCE_KINDS, configuration
from .destination import DESTINATION_MODES, supports_subdirectory
from .engine import Engine
from .publication import Publisher, Remote, validate_connection
from .organizer import FileOrganizer, Publications
from .repository import Repository, TERMINAL
from .retention import Retention
from .storage import media_storage
from .storage_breakdown import StorageBreakdown
from .rules import RuleEvaluator
from .subscriptions import Subscriptions
from .worker import Worker
from .download_cache import DownloadCache
from .imports import Imports
from .creator_cache import CreatorCache
from .metadata_refresh import MetadataRefresh
from .import_metadata import ImportMetadata
from .music.runtime import Music
from .books.runtime import Books
from .comics.runtime import Comics
from .overview import RuntimeSample
from .background import Background

JOB_FIELDS = (
    "comic_id",
    "comic_chapter_id",
    "comic_library_kind",
    "comic_pages_done",
    "comic_pages_total",
    "comic_relative_path",
    "book_id",
    "book_artifact",
    "book_chapters_done",
    "book_chapters_total",
    "book_reused",
    "media_kind",
    "provider",
    "source_id",
    "audio_quality",
    "audio_receipt",
    "audio_reused",
    "audio_unlock",
    "audio_attempts",
    "audio_routing",
    "download_provider",
    "download_source_id",
    "recording_id",
    "job_id",
    "subscription_id",
    "media_key",
    "bvid",
    "cid",
    "selected_cids",
    "layout",
    "revision",
    "title",
    "creator",
    "state",
    "stage",
    "code",
    "progress",
    "created_at",
    "updated_at",
    "target_id",
    "destination_subdirectory",
    "destination_directory",
    "download_reused_cids",
    "quality",
    "actual_quality",
    "cancel_requested",
    "asset_receipt",
    "library_results",
)
RETRYABLE = frozenset({"failed", "auth_required", "unknown", "cancelled", "retry_wait"})


def public_job(job):
    return {
        **{key: job.get(key) for key in JOB_FIELDS},
        "destination_subdirectory": job.get("destination_subdirectory", ""),
        "download_reused_cids": job.get("download_reused_cids", []),
        "can_retry": job["state"] in RETRYABLE,
        "can_delete": job["state"] in TERMINAL,
        "can_cancel": job["state"]
        not in {"completed", "published", "cancelled", "failed", "unknown"},
    }


class PackageContract:
    def __init__(self, directory):
        root = Path(directory)
        try:
            manifest = json.loads((root / "manifest.json").read_bytes())
            require(manifest["package"] == "media-package/v1", "media_contract_unavailable", 503)
            for filename, digest in manifest["sha256"].items():
                require(
                    "/" not in filename and "\\" not in filename and filename != "..",
                    "media_contract_unavailable",
                    503,
                )
                raw = (root / filename).read_bytes().replace(b"\r\n", b"\n")
                require(
                    hashlib.sha256(raw).hexdigest() == digest, "media_contract_unavailable", 503
                )
            self.validator = Draft202012Validator(json.loads((root / "schema.json").read_bytes()))
        except (OSError, KeyError, ValueError):
            raise Fault("media_contract_unavailable", 503) from None

    def check(self, document):
        require(not list(self.validator.iter_errors(document)), "invalid_media_package", 503)


def validate_media(settings):
    config = configuration(settings)
    if config is None:
        return None
    for target in config["targets"].values():
        if target.get("kind") == "media_server":
            require(bool(target.get("servers")), "invalid_input", 400)
        require(isinstance(target.get("library_id"), str), "invalid_input", 400)
        if target.get("publisher"):
            validate_connection(target["publisher"])
        for server in target.get("servers", []):
            validate_connection(server)
            require(
                server.get("kind") in {"emby", "jellyfin", "plex"}
                and isinstance(server.get("server_id"), str)
                and isinstance(server.get("library_id"), str)
                and isinstance(server.get("media_root"), str),
                "invalid_input",
                400,
            )
    PackageContract(config["package_contract_directory"])
    return config


class Media:
    def __init__(self, settings, *, runtime=None, clock, local):
        self.config_error = None
        try:
            self.config = validate_media(settings)
        except (Fault, ValueError, KeyError, TypeError, OSError) as error:
            self.config = None
            self.config_error = getattr(error, "code", "media_configuration_invalid")
        self.local = local
        self.started = False
        self.background = Background(runtime, next_due=self._maintenance_due,
            owner=lambda: self.capabilities.actions.owners.id)
        self.previews = {}
        self.qr = {}
        self.wake = asyncio.Event()
        self.storage_runtime = runtime
        self.shared_cache = getattr(runtime,"cache",None)
        self.repo = Repository(
            settings["database_path"] + ".media", clock=clock, runtime=runtime
        )
        self.accounts = Accounts(self.repo, runtime.credentials)
        self.books = Books(self)
        from .comics.plugin import configuration as comic_file_configuration

        self.comic_files_error = None
        try:
            comic_files = comic_file_configuration(settings)
        except (Fault, ValueError, KeyError, TypeError, AttributeError) as error:
            comic_files = None
            self.comic_files_error = getattr(error, "code", "comic_files_configuration_invalid")
        self.comics = Comics(
            self, file_defaults=comic_files.get("defaults") if comic_files else None
        )
        self.worker = Worker(self)
        # Register durable task definitions in every process; only start()/activate()
        # in the worker may check engines or run maintenance.
        self.books.start_background(self.background)
        self.comics.start_background(self.background)
        if self.config is None:
            return
        self.contract = PackageContract(self.config["package_contract_directory"])
        self.music = Music(self)
        self.connector = Bilibili(clock=clock)
        self.engine = Engine(self.config["engine"])
        self.targets = Targets(self)
        self.remote = Remote(self.targets.token)
        self.publisher = Publications(
            Publisher(self.remote),
            FileOrganizer(self.repo, self.contract, self.config["staging_directory"]),
            self.local,
        )
        self.creator_cache = CreatorCache(
            self, Path(settings["database_path"]).with_suffix(".media-avatars")
        )
        self.servers = MediaServers(self.remote, self.creator_cache)
        self.evaluator = RuleEvaluator()
        self.subscriptions = Subscriptions(self)
        self.retention = Retention(self)
        self.download_cache = DownloadCache(self)
        self.imports = Imports(self)
        self.metadata_refresh = MetadataRefresh(self, public_job)
        self.import_metadata = ImportMetadata(
            self, Path(settings["database_path"]).with_suffix(".media-import-metadata")
        )
        self.storage_breakdown = StorageBreakdown(self, clock=clock)
        self.runtime_sample = RuntimeSample(clock=clock)
        self.background.start("subscriptions", self.subscriptions.pump)
        self.music.start_background(self.background)
        self.background.start("retention", self.retention.pump)

    async def _maintenance_due(self, name):
        return await self.local(self.repo.maintenance_due, name)

    async def _publish_engine(self):
        owner = self.capabilities.actions.owners.id
        async with self.storage_runtime.database.transaction() as connection:
            await connection.execute(
                "INSERT INTO capabilities.runtime_observations(name,owner_id,body) "
                "SELECT 'media.engine',id,%s::jsonb FROM capabilities.execution_owners "
                "WHERE id=%s AND expires_at>now() AND stopped_at IS NULL "
                "ON CONFLICT(name) DO UPDATE SET owner_id=excluded.owner_id,body=excluded.body,"
                "observed_at=now(),revision=capabilities.runtime_observations.revision+1 "
                "WHERE capabilities.runtime_observations.owner_id=excluded.owner_id OR NOT EXISTS "
                "(SELECT 1 FROM capabilities.execution_owners old WHERE old.id=capabilities.runtime_observations.owner_id "
                "AND old.expires_at>now() AND old.stopped_at IS NULL)",
                (json.dumps(dict(self.engine.status)), owner))

    async def _engine_observation(self):
        async with self.storage_runtime.database.transaction(read_only=True) as connection:
            row = await (await connection.execute(
                "SELECT r.*,o.expires_at>now() AND o.stopped_at IS NULL AS owner_live "
                "FROM capabilities.runtime_observations r JOIN capabilities.execution_owners o ON o.id=r.owner_id "
                "WHERE r.name='media.engine'")).fetchone()
        if not row:
            return {"state": "not_verified", "version": None, "code": "media_worker_observation_missing",
                    "owner_live": False, "observed_at": None}
        return {**row["body"], "state": row["body"]["state"] if row["owner_live"] else "not_verified",
                "code": row["body"].get("code") if row["owner_live"] else "media_worker_observation_stale",
                "observed_at": row["observed_at"], "generation": row["revision"], "owner_id": row["owner_id"],
                "owner_live": row["owner_live"], "stale": not row["owner_live"]}

    async def start(self):
        if self.started:
            return
        self.started = True
        if self.config is None:
            return
        self.storage_breakdown.closed = False
        await self.connector.start()
        await self.engine.check()
        await self._publish_engine()

    async def quiesce(self):
        # Existing loops settle their cancellation/lease receipts. Their clients
        # and ledger remain available to in-flight HTTP callbacks until close.
        await self.background.close()

    async def close(self):
        if self.config is None:
            await self.background.close()
            await self.books.close()
            await self.comics.close()
            self.started = False
            return
        await self.storage_breakdown.close()
        await self.background.close()
        await self.evaluator.aclose()
        await self.connector.close()
        await self.remote.close()
        await self.music.close()
        await self.books.close()
        await self.comics.close()
        self.started = False

    def healthy(self):
        return not self.started or self.background.healthy()

    async def view(self):
        if self.config is None:
            jobs, _ = await self.local(self.repo.jobs)
            return {
                "configured": False,
                "capabilities": {
                    "provider": "bilibili",
                    "source_kinds": list(SOURCE_KINDS),
                    "destination_modes": list(DESTINATION_MODES),
                },
                "engine": {
                    "state": "failed" if self.config_error else "not_configured",
                    "version": None,
                    "code": self.config_error or "media_not_configured",
                },
                "accounts": [],
                "targets": [],
                "subscriptions": [],
                "jobs": [public_job(job) for job in jobs],
                "overview": None,
            }
        from .accounts import public_account

        accounts = await self.local(self.repo.list, "account")
        subscriptions = await self.local(self.repo.list, "subscription")
        jobs, _ = await self.local(self.repo.jobs)
        overview = {
            "jobs": await self.local(self.repo.job_overview),
            "storage": self.storage_breakdown.view(
                await self.local(media_storage, dict(self.config["targets"]))
            ),
            **await self.local(self.repo.dashboard_details),
            "runtime": await self.local(self.runtime_sample.view),
        }
        overview["music"]["scan"] = await self.local(self.music.imports.view)
        music_settings = await self.local(self.repo.get, "music_settings", "library")
        overview["music_storage"] = await self.local(
            media_storage,
            {
                "music": {
                    "kind": "media_server",
                    "label": "音乐存档",
                    "directory": music_settings["directory"],
                }
            }
            if music_settings and music_settings.get("directory")
            else {},
        )
        return {
            "configured": True,
            "background": await self.background.snapshot(),
            "capabilities": {
                "provider": "bilibili",
                "source_kinds": list(SOURCE_KINDS),
                "destination_modes": list(DESTINATION_MODES),
            },
            "engine": await self._engine_observation(),
            "accounts": [public_account(account) for account in accounts],
            "targets": [
                {
                    "target_id": identity,
                    "label": target["label"],
                    "state": "ready"
                    if target.get("kind") == "media_server" or target.get("publisher")
                    else "publisher_not_configured",
                    "kind": target.get("kind", "assetlibrary"),
                    "destination_subdirectory_supported": supports_subdirectory(target),
                    "revision": target.get("revision"),
                    "directory": target.get("directory")
                    if target.get("kind") == "media_server"
                    else None,
                    "servers": [
                        {
                            "server_id": server["server_id"],
                            "label": server.get("label", server["server_id"]),
                            "kind": server["kind"],
                            "base_url": server["base_url"],
                            "library_id": server["library_id"],
                            "media_root": server["media_root"],
                        }
                        for server in target.get("servers", [])
                    ],
                }
                for identity, target in self.config["targets"].items()
            ],
            "subscriptions": [
                {
                    **{k:v for k,v in item.items() if k!="context"},
                    "destination_mode": item.get("destination_mode", "custom"),
                    "destination_subdirectory": item.get("destination_subdirectory", ""),
                }
                for item in subscriptions
            ],
            "jobs": [public_job(job) for job in jobs],
            "overview": overview,
        }

    async def resolve(self, value, cookie):
        resolved = await self.connector.resolve(value, cookie, with_formats=False)
        for part in resolved["video"]["parts"]:
            directory = Path(self.config["staging_directory"]) / ".probes" / str(uuid.uuid4())
            part["formats"] = await self.engine.formats(
                resolved["video"]["bvid"], part, cookie, directory
            )
        current = await self.connector.resolve(
            resolved["video"]["bvid"], cookie, with_formats=False
        )
        require(
            [(part["cid"], part["index"]) for part in current["snapshot"]["parts"]]
            == [(part["cid"], part["index"]) for part in resolved["snapshot"]["parts"]],
            "source_identity_changed",
            409,
        )
        return resolved

    def task_record(self, job):
        return {
            "task_id": "media:" + job["job_id"],
            "state": job["state"],
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
            "document": public_job(job),
        }

    def history(self, *, after=None, limit=50, states=None):
        if self.repo is None:
            return [], False
        jobs, more = self.repo.jobs(after=after, limit=limit, states=states)
        return [self.task_record(job) for job in jobs], more

    def record(self, identity):
        if self.repo is None:
            return None
        job = self.repo.job(identity)
        return self.task_record(job) if job else None
