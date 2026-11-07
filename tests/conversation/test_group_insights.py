from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from tianshu.conversation.group_insights import group_insights, summarize
from tianshu.runtime.errors import AppError

NOW = datetime(2026, 11, 7, 4, 0, tzinfo=UTC)


def message(number, text, *, author="alice", speaker="user", extra=()):
    return {"id": number, "parts": [{"kind": "text", "text": text}, *extra],
            "person_id": author, "speaker": speaker, "created_at": NOW, "source_refs": []}


def summary(rows):
    return summarize(rows, {"alice": "甲", "bob": "乙", "carol": "丙"},
                     timezone=ZoneInfo("Asia/Shanghai"), observed_at=NOW, days=7)


def test_group_heat_counts_messages_and_topics_count_human_messages_once():
    result = summary([message(1, "猫粮 猫粮"), message(2, "猫粮", author="bob"),
                      message(3, "猫粮", speaker="assistant")])
    assert result["message_count"] == 3 and result["participant_count"] == 2
    assert result["topics"] == [{"id": "猫粮", "label": "猫粮", "count": 2,
                                 "authors": 2, "message_ids": ["1", "2"]}]
    assert result["daily"][-1] == {"date": "2026-11-07", "count": 3}
    assert result["hourly"][12] == {"hour": 12, "count": 3}


def test_interactions_require_verified_direct_mentions_or_reply_authors():
    result = summary([
        message(1, "你好", extra=(
            {"kind": "mention", "text": "乙", "metadata": {"verified_subject_ids": ["bob"]}},
            {"kind": "reply", "metadata": {"verified_original_author_person_id": "bob"}},
        )),
        message(2, "丙", extra=(
            {"kind": "mention", "metadata": {"verified_subject_ids": ["carol"], "forward_node_index": 1}},
            {"kind": "mention", "text": "丙"},
        )),
        message(3, "猫粮", author=None, extra=(
            {"kind": "mention", "metadata": {"verified_subject_ids": ["carol"]}},
        )),
    ])
    assert result["interactions"] == [{"from": "alice", "to": "bob", "count": 1}]
    assert {person["id"] for person in result["people"]} == {"alice", "bob"}
    assert result["people"][1]["count"] == 0


def test_unknown_authorship_and_forwarded_text_do_not_create_personal_or_topic_facts():
    result = summary([message(1, "", author=None, extra=(
        {"kind": "text", "text": "群秘密", "metadata": {"forward_source_ref": {"source_id": "x"}}},
    ))])
    assert result["people"] == [] and result["topics"] == []
    assert result["message_count"] == 1
    assert result["messages"]["1"]["author"] == "未记录作者"


def test_topic_cooccurrence_is_counted_per_message_and_long_evidence_is_marked():
    result = summary([message(1, "猫粮 散步"), message(2, "猫粮 散步"), message(3, "长" * 1300)])
    assert {"from": "散步", "to": "猫粮", "count": 2} in result["topic_links"]
    assert result["messages"]["3"]["text_truncated"]
    assert len(result["messages"]["3"]["text"]) == 1200


@pytest.mark.parametrize("kind,conversation,body,error", [
    ("private", "one", {}, "group_projection_forbidden"),
    ("group", None, {}, "group_projection_forbidden"),
    ("group", "one", {"days": True}, "invalid_group_insight_range"),
    ("group", "one", {"days": 365}, "invalid_group_insight_range"),
    ("group", "one", {"timezone": "Missing/Zone"}, "invalid_timezone"),
])
async def test_invalid_or_non_group_insight_requests_never_read_storage(kind, conversation, body, error):
    with pytest.raises(AppError, match=error):
        await group_insights(None, SimpleNamespace(conversation_kind=kind, conversation_id=conversation), body)
