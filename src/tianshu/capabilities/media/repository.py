"""Durable subscriptions, source membership and CAS-owned download jobs.

Only this module opens the media database. Transactions contain no network work.
Immutable recovery files are sealed before their references commit.
"""

import json
import psycopg
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

from ..contracts import Fault, digest, canonical, require
from .destination import head_key, same_destination, directory_identity
from .import_records import same_identity
from ..payload_files import MediaPayloads

TERMINAL = frozenset(
    {
        "completed",
        "published",
        "cancelled",
        "failed",
        "unknown",
        "auth_required",
        "waiting_metadata",
    }
)
OVERVIEW_STATES = {
    "published": ("published", "completed"),
    "processing": (
        "downloading",
        "validating",
        "metadata_ready",
        "publishing",
        "asset_indexed",
        "library_verifying",
    ),
    "queued": ("queued", "retry_wait"),
    "attention": ("failed", "unknown", "auth_required", "waiting_metadata"),
    "cancelled": ("cancelled",),
}
VISIBLE = "id NOT IN (SELECT id FROM documents WHERE kind='deleted_job')"
READ_BATCH_SIZE = 256
SUBSCRIPTIONS = frozenset(
    {"subscription", "book_subscription", "comic_subscription", "music_playlist", "book_feed"}
)


class Repository:
    def __init__(self, database, *, clock=time.time, runtime=None):
        from ..storage import DomainStore
        self.database = Path(database)
        self.runtime = runtime
        self.storage = DomainStore(runtime.database, "capability_media")
        self.payloads = MediaPayloads(runtime.files)
        self.clock = clock

    @contextmanager
    def transaction(self):
        with self.storage.transaction() as db:
            yield db

    def _document(self, db, kind, identity):
        row = db.execute(
            'SELECT body FROM documents WHERE kind=%s AND id=%s', (kind, identity)
        ).fetchone()
        return self.payloads.read(kind, json.loads(row[0])) if row else None

    def _documents(self, db, kind, identities):
        """Fetch a transaction's selected documents without one round trip per identity."""
        identities = list(dict.fromkeys(identities))
        result = {}
        for start in range(0, len(identities), READ_BATCH_SIZE):
            batch = identities[start : start + READ_BATCH_SIZE]
            placeholders = ",".join('%s' for _ in batch)
            rows = db.execute(
                f"SELECT id,body FROM documents WHERE kind=%s AND id IN ({placeholders})",
                (kind, *batch),
            )
            result.update((row[0], self.payloads.read(kind, json.loads(row[1]))) for row in rows)
        return result

    def _jobs(self, db, identities, *, include_deleted=False):
        """Selected jobs in the caller's transaction, with the usual visibility predicate."""
        identities = list(dict.fromkeys(identities))
        result = {}
        for start in range(0, len(identities), READ_BATCH_SIZE):
            batch = identities[start : start + READ_BATCH_SIZE]
            placeholders = ",".join('%s' for _ in batch)
            rows = db.execute(
                f"SELECT id,body FROM jobs WHERE id IN ({placeholders})"
                + ("" if include_deleted else " AND " + VISIBLE),
                batch,
            )
            result.update((row[0], json.loads(row[1])) for row in rows)
        return result

    def get(self, kind, identity):
        with self.transaction() as db:
            return self._document(db, kind, identity)

    def list(self, kind):
        with self.transaction() as db:
            rows = [
                self.payloads.read(kind, json.loads(row[0]))
                for row in db.execute(
                    'SELECT body FROM documents WHERE kind=%s ORDER BY id', (kind,)
                )
            ]
            # Keep retired snapshots for job references and monotonically increasing revisions.
            return [
                row for row in rows if kind not in SUBSCRIPTIONS or row.get("state") != "removed"
            ]

    def maintenance_due(self, name):
        from .scheduling import SCAN_TASKS

        kind = next((kind for kind, task in SCAN_TASKS.items() if task == name), None)
        with self.transaction() as db:
            if kind:
                row = db.execute("SELECT min(coalesce((body::jsonb->>'next_scan_at')::double precision,0)) AS due "
                    "FROM documents WHERE kind=%s AND body::jsonb->>'state'='active'", (kind,)).fetchone()
                return {"known": True, "due": row["due"]}
            if name == "book_audio":
                row = db.execute("SELECT min(coalesce((j.body::jsonb->>'next_at')::double precision,0)) AS due FROM documents j "
                    "WHERE j.kind='book_audio_job' AND j.body::jsonb->>'state' IN ('queued','waiting') "
                    "AND NOT EXISTS(SELECT 1 FROM documents p WHERE p.kind='book_audio_plan' "
                    "AND p.id=j.body::jsonb->>'book_id' AND (p.body::jsonb->>'paused')::boolean IS TRUE)").fetchone()
                return {"known": True, "due": row["due"]}
            timed = {"comic_extensions": ("comic_extension_policy", "next_check_at"),
                     "music_imports": ("music_import_scan", "next_scan_at"),
                     "music_navidrome": ("music_navidrome_playlist", "next_sync_at"),
                     "music_maintenance": ("music_quality_check", "next_check_at"),
                     "music_lyrics": ("music_lyrics", "next_check_at")}
            if name in timed:
                kind, field = timed[name]
                row = db.execute("SELECT min(coalesce((body::jsonb->>%s)::double precision,0)) AS due FROM documents "
                    "WHERE kind=%s AND (body::jsonb->>'enabled')::boolean IS DISTINCT FROM false",
                    (field, kind)).fetchone()
                return {"known": True, "due": row["due"]}
        return {"known": False}

    def document_page(self, kind, *, limit=64, after_id=None):
        """One bounded owner page; a filtered empty page still advances its cursor."""
        require(kind in {"book", "comic", "music_source", "subscription"}, "invalid_input", 400)
        require(
            type(limit) is int
            and 1 <= limit <= 128
            and (after_id is None or isinstance(after_id, str) and len(after_id) <= 256),
            "invalid_input",
            400,
        )
        with self.transaction() as db:
            rows = db.execute(
                'SELECT id,body FROM documents WHERE kind=%s AND id>%s ORDER BY id LIMIT %s',
                (kind, after_id or "", limit + 1),
            ).fetchall()
            page = rows[:limit]
            documents = [self.payloads.read(kind, json.loads(row[1])) for row in page]
            return (
                [
                    row
                    for row in documents
                    if kind not in SUBSCRIPTIONS or row.get("state") != "removed"
                ],
                page[-1][0] if len(rows) > limit else None,
            )

    def member_page(self, subscription_id, *, limit=64, after=None):
        """Recorded membership only; an upload without known parts has cid=None."""
        require(
            isinstance(subscription_id, str)
            and 0 < len(subscription_id) <= 128
            and type(limit) is int
            and 1 <= limit <= 128
            and (
                after is None
                or isinstance(after, (tuple, list))
                and len(after) == 2
                and all(isinstance(value, str) and len(value) <= 128 for value in after)
            ),
            "invalid_input",
            400,
        )
        bvid, cid = after or ("", "")
        with self.transaction() as db:
            rows = db.execute(
                "SELECT m.bvid,p.cid FROM members m LEFT JOIN member_parts p ON p.subscription_id=m.subscription_id AND p.bvid=m.bvid WHERE m.subscription_id=%s AND (m.bvid>%s OR (m.bvid=%s AND coalesce(p.cid,'')>%s)) ORDER BY m.bvid,coalesce(p.cid,'') LIMIT %s",
                (subscription_id, bvid, bvid, cid, limit + 1),
            ).fetchall()
        page = rows[:limit]
        return (
            [{"bvid": row[0], "cid": row[1]} for row in page],
            (page[-1][0], page[-1][1] or "") if len(rows) > limit else None,
        )

    def remove_subscription(self, kind, identity, expected, scan_id, *, db, changes=None):
        require(kind in SUBSCRIPTIONS, "invalid_input", 400)
        require(type(expected) is int and expected > 0, "invalid_input", 400)
        current = self._document(db, kind, identity)
        require(current and current.get("state") != "removed", "not_found", 404)
        result = self.put(
            kind,
            identity,
            {**current, **(changes or {}), "state": "removed", "code": "subscription_removed"},
            expected,
            db=db,
        )
        # A scan that already fetched a remote catalogue must not commit after removal.
        db.execute('DELETE FROM scan_leases WHERE id=%s', (scan_id,))
        return result

    def put(self, kind, identity, body, expected=None, *, db=None):
        if db is None:
            with self.transaction() as transaction:
                return self.put(kind, identity, body, expected, db=transaction)
        current = self._document(db, kind, identity)
        revision = current.get("revision", 0) if current else 0
        require(expected is None or revision == expected, "version_conflict", 409)
        from ..execution_context import CURRENT_ACTION
        from tianshu.runtime.serialization import wire
        action=CURRENT_ACTION.get()
        if kind in SUBSCRIPTIONS and "context" not in body:
            if action:body={**body,"context":wire(action.context),"source_refs":wire(action.source_refs)}
            elif current and current.get("context"):body={**body,"context":current["context"],"source_refs":current.get("source_refs",[])}
        result = {**body, "revision": revision + 1}
        db.execute(
            'INSERT INTO documents VALUES(%s,%s,%s,%s) ON CONFLICT(kind,id) DO UPDATE SET revision=excluded.revision,body=excluded.body',
            (
                kind,
                identity,
                result["revision"],
                json.dumps(self.payloads.store(kind, result), ensure_ascii=False),
            ),
        )
        from .scheduling import wake_document

        wake_document(self, db, kind, result)
        return result

    def enqueue(self, payload, *, db=None):
        if db is None:
            with self.transaction() as transaction:
                return self.enqueue(payload, db=transaction)
        if payload.get("media_kind") == "audio":
            return self.insert_job(payload, "audio:" + payload["audio_key"], db)
        if payload.get("media_kind") == "book":
            return self.insert_job(payload, "book:" + payload["book_key"], db)
        # The job is one complete selected-P package, with per-CID membership retained inside it.
        for existing in db.execute(
            'SELECT body FROM jobs WHERE ' + VISIBLE + " AND state NOT IN ('cancelled','failed')"
        ):
            prior = json.loads(existing[0])
            if (
                prior.get("media_kind") not in {"audio", "book", "comic"}
                and prior["bvid"] == payload["bvid"]
                and same_destination(prior, payload)
                and prior["quality"] == payload["quality"]
                and prior.get("layout") == payload.get("layout")
                and set(payload["selected_cids"]) <= set(prior["selected_cids"])
            ):
                return prior
        dedupe_input = {
            "bvid": payload["bvid"],
            "selected_cids": sorted(payload["selected_cids"]),
            "target_id": payload["target_id"],
            "quality": payload["quality"],
            "layout": payload.get("layout"),
            "edition": payload.get("edition", 0),
        }
        if payload.get("destination_subdirectory"):
            dedupe_input["destination_subdirectory"] = directory_identity(payload)
        dedupe = digest(canonical(dedupe_input))
        return self.insert_job(payload, dedupe, db)

    @staticmethod
    def _job_execution_lane(job):
        from tianshu.runtime.execution import ExecutionLane

        return (ExecutionLane.MUSIC_LIBRARY
            if job.get("media_kind") == "audio" and job.get("audio_operation") in {"scan", "import"}
            else ExecutionLane.DOWNLOADS)

    async def job_execution_lane_on(self, connection, identity):
        """Route an original job using only its persisted domain metadata."""
        row = await (await connection.execute(
            "SELECT body::jsonb->>'media_kind' AS media_kind,"
            "body::jsonb->>'audio_operation' AS audio_operation "
            "FROM capability_media.jobs WHERE id=%s", (identity,))).fetchone()
        return self._job_execution_lane(row or {})

    def schedule_job(self,db,job):
        from datetime import datetime,UTC,timedelta
        from tianshu.runtime.jobs import JobSpec
        due=datetime.now(UTC)+timedelta(seconds=job.get("retry_delay",30) if job["state"]=="retry_wait" else 0)
        key="media-acquire:"+job["job_id"]+":"+str(job.get("generation",0))+":"+str(job.get("revision",1))
        self.runtime.jobs.enqueue_sync(db,JobSpec("capabilities.media_job",key,
            {"job_id":job["job_id"],"generation":job.get("generation",0),"revision":job.get("revision",1)},
            scheduled_at=due, execution_lane=self._job_execution_lane(job)))

    def insert_job(self, payload, dedupe, db):
        row = db.execute('SELECT body FROM jobs WHERE dedupe=%s', (dedupe,)).fetchone()
        if row:
            return json.loads(row[0])
        from ..execution_context import CURRENT_ACTION
        from tianshu.runtime.serialization import wire
        action=CURRENT_ACTION.get()
        if "context" not in payload:
            require(action is not None,"media_context_required",403)
            payload={**payload,"context":wire(action.context),"source_refs":wire(action.source_refs),"parent_action_id":action.action_id}
        now = self.clock()
        body = {
            **payload,
            "job_id": str(uuid.uuid4()),
            "state": "queued",
            "stage": "queued",
            "code": "queued",
            "progress": None,
            "created_at": now,
            "updated_at": now,
            "actual_quality": None,
            "cancel_requested": False,
            "asset_receipt": None,
            "library_results": [],
            "generation": 0,
            "revision": 1,
        }
        db.execute(
            'INSERT INTO jobs(id,dedupe,state,created,updated,body) VALUES(%s,%s,%s,%s,%s,%s)',
            (body["job_id"], dedupe, "queued", now, now, json.dumps(body, ensure_ascii=False)),
        )
        db.execute('INSERT INTO events VALUES(%s,%s,%s,%s)', (body["job_id"], "queued", "queued", now))
        self.schedule_job(db,body)
        return body

    def job(self, identity, *, include_deleted=False):
        with self.transaction() as db:
            row = db.execute(
                'SELECT body FROM jobs WHERE id=%s' + ("" if include_deleted else " AND " + VISIBLE),
                (identity,),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def delete_job(self, identity, revision, *, db):
        row = db.execute(
            'SELECT body,owner FROM jobs WHERE id=%s AND ' + VISIBLE, (identity,)
        ).fetchone()
        require(row is not None, "not_found", 404)
        job = json.loads(row["body"])
        require(job["revision"] == revision, "version_conflict", 409)
        require(job["state"] in TERMINAL and row["owner"] is None, "job_not_deletable", 409)
        self.put("deleted_job", identity, {"deleted_at": self.clock()}, 0, db=db)
        # Retain publication bases and membership, while freeing the command's unique key.
        db.execute('UPDATE jobs SET dedupe=%s WHERE id=%s', ("deleted:" + identity, identity))
        return {"deleted": True, "job_id": identity}

    def jobs(self, *, limit=100, after=None, states=None):
        where, parameters = [VISIBLE], []
        if states is not None:
            if not states:
                return [], False
            where.append("state IN (" + ",".join('%s' for _ in states) + ")")
            parameters.extend(sorted(states))
        if after is not None:
            identity = after[1].removeprefix("media:")
            where.append("(CAST(created AS INTEGER)<%s OR (CAST(created AS INTEGER)=%s AND id<%s))")
            parameters.extend((after[0], after[0], identity))
        sql = (
            'SELECT body FROM jobs'
            + (' WHERE ' + " AND ".join(where) if where else "")
            + ' ORDER BY CAST(created AS INTEGER) DESC,id DESC LIMIT %s'
        )
        parameters.append(limit + 1)
        with self.transaction() as db:
            rows = db.execute(sql, parameters).fetchall()
        found = [json.loads(row[0]) for row in rows]
        return found[:limit], len(found) > limit

    def job_count(self):
        with self.transaction() as db:
            return db.execute('SELECT COUNT(*) FROM jobs WHERE ' + VISIBLE).fetchone()[0]

    def job_overview(self):
        """Count the whole ledger in SQL without loading any job documents."""
        with self.transaction() as db:
            counts = dict(
                db.execute('SELECT state, COUNT(*) FROM jobs WHERE ' + VISIBLE + " GROUP BY state")
            )
        return {
            "total": sum(counts.values()),
            **{
                category: sum(counts.get(state, 0) for state in states)
                for category, states in OVERVIEW_STATES.items()
            },
        }

    def dashboard_details(self):
        """Bounded recent lists per medium and aggregate playlist facts, never credentials."""
        started = time.monotonic()
        with self.transaction() as db:
            music = dict(
                db.execute("\n                SELECT count(*) AS total,\n                  coalesce(sum(CASE WHEN (body::jsonb #>> '{state}')='active' THEN 1 ELSE 0 END),0) AS active,\n                  coalesce(sum(CASE WHEN (body::jsonb #>> '{state}')='paused' THEN 1 ELSE 0 END),0) AS paused,\n                  coalesce(sum(CASE WHEN coalesce((body::jsonb #>> '{code}'),'ready') NOT IN ('','ready') THEN 1 ELSE 0 END),0) AS attention,\n                  coalesce(sum(CASE WHEN (body::jsonb #>> '{auto_download}')::boolean IS TRUE THEN 1 ELSE 0 END),0) AS auto_download,\n                  coalesce(sum(CAST((body::jsonb #>> '{member_count}') AS INTEGER)),0) AS members\n                FROM documents WHERE kind='music_playlist'\n            ").fetchone()
            )
            music["files"] = db.execute("\n                SELECT count(*) FROM documents f JOIN documents r ON r.id=f.id\n                AND r.kind='music_recording' WHERE f.kind='music_library_file'\n                AND (r.body::jsonb #>> '{merged_into}') IS NULL\n                AND NOT coalesce((f.body::jsonb #>> '{missing}')::boolean,false)\n            ").fetchone()[0]
            books = dict(
                db.execute("\n                SELECT count(*) AS total,\n                  coalesce(sum(CASE WHEN (body::jsonb #>> '{state}')='active' THEN 1 ELSE 0 END),0) AS active,\n                  coalesce(sum(CASE WHEN (body::jsonb #>> '{state}')='paused' THEN 1 ELSE 0 END),0) AS paused,\n                  coalesce(sum(CASE WHEN coalesce((body::jsonb #>> '{code}'),'ready') NOT IN ('','ready') THEN 1 ELSE 0 END),0) AS attention\n                FROM documents WHERE kind='book_subscription'\n            ").fetchone()
            )
            books["files"] = db.execute(
                "SELECT count(*) FROM documents WHERE kind='book' AND (body::jsonb #>> '{artifact,relative_path}') IS NOT NULL"
            ).fetchone()[0]
            recent = {}
            for kind in ("video", "audio", "book"):
                rows = db.execute(
                    'SELECT body FROM jobs WHERE ' + VISIBLE + " AND coalesce((body::jsonb #>> '{media_kind}'),'video')=%s ORDER BY updated DESC,id DESC LIMIT 5",
                    (kind,),
                ).fetchall()
                recent[kind] = [
                    {
                        key: row.get(key)
                        for key in (
                            "job_id",
                            "title",
                            "creator",
                            "state",
                            "updated_at",
                            "media_kind",
                        )
                    }
                    for row in (json.loads(value[0]) for value in rows)
                ]
            schema = 1
            size = db.execute("SELECT COALESCE(sum(pg_total_relation_size(quote_ident(schemaname)||'.'||quote_ident(tablename))),0)::bigint FROM pg_tables WHERE schemaname=current_schema()").fetchone()[0]
        return {
            "music": music,
            "books": books,
            "recent": recent,
            "database": {
                "state": "ready",
                "engine": "PostgreSQL",
                "schema_version": schema,
                "query_ms": round((time.monotonic() - started) * 1000, 1),
                "size_bytes": size,
                "size_scope": "schema",
                "size_scope_name": "capability_media",
            },
        }

    def events(self, identity):
        with self.transaction() as db:
            return [
                dict(row)
                for row in db.execute(
                    'SELECT state,code,at FROM events WHERE job_id=%s ORDER BY at LIMIT 500',
                    (identity,),
                )
            ]

    def claim(self, owner, lease_seconds=30, *, kinds=None,job_id=None):
        now = self.clock()
        with self.transaction() as db:
            kind_clause = ""
            kind_values = ()
            if kinds is not None:
                require(
                    kinds and set(kinds) <= {"video", "audio", "book", "comic"},
                    "invalid_input",
                    400,
                )
                kind_values = tuple(kinds)
                kind_clause = (
                    " AND coalesce((body::jsonb #>> '{media_kind}'),'video') IN ("
                    + ",".join('%s' for _ in kinds)
                    + ")"
                )
            if job_id is not None:
                kind_clause+=" AND id=%s"
                kind_values=(*kind_values,job_id)
            rows = db.execute(
                "SELECT id,state,generation,body FROM jobs WHERE (state='queued' OR (state='retry_wait' AND lease<%s) OR (owner IS NOT NULL AND lease<%s)) AND state NOT IN ('completed','published','cancelled','failed','unknown','auth_required','waiting_metadata')"
                + kind_clause
                + " ORDER BY (owner IS NOT NULL AND lease<%s) DESC, CASE WHEN (body::jsonb #>> '{quality_review}')::boolean IS TRUE AND NOT coalesce((body::jsonb #>> '{quality_catchup}')::boolean,false) THEN 1 ELSE 0 END,created LIMIT 500",
                (now, now, *kind_values, now),
            ).fetchall()
            occupied = [
                json.loads(value[0])
                for value in db.execute(
                    'SELECT body FROM jobs WHERE owner IS NOT NULL AND lease>=%s', (now,)
                )
            ]
            row = next(
                (
                    value
                    for value in rows
                    if not any(
                        peer["job_id"] != value["id"]
                        and self.jobs_overlap(peer, json.loads(value["body"]))
                        for peer in occupied
                    )
                ),
                None,
            )
            if row is None:
                return None
            body = json.loads(row["body"])
            body["generation"] = row["generation"] + 1
            # A lost publication response is reconciled through its stable publisher key.
            body["state"] = "publishing" if body.get("package") else "downloading"
            body["stage"] = body["state"]
            body["updated_at"] = now
            db.execute(
                'UPDATE jobs SET state=%s,generation=%s,owner=%s,lease=%s,updated=%s,body=%s WHERE id=%s',
                (
                    body["state"],
                    body["generation"],
                    owner,
                    now + lease_seconds,
                    now,
                    json.dumps(body, ensure_ascii=False),
                    body["job_id"],
                ),
            )
            return body

    @staticmethod
    def jobs_overlap(left, right):
        if left.get("media_kind") == "comic" or right.get("media_kind") == "comic":
            return (
                left.get("media_kind") == right.get("media_kind") == "comic"
                and left["comic_chapter_id"] == right["comic_chapter_id"]
                and left["comic_target"] == right["comic_target"]
            )
        if left.get("media_kind") == "book" or right.get("media_kind") == "book":
            return (
                left.get("media_kind") == right.get("media_kind") == "book"
                and left["book_id"] == right["book_id"]
                and left["destination_directory"] == right["destination_directory"]
            )
        if left.get("media_kind") == "audio" or right.get("media_kind") == "audio":
            return (
                left.get("media_kind") == right.get("media_kind") == "audio"
                and (
                    left.get("recording_id")
                    and left.get("recording_id") == right.get("recording_id")
                    or (
                        left.get("provider") == right.get("provider")
                        and left["source_id"] == right["source_id"]
                    )
                )
                and left["destination_directory"] == right["destination_directory"]
            )
        return left["bvid"] == right["bvid"] and same_destination(left, right)

    def update_job(self, identity, changes, *, owner=None, generation=None, db=None):
        if db is None:
            with self.transaction() as transaction:
                return self.update_job(
                    identity, changes, owner=owner, generation=generation, db=transaction
                )
        row = db.execute(
            'SELECT body,owner,generation FROM jobs WHERE id=%s', (identity,)
        ).fetchone()
        require(row is not None, "not_found", 404)
        if owner is not None:
            require(
                row["owner"] == owner and row["generation"] == generation, "lease_conflict", 409
            )
        elif generation is not None:
            require(row["generation"] == generation, "lease_conflict", 409)
        previous = json.loads(row["body"])
        if owner is not None and changes.get("state") not in {"cancelled", "unknown"}:
            require(not previous["cancel_requested"], "job_cancelled", 409)
        body = {
            **previous,
            **changes,
            "updated_at": self.clock(),
            "revision": previous.get("revision", 1) + 1,
        }
        ended = body["state"] in TERMINAL
        db.execute(
            'UPDATE jobs SET state=%s,updated=%s,body=%s,owner=%s,lease=%s WHERE id=%s',
            (
                body["state"],
                body["updated_at"],
                json.dumps(body, ensure_ascii=False),
                None if ended or body["state"] in {"retry_wait", "queued"} else row["owner"],
                0
                if ended
                else self.clock()
                + (body.get("retry_delay", 30) if body["state"] == "retry_wait" else 30),
                identity,
            ),
        )
        if changes.get("state") in {"queued","retry_wait"}:self.schedule_job(db,body)
        if "state" in changes:
            db.execute(
                'INSERT INTO events VALUES(%s,%s,%s,%s)',
                (identity, body["state"], body["code"], body["updated_at"]),
            )
        if (
            changes.get("state") == "asset_indexed"
            and body.get("asset_receipt")
            and body.get("media_kind") != "comic"
        ):
            self.put(
                "target_head",
                head_key(body),
                {"job_id": identity, "operation_id": body["asset_receipt"]["operation_id"]},
                db=db,
            )
        return body

    def heartbeat(self, identity, owner, generation):
        with self.transaction() as db:
            row = db.execute(
                'SELECT body FROM jobs WHERE id=%s AND owner=%s AND generation=%s',
                (identity, owner, generation),
            ).fetchone()
            require(row is not None, "lease_conflict", 409)
            body = json.loads(row[0])
            require(not body["cancel_requested"], "job_cancelled", 409)
            db.execute('UPDATE jobs SET lease=%s WHERE id=%s', (self.clock() + 30, identity))

    def require_scan(self, db, identity, owner, generation):
        row = db.execute(
            'SELECT owner,generation,expires FROM scan_leases WHERE id=%s', (identity,)
        ).fetchone()
        require(
            row is not None
            and row["owner"] == owner
            and row["generation"] == generation
            and row["expires"] >= self.clock(),
            "lease_conflict",
            409,
        )

    def scan_claim(self, identity, owner):
        with self.transaction() as db:
            row = db.execute(
                'SELECT generation,expires FROM scan_leases WHERE id=%s', (identity,)
            ).fetchone()
            require(row is None or row["expires"] < self.clock(), "scan_in_progress", 409)
            generation = row["generation"] + 1 if row else 1
            db.execute(
                'INSERT INTO scan_leases VALUES(%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET owner=excluded.owner,generation=excluded.generation,expires=excluded.expires',
                (identity, owner, generation, self.clock() + 30),
            )
            return generation

    def scan_heartbeat(self, identity, owner, generation):
        with self.transaction() as db:
            changed = db.execute(
                'UPDATE scan_leases SET expires=%s WHERE id=%s AND owner=%s AND generation=%s',
                (self.clock() + 30, identity, owner, generation),
            ).rowcount
            require(changed == 1, "lease_conflict", 409)

    def scan_release(self, identity, owner, generation):
        with self.transaction() as db:
            db.execute(
                'DELETE FROM scan_leases WHERE id=%s AND owner=%s AND generation=%s',
                (identity, owner, generation),
            )

    def complete_scan(
        self, subscription, members, jobs, owner=None, generation=None, part_members=None
    ):
        with self.transaction() as db:
            if owner is not None:
                self.require_scan(db, subscription["subscription_id"], owner, generation)
            current = self._document(db, "subscription", subscription["subscription_id"])
            require(
                current is not None
                and current["revision"] == subscription["revision"]
                and current["state"] == "active",
                "version_conflict",
                409,
            )
            # Membership is accumulated: deleting and later re-adding a member does not download twice.
            db.executemany(
                'INSERT INTO members VALUES(%s,%s) ON CONFLICT DO NOTHING',
                ((subscription["subscription_id"], bvid) for bvid in members),
            )
            before = db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
            for job in jobs:
                self.enqueue(job, db=db)
            queued = db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] - before
            if part_members:
                db.executemany(
                    'INSERT INTO member_parts VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',
                    (
                        (subscription["subscription_id"], bvid, cid)
                        for bvid, cids in part_members.items()
                        for cid in cids
                    ),
                )
            count = db.execute(
                'SELECT COUNT(*) FROM members WHERE subscription_id=%s',
                (subscription["subscription_id"],),
            ).fetchone()[0]
            body = {
                **current,
                "baseline_ready": True,
                "last_scan_at": self.clock(),
                "next_scan_at": self.clock() + current["interval_seconds"],
                "code": "scan_complete",
                "counts": {"members": count, "queued": current["counts"]["queued"] + queued},
                "quality_reassessment": False,
                "destination_reassessment": False,
            }
            # Scanning facts do not invalidate the user's configuration revision.
            db.execute(
                "UPDATE documents SET body=%s WHERE kind='subscription' AND id=%s",
                (json.dumps(body, ensure_ascii=False), subscription["subscription_id"]),
            )
            return body, queued

    def download_claim(self, bvid, cid, quality, owner):
        """Claim one resolved quality across workers/processes; ready records are only candidates."""
        with self.transaction() as db:
            key = (bvid, cid, quality)
            row = db.execute(
                'SELECT * FROM download_sources WHERE bvid=%s AND cid=%s AND quality=%s', key
            ).fetchone()
            if row and row["state"] == "ready":
                return {
                    "state": "ready",
                    "generation": row["generation"],
                    "download": json.loads(row["body"]),
                }
            if row and row["owner"] and row["lease"] >= self.clock():
                return {"state": "waiting"}
            generation = row["generation"] + 1 if row else 1
            db.execute(
                "INSERT INTO download_sources VALUES(%s,%s,%s,'downloading',%s,%s,%s,NULL) ON CONFLICT(bvid,cid,quality) DO UPDATE SET state='downloading',owner=excluded.owner,generation=excluded.generation,lease=excluded.lease,body=NULL",
                (*key, owner, generation, self.clock() + 30),
            )
            return {"state": "owned", "generation": generation}

    def download_invalidate(self, bvid, cid, quality, generation):
        with self.transaction() as db:
            db.execute(
                "UPDATE download_sources SET state='missing',owner=NULL,lease=0,body=NULL WHERE bvid=%s AND cid=%s AND quality=%s AND generation=%s AND state='ready'",
                (bvid, cid, quality, generation),
            )

    def download_heartbeat(self, bvid, cid, quality, owner, generation):
        with self.transaction() as db:
            changed = db.execute(
                "UPDATE download_sources SET lease=%s WHERE bvid=%s AND cid=%s AND quality=%s AND owner=%s AND generation=%s AND state='downloading'",
                (self.clock() + 30, bvid, cid, quality, owner, generation),
            ).rowcount
            require(changed == 1, "lease_conflict", 409)

    def download_finish(self, bvid, cid, quality, owner, generation, download=None):
        with self.transaction() as db:
            changed = db.execute(
                "UPDATE download_sources SET state=%s,owner=NULL,lease=0,body=%s WHERE bvid=%s AND cid=%s AND quality=%s AND owner=%s AND generation=%s AND state='downloading'",
                (
                    "ready" if download else "missing",
                    json.dumps(download) if download else None,
                    bvid,
                    cid,
                    quality,
                    owner,
                    generation,
                ),
            ).rowcount
            require(changed == 1, "lease_conflict", 409)

    def cached_downloads(self, bvid, cid):
        with self.transaction() as db:
            rows = db.execute(
                "SELECT body FROM jobs WHERE (body::jsonb #>> '{bvid}')=%s ORDER BY updated DESC",
                (bvid,),
            ).fetchall()
        return [
            *self.download_variants(bvid, cid),
            *[
                body["downloads"][cid]
                for row in rows
                if cid in (body := json.loads(row[0])).get("downloads", {})
            ],
        ]

    def remember_download(self, bvid, cid, quality, download):
        self.put(
            "download_variant",
            digest({"bvid": bvid, "cid": cid, "quality": quality, "sha256": download["sha256"]}),
            {"bvid": bvid, "cid": cid, "actual_quality": quality, "download": download},
        )

    def download_variants(self, bvid, cid):
        with self.transaction() as db:
            rows = db.execute(
                "SELECT body FROM documents WHERE kind='download_variant' AND (body::jsonb #>> '{bvid}')=%s AND (body::jsonb #>> '{cid}')=%s ORDER BY revision DESC,id DESC",
                (bvid, cid),
            ).fetchall()
        return [json.loads(row[0])["download"] for row in rows]

    def forget_download(self, bvid, cid, quality, sha256):
        with self.transaction() as db:
            db.execute(
                "DELETE FROM documents WHERE kind='download_variant' AND id=%s",
                (digest({"bvid": bvid, "cid": cid, "quality": quality, "sha256": sha256}),),
            )

    def refresh_metadata(self, identity, revision, resolved, *, db):
        row = db.execute('SELECT body FROM jobs WHERE id=%s AND ' + VISIBLE, (identity,)).fetchone()
        current = json.loads(row[0]) if row else None
        require(current and current["revision"] == revision, "version_conflict", 409)
        self.put(
            "snapshot_history",
            str(uuid.uuid4()),
            {"job_id": identity, "snapshot": current["snapshot"], "recorded_at": self.clock()},
            db=db,
        )
        changes = {
            "snapshot": resolved["snapshot"],
            "cover_url": resolved["video"]["cover_url"],
            "creator_images": resolved.get("creator_images", []),
            "title": current.get("overrides", {}).get("title", resolved["video"]["title"]),
            "creator": resolved["video"]["creator"]["name"],
            "state": "queued",
            "stage": "metadata",
            "code": "source_metadata_refreshed",
            "package": None,
            "asset_operation": None,
            "metadata_changed": True,
            "edition": current.get("edition", 0) + 1,
            "attempts": 0,
            "library_attempts": 0,
        }
        if current.get("asset_receipt"):
            changes["base_edition"] = {
                key: current.get(key)
                for key in (
                    "package",
                    "asset_operation",
                    "asset_receipt",
                    "selected_cids",
                    "downloads",
                    "snapshot",
                    "quality",
                    "bvid",
                    "target_id",
                    "destination_subdirectory",
                    "destination_directory",
                    "job_id",
                )
            }
        return self.update_job(identity, changes, db=db)

    def media_sources(self, bvid, cid, quality):
        with self.transaction() as db:
            rows = db.execute(
                "SELECT body FROM documents WHERE kind='media_source' AND (body::jsonb #>> '{bvid}')=%s AND (body::jsonb #>> '{cid}')=%s AND (body::jsonb #>> '{actual_quality}')=%s ORDER BY revision DESC,id DESC",
                (bvid, cid, quality),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def import_commit(self, identity, record, expected):
        with self.transaction() as db:
            current = self._document(db, "media_import", identity)
            require(
                (current.get("revision", 0) if current else 0) == expected, "version_conflict", 409
            )
            if (
                current
                and {key: value for key, value in current.items() if key != "revision"} == record
            ):
                return current
            require(
                not (
                    current
                    and current["state"] == "verified"
                    and same_identity(current["observation"], record.get("observation"))
                    and (
                        record["state"] != "verified"
                        or any(
                            current[key] != record[key] for key in ("bvid", "cid", "actual_quality")
                        )
                    )
                ),
                "import_evidence_conflict",
                409,
            )
            saved = self.put("media_import", identity, record, expected, db=db)
            if saved["state"] == "verified":
                self.put(
                    "media_source",
                    "import:" + identity,
                    self._import_source(identity, saved),
                    db=db,
                )
            return saved

    @staticmethod
    def _import_source(identity, record):
        source = {
            key: record[key]
            for key in (
                "root_id",
                "directory",
                "relative_path",
                "bvid",
                "cid",
                "actual_quality",
                "sha256",
                "evidence_digest",
            )
        }
        return {
            **source,
            "kind": "import",
            "import_record_id": identity,
            "import_record_revision": record["revision"],
            "entry": {
                "path": record["relative_path"],
                "kind": "video",
                "cid": record["cid"],
                "sha256": record["sha256"],
                "size_bytes": record["observation"]["size_bytes"],
            },
        }

    def import_relocate(self, plan):
        """CAS the complete old root and all its reuse references in one transaction.

        File proofs are produced by ImportRelocation before calling this storage operation.
        Historical metadata plans/receipts deliberately keep their original paths.
        """
        with self.transaction() as db:
            migration_id = plan["migration_id"]
            entries = plan["entries"]
            prepared = self._document(db, "import_relocation_plan", migration_id)
            require(prepared == plan, "import_relocation_plan_changed", 409)
            receipt = self._document(db, "import_relocation", migration_id)
            if receipt:
                require(receipt["fingerprint"] == plan["fingerprint"], "idempotency_conflict", 409)
                return {**receipt, "replayed": True}
            old_records = {
                row["id"]: json.loads(row["body"])
                for row in db.execute(
                    "SELECT id,body FROM documents WHERE kind='media_import' AND (body::jsonb #>> '{root_id}')=%s AND (body::jsonb #>> '{directory}')=%s",
                    (plan["root_id"], plan["old_directory"]),
                )
            }
            require(
                old_records
                == {entry["old_record_id"]: entry["expected_record"] for entry in entries},
                "version_conflict",
                409,
            )
            sources = [
                (row["id"], json.loads(row["body"]))
                for row in db.execute("SELECT id,body FROM documents WHERE kind='media_source'")
            ]
            for entry in entries:
                require(
                    entry["new_record_id"] == entry["old_record_id"]
                    or (
                        self._document(db, "media_import", entry["new_record_id"]) is None
                        and self._document(db, "media_source", "import:" + entry["new_record_id"])
                        is None
                    ),
                    "import_relocation_path_conflict",
                    409,
                )
            result_entries = []
            for entry in entries:
                old_id, new_id = entry["old_record_id"], entry["new_record_id"]
                saved = {**entry["record"], "revision": entry["expected_record"]["revision"] + 1}
                db.execute(
                    "UPDATE documents SET id=%s,revision=%s,body=%s WHERE kind='media_import' AND id=%s",
                    (new_id, saved["revision"], json.dumps(saved, ensure_ascii=False), old_id),
                )
                changed_sources, removed_sources = [], []
                for source_id, source in sources:
                    if source.get("kind") != "import" or source.get("import_record_id") != old_id:
                        continue
                    if saved["state"] != "verified":
                        # import_commit can leave a stale reference after invalidation. Never revive it.
                        db.execute(
                            "DELETE FROM documents WHERE kind='media_source' AND id=%s", (source_id,)
                        )
                        removed_sources.append({"source_id": source_id, "record": source})
                        continue
                    expected_source = self._import_source(old_id, entry["expected_record"])
                    require(
                        all(source.get(key) == value for key, value in expected_source.items()),
                        "import_source_changed",
                        409,
                    )
                    destination_id = (
                        "import:" + new_id if source_id == "import:" + old_id else source_id
                    )
                    updated = {
                        **source,
                        **self._import_source(new_id, saved),
                        "revision": source["revision"] + 1,
                    }
                    db.execute(
                        "UPDATE documents SET id=%s,revision=%s,body=%s WHERE kind='media_source' AND id=%s",
                        (
                            destination_id,
                            updated["revision"],
                            json.dumps(updated, ensure_ascii=False),
                            source_id,
                        ),
                    )
                    changed_sources.append(
                        {
                            "old_source_id": source_id,
                            "new_source_id": destination_id,
                            "record": updated,
                        }
                    )
                if saved["state"] == "verified" and not changed_sources:
                    updated = self.put(
                        "media_source",
                        "import:" + new_id,
                        self._import_source(new_id, saved),
                        0,
                        db=db,
                    )
                    changed_sources.append(
                        {
                            "old_source_id": None,
                            "new_source_id": "import:" + new_id,
                            "record": updated,
                        }
                    )
                result_entries.append(
                    {
                        **entry,
                        "record": saved,
                        "updated_sources": changed_sources,
                        "removed_stale_sources": removed_sources,
                    }
                )
            receipt = self.put(
                "import_relocation",
                migration_id,
                {
                    "schema_version": 1,
                    "migration_id": migration_id,
                    "root_id": plan["root_id"],
                    "old_directory": plan["old_directory"],
                    "new_directory": plan["new_directory"],
                    "fingerprint": plan["fingerprint"],
                    "state": "committed",
                    "committed_at": self.clock(),
                    "entries": result_entries,
                    "historical_metadata": plan["historical_metadata"],
                },
                0,
                db=db,
            )
            return {**receipt, "replayed": False}

    def download_source(self, bvid, cid, quality):
        with self.transaction() as db:
            row = db.execute(
                "SELECT body,generation FROM download_sources WHERE bvid=%s AND cid=%s AND quality=%s AND state='ready'",
                (bvid, cid, quality),
            ).fetchone()
        return {"download": json.loads(row[0]), "generation": row[1]} if row else None

    def retention_jobs(self):
        # Deleted information still owns recovery facts and retained files.
        with self.transaction() as db:
            rows = db.execute(
                "SELECT body FROM jobs WHERE state IN ('completed','published') ORDER BY updated DESC"
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def staging_referenced(self, file):
        with self.transaction() as db:
            rows = db.execute(
                "SELECT body FROM jobs WHERE state NOT IN ('completed','published')",
            ).fetchall()
        return any(
            download.get("file") == file
            for row in rows
            for download in json.loads(row["body"]).get("downloads", {}).values()
        )

    def job_inputs(self, identity):
        with self.transaction() as db:
            rows = db.execute(
                "SELECT body FROM documents WHERE kind='media_input' AND (body::jsonb #>> '{job_id}')=%s",
                (identity,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def members(self, identity):
        with self.transaction() as db:
            return {
                row[0]
                for row in db.execute(
                    'SELECT bvid FROM members WHERE subscription_id=%s', (identity,)
                )
            }

    def member_parts(self, identity):
        result = {}
        with self.transaction() as db:
            for bvid, cid in db.execute(
                'SELECT bvid,cid FROM member_parts WHERE subscription_id=%s', (identity,)
            ):
                result.setdefault(bvid, set()).add(cid)
        return result

    def episode_numbers(self, bvid, cids):
        with self.transaction() as db:
            maximum = db.execute(
                'SELECT COALESCE(MAX(number),0) FROM episodes WHERE bvid=%s', (bvid,)
            ).fetchone()[0]
            for cid in cids:
                if (
                    db.execute(
                        'SELECT 1 FROM episodes WHERE bvid=%s AND cid=%s', (bvid, cid)
                    ).fetchone()
                    is None
                ):
                    maximum += 1
                    db.execute('INSERT INTO episodes VALUES(%s,%s,%s)', (bvid, cid, maximum))
            return {
                row[0]: row[1]
                for row in db.execute('SELECT cid,number FROM episodes WHERE bvid=%s', (bvid,))
            }
