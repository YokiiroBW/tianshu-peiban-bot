"""Independent offline package and snapshot relation verification."""
import copy
import hashlib
import json
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parents[1]
if (ROOT / ".deps").is_dir():
    sys.path.insert(0, str(ROOT / ".deps"))
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def relation(request, response):
    assert response["request_id"] == request["query"]["request_id"]
    assert all(response[k] == request[k] for k in ("actor_id", "conversation_id"))
    assert len(response["history"]) <= request["limit"]
    terminal = {"sent", "failed", "cancelled", "observed", "closed_unknown"}
    seen = set()
    for name in ("active_turns", "history"):
        seqs = []
        for view in response[name]:
            turn = view["turn"]
            assert turn["turn_id"] not in seen
            seen.add(turn["turn_id"])
            assert (turn["phase"] in terminal) == (name == "history")
            seqs.append(turn["turn_sequence"])
            if name == "history" and request["before_turn_sequence"] is not None:
                assert turn["turn_sequence"] < request["before_turn_sequence"]
            pairs = [(m["message_id"], m["revision"]) for m in view["messages"]]
            assert len(set(pairs)) == len(pairs)
            segments, replies = set(), set()
            for reply in view["replies"]:
                assert reply["reply_id"] not in replies
                assert reply["segment_sequence"] not in segments
                replies.add(reply["reply_id"])
                segments.add(reply["segment_sequence"])
                assert reply["segment_sequence"] <= reply["segment_count"]
        assert len(seqs) == len(set(seqs))
        if name == "history":
            assert seqs == sorted(seqs, reverse=True)
    collectors = response["collectors"]
    assert len({c["collection_id"] for c in collectors}) == len(collectors)
    for collector in collectors:
        pairs = [(m["message_id"], m["revision"]) for m in collector["messages"]]
        assert len(set(pairs)) == len(pairs)
    cursor = response["next_before_turn_sequence"]
    assert cursor is None or (response["history"] and cursor == min(v["turn"]["turn_sequence"] for v in response["history"]))
    assert len(json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode()) <= 1048576


def main():
    manifest = read(PACKAGE / "manifest.json")
    for name, expected in manifest["sha256"].items():
        assert sha(PACKAGE / name) == expected, name
    dependency = ROOT / "text-dialogue/v1"
    assert sha(dependency / "manifest.json") == manifest["dependency"]["manifest_sha256"]
    for name, expected in read(dependency / "manifest.json")["sha256"].items():
        assert sha(ROOT / name) == expected, name
    schemas = [read(p) for p in (dependency / "schemas").glob("*.json")]
    schema = read(PACKAGE / "schema.json")
    schemas.append(schema)
    registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas)
    Draft202012Validator.check_schema(schema)
    def check(name, document):
        Draft202012Validator({"$ref": schema["$id"] + "#/$defs/" + name}, registry=registry, format_checker=FormatChecker()).validate(document)
    positives = read(PACKAGE / "examples.json")
    for example in positives:
        check(example["schema"], example["document"])
    for example in read(PACKAGE / "negative-examples.json"):
        try:
            check(example["schema"], example["document"])
        except Exception as error:
            from jsonschema import ValidationError
            assert isinstance(error, ValidationError), type(error)
        else:
            raise AssertionError("Negative accepted: " + example["id"])
    request = next(e["document"] for e in positives if e["schema"] == "snapshot_request")
    responses = [e["document"] for e in positives if e["schema"] == "snapshot_response"]
    for response in responses:
        relation(request, response)
    for key, value in (("request_id", "wrong"), ("actor_id", "wrong"), ("conversation_id", "wrong")):
        bad = copy.deepcopy(responses[0]); bad[key] = value
        try:
            relation(request, bad)
        except AssertionError:
            pass
        else:
            raise AssertionError("Unrelated response accepted")
    print(f"PASS: {len(positives)} structural positives, 3 schema negatives, {len(responses)} relation positives, 3 correlation negatives; offline only.")


if __name__ == "__main__":
    main()
