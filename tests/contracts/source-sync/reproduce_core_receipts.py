"""Run fixed Core ingest/cancel and SQLite, with the fixed product's fake dependencies.

Exports immutable Git blobs into this task's disposable directory. No product working
tree, product database, HTTP listener, real identity, model or channel is used.
"""
import argparse
import asyncio
import copy
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

PIN = "17eba4f1d513073c3e1bad7823e2f7c3fc728754"
TESTS = Path(__file__).resolve().parent


def git(repo, *arguments):
    return subprocess.run(["git", *arguments], cwd=repo, check=True, capture_output=True).stdout


async def run(export, contracts):
    sys.path[:0] = [str(export / "src"), str(export / "tests")]
    os.environ["TIANSHU_CONTRACTS"] = str(contracts)
    from support import Harness
    from tianshu_companion.contracts import Fault, digest
    from tianshu_companion.short_context import sources_current

    cases = []

    def harness(name, **policy):
        h = Harness(str(export / (name + ".sqlite")), **policy)
        h.clock.now = 1789344000.0  # Fixed synthetic time; no real channel/user data.
        return h

    async def error(core, request):
        try:
            await core.ingest("nonebot", request)
        except Fault as fault:
            return {"code": fault.code, "status": fault.status}
        raise AssertionError("Expected a rejected admission")

    h = harness("multi-target")
    try:
        request = h.request(message="synthetic:multi", targets=["actor:a", "actor:b"])
        h.contracts.check("conversation#ingest_request", request)
        result = await error(h.core, request)
        assert result["code"] == "forbidden" and not h.core.store.list("inbox")
        cases.append(dict(id="schema_valid_multi_target_rejected", result=result, inbox_count=0))
    finally:
        await h.core.close()

    h = harness("retarget-same-revision")
    try:
        original = h.request(message="synthetic:same", actor="actor:a")
        first = await h.core.ingest("nonebot", original)
        other = h.request(message="synthetic:same", actor="actor:b")
        result = await error(h.core, other)
        assert result["code"] == "idempotency_conflict" and len(h.core.store.list("inbox")) == 1
        cases.append(dict(id="same_revision_new_target_conflicts", result=result, first_receipt=first, inbox_count=1))
    finally:
        await h.core.close()

    h = harness("empty-target-alias")
    try:
        original = h.request(message="synthetic:empty", actor="actor:a", targets=[])
        first = await h.core.ingest("nonebot", original)
        other = h.request(message="synthetic:empty", actor="actor:b", targets=[])
        second = await h.core.ingest("nonebot", other)
        collection = h.core.store.get("collections", second["collection_id"])
        assert second["deduplicated"] and first["receipt_id"] == second["receipt_id"]
        assert collection["scope"]["actor_id"] == "actor:a"
        cases.append(dict(id="empty_target_new_origin_reuses_first_receipt", first_receipt=first, second_receipt=second, stored_scope=collection["scope"], inbox_count=len(h.core.store.list("inbox"))))
    finally:
        await h.core.close()

    h = harness("mixed-actor-collector")
    try:
        first = await h.core.ingest("nonebot", h.request(message="synthetic:a", actor="actor:a"))
        second = await h.core.ingest("nonebot", h.request(message="synthetic:b", actor="actor:b"))
        collection = h.core.store.get("collections", first["collection_id"])
        targets = [m["target_actor_ids"] for m in collection["messages"]]
        assert first["collection_id"] == second["collection_id"]
        assert collection["scope"]["actor_id"] == "actor:a" and targets == [["actor:a"], ["actor:b"]]
        cases.append(dict(id="different_messages_share_actor_a_collector", first_receipt=first, second_receipt=second, stored_scope=collection["scope"], stored_targets=targets, inbox_sources=[row["source"] for row in h.core.store.list("inbox")]))
    finally:
        await h.core.close()

    h = harness("cross-actor-edit", silence_ms=0)
    try:
        original = h.request(message="synthetic:edit", actor="actor:a")
        first = await h.core.ingest("nonebot", original)
        second = await h.core.ingest("nonebot", h.request(message="synthetic:edit", actor="actor:b", kind="edit", revision=2))
        a = h.core.store.get("collections", first["collection_id"])
        b = h.core.store.get("collections", second["collection_id"])
        stable = {k: v for k, v in original["message_key"].items() if k != "revision"}
        latest = h.core.store.latest_source(first["conversation_id"], digest(stable))
        assert a["scope"]["actor_id"] == "actor:a" and b["scope"]["actor_id"] == "actor:b"
        assert latest["receipt"]["receipt_id"] == second["receipt_id"]
        cases.append(dict(id="higher_revision_can_change_actor_after_seal", first_receipt=first, second_receipt=second, original_scope=a["scope"], current_scope=b["scope"], latest_source=latest["source"]))
    finally:
        await h.core.close()

    h = harness("cancel-versus-retract", silence_ms=0)
    try:
        request = h.request(message="synthetic:cancel")
        receipt = await h.core.ingest("nonebot", request)
        before = copy.deepcopy(h.turns()[0])
        cancellation = await h.cancel(before)
        cancelled = h.core.store.get("turns", before["id"])
        stable = {k: v for k, v in request["message_key"].items() if k != "revision"}
        latest = h.core.store.latest_source(receipt["conversation_id"], digest(stable))
        assert cancelled["phase"] == "cancelled" and sources_current(h.core.store, cancelled)
        assert latest["request"]["kind"] == "message" and latest["receipt"]["receipt_id"] == receipt["receipt_id"]
        await h.core.close()
        h.core = h.new_core()
        reopened = h.core.store.get("turns", before["id"])
        assert reopened["phase"] == "cancelled" and sources_current(h.core.store, reopened)
        retraction = await h.core.ingest("nonebot", h.request(message="synthetic:cancel", kind="retract", revision=2))
        assert not sources_current(h.core.store, reopened)
        cases.append(dict(id="reply_cancel_preserves_input_across_restart_then_retract_invalidates", accepted_receipt=receipt, cancellation=cancellation, cancelled_phase=reopened["phase"], input_current_after_cancel_and_restart=True, retraction_receipt=retraction, input_current_after_retract=False))
    finally:
        await h.core.close()
    return cases


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--contracts", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    repo, contracts = args.repository.resolve(), args.contracts.resolve()
    assert git(repo, "rev-parse", PIN).decode().strip() == PIN
    runtime = (TESTS / ".runtime").resolve()
    runtime.mkdir(exist_ok=True)
    files = git(repo, "ls-tree", "-r", "--name-only", PIN, "src/tianshu_companion").decode().splitlines()
    files = [p for p in files if p.endswith(".py") and not p.endswith("/app.py")] + ["tests/support.py"]
    with tempfile.TemporaryDirectory(prefix="core-receipts-", dir=runtime) as directory:
        export = Path(directory).resolve()
        # Verify the final absolute disposable path before automatic recursive cleanup.
        assert export.is_relative_to(runtime) and export != runtime
        hashes = []
        for name in files:
            data = git(repo, "show", PIN + ":" + name)
            target = export / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            assert target.read_bytes() == data
            hashes.append(dict(path=name, sha256=hashlib.sha256(data).hexdigest()))
        cases = asyncio.run(run(export, contracts))
        report = dict(
            task="TS-002", fixture_only_dependencies=True, core_commit=PIN,
            execution="Real pinned Core.ingest/Core.cancel/Store and published schema; in-process, no HTTP or genuine identity/model/channel",
            exported_blobs=hashes,
            dependencies={p: importlib.metadata.version(p) for p in ("httpx", "jsonschema", "referencing")},
            cases=cases,
        )
    destination = args.report.resolve()
    allowed = (TESTS.parents[2] / "docs/development/candidates/source-sync").resolve()
    if not destination.is_relative_to(allowed):
        raise ValueError("Report must stay inside the allowed candidate directory")
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"PASS: {len(cases)} fixed-Core receipt/storage reproductions; report {destination}")


if __name__ == "__main__":
    main()
