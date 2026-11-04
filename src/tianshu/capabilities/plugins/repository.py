"""Plugin-owned transactions over Platform's registered PostgreSQL backend."""

from contextlib import closing, contextmanager
import json
import time
import uuid

from ..contracts import canonical, digest, require


class Repository:
    def __init__(self, path, *, runtime=None):
        from ..storage import DomainStore
        self.runtime = runtime
        self.storage = DomainStore(runtime.database, "capability_plugins")

    @contextmanager
    def transaction(self, write=True):
        with self.storage.transaction(write) as db:
            yield db

    def get(self, kind, identifier, db=None):
        require(
            kind in {"packages", "instances", "operations", "notifications", "schedules"},
            "invalid_input",
            400,
        )
        if db is None:
            with self.transaction(False) as connection:
                return self.get(kind, identifier, connection)
        row = db.execute(f"SELECT document FROM plugin_{kind} WHERE id=%s", (identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, kind, document, db=None, *, expected=None):
        if db is None:
            with self.transaction() as connection:
                return self.put(kind, document, connection, expected=expected)
        if expected is not None:
            row = db.execute(f"SELECT document FROM plugin_{kind} WHERE id=%s FOR UPDATE",
                             (document["id"],)).fetchone()
            old = json.loads(row[0]) if row else None
            require(old is not None and old.get("revision") == expected, "revision_conflict", 409)
        db.execute(
            f"INSERT INTO plugin_{kind}(id,document) VALUES(%s,%s) ON CONFLICT(id) DO UPDATE SET document=excluded.document",
            (document["id"], canonical(document)),
        )
        return document

    def list(self, kind):
        require(
            kind in {"packages", "instances", "operations", "notifications", "schedules"},
            "invalid_input",
            400,
        )
        with self.transaction(False) as db:
            return [
                json.loads(row[0])
                for row in db.execute(f"SELECT document FROM plugin_{kind} ORDER BY id")
            ]

    def observe_runtime(self, owner_id, samples, *, starting=False):
        """Publish actual child/RPC observations under the existing worker lease."""
        with self.transaction() as db:
            # Config revision is unchanged: this is a runtime observation of the
            # existing instance, not another desired-state/configuration writer.
            if not samples:
                return
            # A delayed health sample can only renew its existing generation.
            # After stop/replace, it cannot recreate a removed claim or clear a
            # newer child's observation. Only actual startup creates a claim.
            db.execute(
                "WITH samples AS (SELECT * FROM jsonb_to_recordset(%s::jsonb) "
                "AS sample(id text,revision bigint,generation text,pid integer)) "
                "UPDATE plugin_instances i SET document=jsonb_set(i.document::jsonb,'{plugin_runtime}',"
                "jsonb_build_object('owner_id',o.id,'generation',s.generation,'pid',s.pid,"
                "'instance_revision',s.revision,'observed_at',now(),"
                "'expires_at',now()+interval '15 seconds'))::text "
                "FROM samples s,capabilities.execution_owners o WHERE i.id=s.id "
                "AND (i.document::jsonb->>'revision')::bigint=s.revision "
                "AND i.document::jsonb->>'desired_state'='enabled' "
                "AND (%s::boolean OR (i.document::jsonb #>> '{plugin_runtime,owner_id}'=o.id "
                "AND i.document::jsonb #>> '{plugin_runtime,generation}'=s.generation)) "
                "AND o.id=%s AND o.role='independent' AND o.expires_at>now() AND o.stopped_at IS NULL",
                (canonical(samples), starting, owner_id),
            )

    def runtime(self, identifier, revision):
        """An API process verifies both the instance generation and worker lease."""
        with self.transaction(False) as db:
            row = db.execute(
                "SELECT i.document::jsonb->'plugin_runtime' FROM plugin_instances i "
                "JOIN capabilities.execution_owners o "
                "ON o.id=(i.document::jsonb #>> '{plugin_runtime,owner_id}') "
                "WHERE i.id=%s AND (i.document::jsonb->>'revision')::bigint=%s "
                "AND i.document::jsonb->>'desired_state'='enabled' "
                "AND (i.document::jsonb #>> '{plugin_runtime,instance_revision}')::bigint=%s "
                "AND (i.document::jsonb #>> '{plugin_runtime,expires_at}')::timestamptz>now() "
                "AND o.role='independent' AND o.expires_at>now() AND o.stopped_at IS NULL",
                (identifier, revision, revision),
            ).fetchone()
            if not row:
                return None
            return json.loads(row[0]) if isinstance(row[0], str) else row[0]

    def forget_runtime(self, identifier, owner_id, generation):
        with self.transaction() as db:
            db.execute(
                "UPDATE plugin_instances SET document=(document::jsonb-'plugin_runtime')::text "
                "WHERE id=%s AND document::jsonb #>> '{plugin_runtime,owner_id}'=%s "
                "AND document::jsonb #>> '{plugin_runtime,generation}'=%s",
                (identifier, owner_id, generation),
            )

    def save_schedule(self, document, expected, enqueue):
        with self.transaction() as connection:
            self.runtime.jobs.assert_generation_sync_on(connection, document.get("data_generation"))
            self.put("schedules",document,connection,expected=expected)
            enqueue(connection,document)

    def wait_schedule(self, document, expected):
        with self.transaction() as connection:
            self.runtime.jobs.assert_generation_sync_on(connection, document.get("data_generation"))
            self.put("schedules", document, connection, expected=expected)

    def defer_schedule(self, document, enqueue):
        with self.transaction() as connection:
            self.runtime.jobs.assert_generation_sync_on(connection, document.get("data_generation"))
            enqueue(connection,document)

    def accept(self, instance, action, value, intent, principal, *, binding=None):
        key = digest([principal, instance["id"], action["id"], intent])
        semantics = digest([value, binding])
        with self.transaction() as db:
            current = self.get("instances", instance["id"], db)
            require(
                current and current["revision"] == instance["revision"], "revision_conflict", 409
            )
            row = db.execute(
                'SELECT semantics,document FROM capabilities.actions WHERE intent=%s AND instance_id IS NOT NULL', (key,)
            ).fetchone()
            if row:
                require(row[0] == semantics, "idempotency_conflict", 409)
                return json.loads(row[1]), False
            from ..execution_context import CURRENT_ACTION
            from tianshu.runtime.serialization import wire
            from tianshu.types import Attempt,Receipt
            typed_action=CURRENT_ACTION.get()
            require(typed_action is not None,"trusted_action_required",403)
            document = {
                "id": typed_action.action_id,
                "instance_id": instance["id"],
                "package_id": instance["package_id"],
                "package_version": instance["version"],
                "action": action["id"],
                "intent_key": intent,
                "principal": principal,
                "config_revision": instance["revision"],
                "binding_revision": instance["revision"],
                "binding_id": binding,
                "input": value,
                "config": instance["config"],
                "execution": "accepted",
                "effect": "none",
                "delivery": "not_requested",
                "result": None,
                "external": None,
                "error": None,
                "revision": 1,
                "created_at": int(time.time()),
                "updated_at": time.time(),
                "dispatch": "not_sent",
                "checkpoint": None,
                "progress": None,
                "write": action["effect"] == "write",
                "snapshot": instance,
            }
            existing=db.execute("SELECT document FROM capabilities.actions WHERE id=%s FOR UPDATE",(typed_action.action_id,)).fetchone()
            if existing:
                previous=json.loads(existing[0])
                if previous.get("plugin_initialized"):
                    return previous,False
                db.execute("UPDATE capabilities.actions SET document=%s,instance_id=%s WHERE id=%s",(canonical({**previous,**document,"plugin_initialized":True}),instance["id"],typed_action.action_id))
            else:
                attempt=Attempt(str(uuid.uuid4()),typed_action.action_id)
                receipt=Receipt(str(uuid.uuid4()),typed_action.action_id,attempt.attempt_id,"pending","none","not_requested")
                db.execute("INSERT INTO capabilities.actions(id,intent,semantics,principal_id,kind,instance_id,created_at,document,action,attempt,receipt) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb)",(document["id"],key,semantics,typed_action.context.principal_id,typed_action.capability_id,instance["id"],document["created_at"],canonical({**document,"plugin_initialized":True}),canonical(wire(typed_action)),canonical(wire(attempt)),canonical(wire(receipt))))
            return document, True

    def call(self, identifier):
        with self.transaction(False) as db:
            row = db.execute(
                'SELECT document FROM capabilities.actions WHERE id=%s', (identifier,)
            ).fetchone()
            return json.loads(row[0]) if row else None

    def update_call(self, identifier, **changes):
        with self.transaction() as db:
            row = db.execute(
                'SELECT document FROM capabilities.actions WHERE id=%s', (identifier,)
            ).fetchone()
            require(row is not None, "not_found", 404)
            document = json.loads(row[0])
            document.update(changes, revision=document["revision"] + 1, updated_at=time.time())
            db.execute(
                'UPDATE capabilities.actions SET document=%s WHERE id=%s', (canonical(document), identifier)
            )
            return document

    def calls(self, instance=None, *, limit=100):
        with self.transaction(False) as db:
            query = 'SELECT document FROM capabilities.actions WHERE instance_id IS NOT NULL'
            parameters = []
            if instance:
                query += ' AND instance_id=%s'
                parameters.append(instance)
            query += ' ORDER BY created_at DESC,id DESC LIMIT %s'
            parameters.append(limit)
            return [json.loads(row[0]) for row in db.execute(query, parameters)]

    def unfinished(self):
        with self.transaction(False) as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT document FROM capabilities.actions WHERE instance_id IS NOT NULL AND (document::jsonb #>> '{execution}') IN ('accepted','running')"
                )
            ]

    def snapshot(self, instance):
        with self.transaction(False) as db:
            return {
                "instance": self.get("instances", instance, db),
                "state": list(
                    db.execute(
                        'SELECT key,value,revision FROM plugin_state WHERE instance_id=%s',
                        (instance,),
                    )
                ),
            }

    def restore(self, instance, snapshot):
        with self.transaction() as db:
            self.put("instances", snapshot["instance"], db)
            db.execute('DELETE FROM plugin_state WHERE instance_id=%s', (instance,))
            for key, value, revision in snapshot["state"]:
                db.execute(
                    'INSERT INTO plugin_state VALUES(%s,%s,%s,%s)', (instance, key, value, revision)
                )

    def state(self, instance, operation, value):
        key = value["key"]
        require(isinstance(key, str) and 1 <= len(key) <= 256, "invalid_input", 400)
        with self.transaction(operation != "get") as db:
            row = db.execute(
                'SELECT value,revision FROM plugin_state WHERE instance_id=%s AND key=%s',
                (instance, key),
            ).fetchone()
            if operation == "get":
                return {"value": json.loads(row[0]), "revision": row[1]} if row else None
            revision = row[1] if row else None
            require(value["expected_revision"] == revision, "revision_conflict", 409)
            if operation == "delete":
                db.execute(
                    'DELETE FROM plugin_state WHERE instance_id=%s AND key=%s', (instance, key)
                )
                return {"deleted": True}
            require(
                operation == "compare_and_set" and len(canonical(value["value"]).encode()) <= 65536,
                "invalid_input",
                400,
            )
            revision = (revision or 0) + 1
            db.execute(
                'INSERT INTO plugin_state VALUES(%s,%s,%s,%s) ON CONFLICT(instance_id,key) DO UPDATE SET value=excluded.value,revision=excluded.revision',
                (instance, key, canonical(value["value"]), revision),
            )
            return {"value": value["value"], "revision": revision}

    def log(self, instance, document):
        with self.transaction() as db:
            db.execute(
                'INSERT INTO plugin_logs VALUES(%s,%s,%s,%s)',
                (uuid.uuid4().hex, instance, time.time(), canonical(document)),
            )
            # Keep the last 1000 entries per instance without an unbounded runtime buffer.
            db.execute(
                'DELETE FROM plugin_logs WHERE instance_id=%s AND id NOT IN (SELECT id FROM plugin_logs WHERE instance_id=%s ORDER BY created_at DESC,id DESC LIMIT 1000)',
                (instance, instance),
            )

    def logs(self, instance):
        with self.transaction(False) as db:
            return [
                {"id": row[0], "created_at": row[1], **json.loads(row[2])}
                for row in db.execute(
                    'SELECT id,created_at,document FROM plugin_logs WHERE instance_id=%s ORDER BY created_at DESC,id DESC LIMIT 100',
                    (instance,),
                )
            ]
