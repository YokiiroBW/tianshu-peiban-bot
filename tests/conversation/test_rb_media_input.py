"""Unavailable media is received, while late bytes are a separate original-part version."""
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.conversation.codec import document, signature
from tianshu.conversation.service import ConversationService
from tianshu.conversation.types import InputPart
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import Migration, migrate
from tianshu.types import MediaRef, OriginKind, SourceRef, TrustedContext


@pytest.mark.parametrize("kind", ["image", "audio", "video", "file"])
def test_media_missing_is_a_structured_model_input(kind):
    part = InputPart(kind, metadata={"media_unavailable": True, "error_code": "provider_media_unavailable"})
    ConversationService._validate_parts((InputPart("text", text="看这条"), part))
    converted = ConversationService._message("user", [{"kind": kind, "metadata": dict(part.metadata)}])
    assert converted.parts[0].kind == "text"
    assert '"availability": "unavailable"' in converted.parts[0].text
    assert "不能推测" in converted.parts[0].text
    with pytest.raises(AppError, match="input_media_required"):
        ConversationService._validate_parts((InputPart(kind),))


@pytest.mark.asyncio
async def test_duplicate_late_attachment_preserves_original_message_and_revocation_hides_overlay(database):
    await migrate(database, (Migration("0032_conversation", files("tianshu.conversation").joinpath("schema.sql").read_text()),))
    context = TrustedContext("owner", "actor", "person", "chat", ("scope",), ("scope",), 1,
                             OriginKind.HUMAN, conversation_kind="private", delivery_allowed=True)
    original_ref, supplement_ref = SourceRef("original", 1), SourceRef("supplement", 1)
    parts = [{"kind": "image", "text": None, "media": None,
              "metadata": {"media_unavailable": True, "error_code": "provider_media_unavailable"}}]
    digest = signature(parts)
    async with database.transaction() as conn:
        await conn.execute("INSERT INTO conversation.turns(turn_id,actor_id,conversation_id,producer,event_id,event_revision,"
            "context,input_parts,source_refs,original_parts,original_source_refs,original_digest,state,reply_owner,reply_required,"
            "occurred_at,received_at,deadline_at) VALUES('turn','actor','chat','qq','event',1,%s,%s,%s,%s,%s,%s,"
            "'observed','other',false,now(),now(),now()+interval '15 minutes')",
            (document(context), document(parts), document((original_ref,)), document(parts), document((original_ref,)), digest))
        await conn.execute("INSERT INTO conversation.messages(turn_id,actor_id,conversation_id,speaker,parts,source_refs,scope_ids) "
            "VALUES('turn','actor','chat','user',%s,%s,%s)", (document(parts), document((original_ref,)), ["scope"]))
    queued = []
    jobs = SimpleNamespace(enqueue=AsyncMock(side_effect=lambda _connection, spec: queued.append(spec)))
    authority = SimpleNamespace(validate_persistence=AsyncMock(return_value=True),
        validate_media_supplement_on=AsyncMock(return_value=True),
        check_many=AsyncMock(side_effect=lambda _ctx, groups: tuple(True for _ in groups)))
    service = object.__new__(ConversationService)
    service.database, service.jobs, service.sources, service.identity = database, jobs, authority, authority
    media = MediaRef("source:supplement:1", "controlled", "image/png", 10, "sha256")
    async with database.transaction() as conn:
        first = await service.supplement_input(conn, context, input_id="turn", part_index=0,
            original_ref=original_ref, supplement_ref=supplement_ref, media=media)
    async with database.transaction() as conn:
        second = await service.supplement_input(conn, context, input_id="turn", part_index=0,
            original_ref=original_ref, supplement_ref=supplement_ref, media=media)
    assert first == second == 1 and len(queued) == 1
    async with database.transaction(read_only=True) as conn:
        turn = await (await conn.execute("SELECT * FROM conversation.turns WHERE turn_id='turn'")).fetchone()
        rows = await (await conn.execute("SELECT * FROM conversation.messages WHERE turn_id='turn'")).fetchall()
        count = await (await conn.execute("SELECT count(*) AS n FROM conversation.input_supplements")).fetchone()
    assert turn["event_revision"] == 1 and turn["original_parts"] == parts
    assert turn["original_digest"] == digest and count["n"] == 1
    overlay = await service._supplemented_messages(context, rows)
    assert overlay[0]["parts"][0]["media"]["media_id"] == media.media_id
    authority.check_many.side_effect = lambda _ctx, groups: tuple(False for _ in groups)
    revoked = await service._supplemented_messages(context, rows)
    assert revoked[0]["parts"][0]["media"] is None
