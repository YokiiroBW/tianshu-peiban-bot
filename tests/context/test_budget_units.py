from datetime import UTC, datetime, timedelta

import pytest

from tianshu.conversation.context.assembly import ContextAssembler
from tianshu.models.types import ModelMessage, ModelPart
from tianshu.runtime.errors import AppError
from tianshu.types import ContextContribution, OriginKind, RequestBudget, TrustedContext


class Authority:
    async def check(self, *args):
        pass

    async def check_many(self, context, groups):
        return tuple(True for _ in groups)


def context():
    return TrustedContext("p", "a", "person", None, ("s",), ("s",), 1, OriginKind.HUMAN)


async def test_full_persona_is_mandatory_and_estimates_are_honest():
    assembler = ContextAssembler(Authority(), None, None)
    pack = await assembler.assemble(
        context(),
        "extract",
        "hello",
        RequestBudget(datetime.now(UTC) + timedelta(seconds=10)),
        persona_text="稳定人格" * 50,
        max_input_tokens=4000,
        reserved_output_tokens=100,
        contributions=(ContextContribution("optional", "x" * 4000, priority=1),),
    )
    assert "稳定人格" * 50 in pack.system and "budget_omitted:optional" in pack.missing
    assert pack.token_usage.used_tokens is None and pack.token_usage.measurement == "estimate"


async def test_tool_call_and_actual_result_cannot_be_silently_split():
    assembler = ContextAssembler(Authority(), None, None)
    messages = (
        ModelMessage(
            "assistant", (ModelPart("tool_call", call_id="real-call", name="read", arguments={}),)
        ),
        ModelMessage("tool", (ModelPart("tool_result", call_id="real-call", result="x" * 5000),)),
    )
    with pytest.raises(AppError, match="context_mandatory_budget_exceeded"):
        await assembler.assemble(
            context(),
            "extract",
            "",
            RequestBudget(datetime.now(UTC) + timedelta(seconds=10)),
            recent_messages=messages,
            max_input_tokens=1000,
            reserved_output_tokens=100,
        )
