"""Read-only group activity derived from currently authorized conversation records."""

import re
from collections import Counter
from datetime import timedelta
from itertools import combinations
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tianshu.runtime.errors import AppError
from tianshu.types import Coverage

from .codec import source_refs

STOP_WORDS = re.compile(
    r"我们|你们|他们|这个|那个|什么|怎么|就是|可以|没有|一个|一下|现在|今天|然后|但是|"
    r"还是|已经|真的|不是|觉得|因为|所以|如果|或者|还有|好像|自己|这样|那么|"
    r"的|了|呢|吧|吗|啊|呀|在|是|和|与|就|都|也|很|我|你|他|她|它"
)


def keywords(text):
    """Literal keywords, not model-generated topics or inferred personal interests."""
    result = set()
    for word in re.findall(r"[A-Za-z][A-Za-z0-9_+#.-]{1,31}|[\u3400-\u9fff]+", text.casefold()):
        if re.fullmatch(r"[\u3400-\u9fff]+", word):
            for piece in STOP_WORDS.split(word):
                if 2 <= len(piece) <= 8:
                    result.add(piece)
                elif len(piece) > 8:
                    result.update(piece[index:index + 2] for index in range(len(piece) - 1))
        elif word not in {"the", "and", "this", "that", "with", "http", "https"}:
            result.add(word)
    return result


def summarize(rows, names, *, timezone, observed_at, days):
    """Keep authors, verified mentions, and verified reply authors separate."""
    people, topics, interactions, cooccurrence = {}, {}, Counter(), Counter()
    hourly = Counter()
    daily = Counter()
    samples = {}
    for row in rows:
        message_id = str(row["id"])
        parts = row["parts"]
        text = " ".join(part.get("text") or "" for part in parts
                        if part.get("kind") == "text"
                        and not any(key in (part.get("metadata") or {})
                                    for key in ("forward_source_ref", "forward_node_index")))
        text = text.strip()
        author = row.get("person_id")
        if row["speaker"] == "assistant":
            author = None
        samples[message_id] = {
            "id": message_id, "text": text[:1200] or "媒体或非文字消息",
            "text_truncated": len(text) > 1200,
            "author": names.get(author, "未记录作者") if author else
                      "角色" if row["speaker"] == "assistant" else "未记录作者",
            "created_at": row["created_at"], "source_refs": row["source_refs"],
        }
        local = row["created_at"].astimezone(timezone)
        daily[local.date().isoformat()] += 1
        hourly[local.hour] += 1
        if author:
            person = people.setdefault(author, {"id": author, "label": names.get(author, "群成员"),
                                                "count": 0, "message_ids": []})
            person["count"] += 1
            person["message_ids"].append(message_id)
            targets = set()
            for part in parts:
                metadata = part.get("metadata") or {}
                if "forward_source_ref" in metadata or "forward_node_index" in metadata:
                    continue
                if part.get("kind") in {"text", "mention", "mention_all"}:
                    targets.update(metadata.get("verified_subject_ids") or ())
                if part.get("kind") == "reply" and metadata.get("verified_original_author_person_id"):
                    targets.add(metadata["verified_original_author_person_id"])
            for target in targets - {author}:
                if target in names:
                    people.setdefault(target, {"id": target, "label": names[target],
                                               "count": 0, "message_ids": []})
                    interactions[tuple(sorted((author, target)))] += 1
        # Topic heat reflects human messages, without counting the bot's repeats.
        terms = set(sorted(keywords(re.sub(r"https?://\S+", "", text[:8192])))[:24]) if row["speaker"] == "user" else set()
        for term in terms:
            topic = topics.setdefault(term, {"id": term, "label": term, "count": 0,
                                             "authors": set(), "message_ids": []})
            topic["count"] += 1
            if author:
                topic["authors"].add(author)
            topic["message_ids"].append(message_id)
        cooccurrence.update(combinations(sorted(terms), 2))
    ranked = sorted(topics.values(), key=lambda item: (-item["count"], item["label"]))[:30]
    selected = {topic["id"] for topic in ranked}
    for topic in ranked:
        topic["authors"] = len(topic["authors"])
    end = observed_at.astimezone(timezone).date()
    return {
        "days": days, "timezone": str(timezone), "observed_at": observed_at,
        "message_count": len(rows), "participant_count": sum(person["count"] > 0 for person in people.values()),
        "daily": [{"date": (end - timedelta(days=offset)).isoformat(),
                   "count": daily[(end - timedelta(days=offset)).isoformat()]}
                  for offset in reversed(range(days + 1))],
        "hourly": [{"hour": hour, "count": hourly[hour]} for hour in range(24)],
        "topics": ranked,
        "topic_links": [{"from": first, "to": second, "count": count}
                        for (first, second), count in cooccurrence.items()
                        if first in selected and second in selected],
        "people": sorted(people.values(), key=lambda item: (-item["count"], item["id"])),
        "interactions": [{"from": first, "to": second, "count": count}
                         for (first, second), count in sorted(interactions.items())],
        "messages": samples,
    }


async def group_insights(service, context, body):
    if context.conversation_kind != "group" or not context.conversation_id:
        raise AppError("group_projection_forbidden", status=403)
    days = body.get("days", 7)
    if type(days) is not int or days not in {1, 7, 30}:
        raise AppError("invalid_group_insight_range")
    try:
        timezone = ZoneInfo(body.get("timezone", "Asia/Shanghai"))
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        raise AppError("invalid_timezone") from None
    async with service.database.transaction(read_only=True) as conn:
        await service.identity.check_context(context, connection=conn)
        observed = await (await conn.execute("SELECT now() AS observed_at")).fetchone()
        rows = await (await conn.execute(
            "SELECT m.*,author.speaker_id AS person_id FROM conversation.messages m "
            "LEFT JOIN conversation.turns t USING(turn_id) "
            "LEFT JOIN LATERAL (SELECT e.speaker_id FROM identity.events e "
            "JOIN identity.connections c USING(connection_id) WHERE c.actor_id=m.actor_id "
            "AND e.conversation_id=m.conversation_id AND e.producer=t.producer "
            "AND e.event_id=t.event_id AND e.revision=t.event_revision ORDER BY e.connection_id LIMIT 1) author ON true "
            "WHERE m.actor_id=%s AND m.conversation_id=%s "
            "AND m.scope_ids<@%s::text[] AND m.created_at>=now()-make_interval(days=>%s) "
            "ORDER BY m.id DESC LIMIT 1001",
            (context.actor_id, context.conversation_id, list(context.scope_ids), days),
        )).fetchall()
        checked = await service.sources.check_many_on(conn, context, tuple(source_refs(row["source_refs"]) for row in rows[:1000]))
        visible = [row for row, allowed in zip(rows[:1000], checked, strict=True) if allowed]
        names = await (await conn.execute(
            "SELECT DISTINCT p.person_id,p.display_name FROM identity.people p "
            "JOIN identity.conversation_members m USING(person_id) WHERE m.conversation_id=%s",
            (context.conversation_id,),
        )).fetchall()
        await service.identity.check_context(context, connection=conn)
    result = summarize(visible, {row["person_id"]: row["display_name"] for row in names},
                       timezone=timezone, observed_at=observed["observed_at"], days=days)
    missing = (["recent_message_window"] if len(rows) > 1000 else []) + (["source_unavailable"] if not all(checked) else [])
    return {**result, "coverage": Coverage(not missing, missing=tuple(missing))}
