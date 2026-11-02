"""Only the dialogue entry point opts into provider conversation continuity."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.conversation.context.assembly import ContextAssembler
from tianshu.conversation.service import ConversationService
from tianshu.types import OriginKind, TrustedContext


@pytest.mark.asyncio
async def test_chat_explicitly_preserves_its_original_conversation_session_and_context():
    context = TrustedContext("principal", "actor", "person", "group", ("scope",), ("scope",),
        1, OriginKind.HUMAN, conversation_kind="group")
    identity = SimpleNamespace(check=AsyncMock(), check_many=AsyncMock(side_effect=lambda _context, refs: tuple(True for _ in refs)))
    service = SimpleNamespace(
        companion=SimpleNamespace(actor=AsyncMock(return_value={"persona_id": None})),
        life=SimpleNamespace(state=AsyncMock(return_value={"activity": None, "timezone": "UTC",
            "affect": {"source_refs": (), "feelings": (), "regulation": {"expression_direction": "natural"}}}),
            commitments=AsyncMock(return_value=SimpleNamespace(items=()))),
        expression=SimpleNamespace(hint=AsyncMock(return_value={})),
        growth=SimpleNamespace(material=AsyncMock(return_value=())),
        messages=AsyncMock(return_value=SimpleNamespace(items=())),
        assembler=ContextAssembler(identity, None, None), _tools=lambda _choices: ((), {}),
    )
    row = {"turn_id": "turn", "round": 0, "source_refs": [], "message_cutoff": None,
        "input_parts": [{"kind": "text", "text": "hello"}], "continuation": [],
        "deadline_at": datetime.now(UTC)+timedelta(minutes=5)}
    request, _pack, _tools = await ConversationService._prepare_model_request(service, context, row, "attempt", ())
    assert request.provider_session_scope == "conversation"
    assert request.function == "chat" and request.context is context
