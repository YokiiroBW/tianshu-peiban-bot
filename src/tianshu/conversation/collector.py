"""Durable U01 silence collection, separated by actual speaker and authorization facts."""
from datetime import UTC, datetime, timedelta
import json

from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import ServiceClass
from .codec import document, signature, source_refs


class ConversationCollector:
    def __init__(self, database, jobs, policy):
        self.database, self.jobs, self.policy = database, jobs, policy

    async def accept(self, connection, context, message, refs, turn_id, responding):
        await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"turn:{context.actor_id}:{context.conversation_id}",))
        old = await (await connection.execute("SELECT turn_id,state,merged_into FROM conversation.turns WHERE turn_id=%s", (turn_id,))).fetchone()
        if old:
            return {"turn_id": old["merged_into"] or turn_id, "input_id": turn_id, "state": old["state"]}
        key = signature({"actor": context.actor_id, "conversation": context.conversation_id, "principal": context.principal_id,
            "person": context.person_id, "producer": message.producer, "scope": context.scope_ids,
            "audience": context.audience, "epoch": context.access_epoch})
        now = datetime.now(UTC)
        due = await (await connection.execute("SELECT * FROM conversation.turns WHERE actor_id=%s AND conversation_id=%s AND state='collecting' AND seal_at<=%s FOR UPDATE", (context.actor_id, context.conversation_id, now))).fetchall()
        for row in due:
            await self._seal(connection, row)
        parent = None
        if responding and not message.command:
            parent = await (await connection.execute("SELECT * FROM conversation.turns WHERE actor_id=%s AND conversation_id=%s AND collector_key=%s AND state='collecting' FOR UPDATE", (context.actor_id, context.conversation_id, key))).fetchone()
        raw = wire(message.parts)
        size = len(json.dumps(raw, ensure_ascii=False).encode())
        if size > self.policy.max_collection_bytes:
            raise AppError("collection_too_large", status=413)
        if parent and (parent["collection_messages"]+1 > self.policy.max_collection_messages or parent["collection_bytes"]+size > self.policy.max_collection_bytes):
            await self._seal(connection, parent)
            parent = None
        if responding and parent is None:
            counts = await (await connection.execute("SELECT count(*) FILTER (WHERE state='collecting') AS collectors,count(*) FILTER (WHERE state IN ('collecting','queued')) AS queued FROM conversation.turns WHERE actor_id=%s AND conversation_id=%s", (context.actor_id, context.conversation_id))).fetchone()
            if counts["collectors"] >= self.policy.max_collectors_per_conversation or counts["queued"] >= self.policy.max_queued_turns:
                raise AppError("conversation_busy", "当前消息队列已满，请稍后重试；这条消息尚未受理。", status=429)
        state = "merged" if parent else "queued" if responding and message.command else "collecting" if responding else "observed"
        seal_at = now+timedelta(milliseconds=self.policy.silence_ms)
        await connection.execute(
            "INSERT INTO conversation.turns(turn_id,actor_id,conversation_id,producer,event_id,event_revision,context,input_parts,source_refs,original_parts,original_source_refs,state,reply_owner,reply_required,command,occurred_at,received_at,deadline_at,collector_key,seal_at,collection_messages,collection_bytes,merged_into) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s)",
            (turn_id, context.actor_id, context.conversation_id, message.producer, message.event_id, message.revision, document(context),
             document(raw), document(refs), document(raw), document(refs), state, message.reply_owner, message.reply_required,
             message.command, message.occurred_at, message.received_at, now+timedelta(minutes=15), key, seal_at, size, parent["turn_id"] if parent else None))
        message_row = await (await connection.execute("INSERT INTO conversation.messages(turn_id,actor_id,conversation_id,speaker,parts,source_refs,scope_ids) VALUES (%s,%s,%s,'user',%s,%s,%s) RETURNING id", (turn_id, context.actor_id, context.conversation_id, document(raw), document(refs), list(context.scope_ids)))).fetchone()
        root_id = parent["turn_id"] if parent else turn_id
        collection_revision = parent["collection_revision"]+1 if parent else 1
        if parent:
            combined_refs = tuple({(ref.source_id, ref.revision): ref for ref in (*source_refs(parent["source_refs"]), *refs)}.values())
            if self.policy.max_wait_ms is not None:
                seal_at = min(seal_at, parent["received_at"]+timedelta(milliseconds=self.policy.max_wait_ms))
            await connection.execute("UPDATE conversation.turns SET input_parts=input_parts||%s,source_refs=%s,seal_at=%s,collection_messages=collection_messages+1,collection_bytes=collection_bytes+%s,collection_revision=%s,message_cutoff=%s WHERE turn_id=%s", (document(raw), document(combined_refs), seal_at, size, collection_revision, message_row["id"], root_id))
        else:
            await connection.execute("UPDATE conversation.turns SET message_cutoff=%s WHERE turn_id=%s", (message_row["id"], root_id))
        if state in {"collecting", "merged"}:
            await self.jobs.enqueue(connection, JobSpec("conversation.seal", f"collect:{root_id}:{collection_revision}", {"turn_id": root_id, "expected_revision": collection_revision}, ServiceClass.INTERACTIVE, seal_at, context.actor_id))
        elif state == "queued":
            await self._run(connection, root_id, context.actor_id)
        return {"turn_id": root_id, "input_id": turn_id, "state": "collecting" if parent else state}

    async def _run(self, connection, turn_id, actor_id):
        await self.jobs.enqueue(connection, JobSpec("conversation.run", "turn:"+turn_id+":0", {"turn_id": turn_id}, ServiceClass.INTERACTIVE, subject_id=actor_id))

    async def _seal(self, connection, row):
        await connection.execute("UPDATE conversation.turns SET state='queued',deadline_at=now()+interval '15 minutes' WHERE turn_id=%s AND state='collecting'", (row["turn_id"],))
        await self._run(connection, row["turn_id"], row["actor_id"])

    async def seal(self, turn_id, expected_revision):
        async with self.database.transaction() as connection:
            row = await (await connection.execute("SELECT * FROM conversation.turns WHERE turn_id=%s FOR UPDATE", (turn_id,))).fetchone()
            if not row or row["state"] != "collecting" or row["collection_revision"] != expected_revision:
                return
            if row["seal_at"] > datetime.now(UTC):
                return
            await self._seal(connection, row)
