"""A frozen synthetic corpus; expectations never enter the application or model input."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path


OLD_PRODUCTION_SHA = "dcdf729b5d22f451093e3658d131fd6757a00dde"
COST_STORY_IDS = ("S01", "S02", "S03", "S05", "S06", "S08", "S10", "S14")


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode()).hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


@dataclass(frozen=True)
class Story:
    id: str
    split: str
    focus: str
    messages: tuple[tuple[str, str], ...]
    question: str
    required: tuple[str, ...]
    forbidden: tuple[str, ...]

    def execution_input(self):
        # No focus, expected result, rubric, or later question is passed at ingest time.
        return {"id": self.id, "messages": self.messages, "question": self.question}

    def rubric(self):
        return {"required": self.required, "forbidden": self.forbidden,
                "verdict": "pending_human_review", "reviewer": None, "notes": None}


def read_corpus(path):
    value = load_json(path)
    stories = []
    for row in value["families"]:
        if row["split"] not in {"development", "held_out"}:
            raise ValueError("invalid_story_split")
        messages = tuple(tuple(item) for item in row["messages"])
        if (not messages or any(len(item) != 2 or any(not isinstance(x, str) or not x
                 for x in item) for item in messages)
                or not isinstance(row["question"], str) or not row["question"]):
            raise ValueError("invalid_story_messages")
        stories.append(Story(row["id"], row["split"], row["focus"], messages,
                             row["question"], tuple(row["required"]), tuple(row["forbidden"])))
    if len({story.id for story in stories}) != len(stories):
        raise ValueError("duplicate_story_id")
    return tuple(stories), digest(value)


def stream_plan(story, settings):
    """The same arrival clock/checkpoints are used for all cost variants.

    First establish one observed turn, then deliver at most three turns before a
    checkpoint. A checkpoint is a quiet-window boundary; the policy can further
    divide that prefix. Operators can pin explicit boundaries/times for a story.
    Only the currently arrived message is returned in each event.
    """
    overrides = settings.get("stories", {}).get(story.id, {})
    count = len(story.messages)
    flush_after = overrides.get("flush_after", sorted({1, *range(4, count + 1, 3), count}))
    if (not isinstance(flush_after, list) or flush_after != sorted(set(flush_after))
            or not flush_after or flush_after[-1] != count
            or any(type(x) is not int or not 1 <= x <= count for x in flush_after)):
        raise ValueError("invalid_stream_checkpoints:" + story.id)
    anchor = datetime.fromisoformat(settings.get("anchor", "2026-11-01T09:00:00+08:00"))
    if anchor.tzinfo is None:
        raise ValueError("stream_anchor_timezone_required")
    seconds = settings.get("message_interval_seconds", 60)
    if type(seconds) is not int or seconds < 1:
        raise ValueError("invalid_stream_interval")
    times = overrides.get("occurred_at")
    if times is not None and len(times) != count:
        raise ValueError("invalid_stream_times")
    events = []
    for index, (speaker, text) in enumerate(story.messages, 1):
        occurred = datetime.fromisoformat(times[index - 1]) if times else anchor + timedelta(
            seconds=(index - 1) * seconds)
        if occurred.tzinfo is None or events and occurred <= events[-1]["occurred_at"]:
            raise ValueError("stream_times_must_increase")
        events.append({"index": index, "speaker": speaker, "text": text,
                       "occurred_at": occurred, "flush": index in flush_after})
    return tuple(events)


def comparison_key(story, corpus_hash, stream_settings):
    # Policy/code intentionally differ; input and arrival schedule must not.
    plan = [{**event, "occurred_at": event["occurred_at"].isoformat()}
            for event in stream_plan(story, stream_settings)]
    return digest({"corpus_hash": corpus_hash, "story": story.execution_input(), "stream": plan})


def private_output(path, private_root):
    """All generated corpora, models, logs and checkouts stay in the explicit private area."""
    result, root = Path(path).resolve(), Path(private_root).resolve()
    if result == root or root not in result.parents:
        raise ValueError("output_must_be_inside_private_root")
    return result
