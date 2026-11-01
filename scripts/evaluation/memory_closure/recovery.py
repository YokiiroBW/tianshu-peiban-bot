"""Fixed-ID cutover recovery preparation and explicit native Episode repair.

No SQL business-state updates, model calls, inbox recreation, or message sends.
QQ entries are inspected/planned only: normal ingest can enqueue a real reply,
so root resumes the original task after reviewing the actual event/authorization.
"""

import argparse
import asyncio
from pathlib import Path
import os
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from evaluation.memory_closure.dataset import digest, load_json, private_output, write_json
else:
    from .dataset import digest, load_json, private_output, write_json


SOURCE_REJECTION_ERRORS = {"reflection_material_source_required", "reflection_material_contract_invalid",
                           "reflection_original_evidence_required", "reflection_not_original_evidence"}


def validate_manifest(value):
    if value.get("schema_version") != "memory.closure.recovery.v1" or not value.get("manifest_id"):
        raise ValueError("fixed_recovery_manifest_required")
    seen = set()
    for item in value.get("items", []):
        kind = item.get("kind")
        if kind == "imagined_episode":
            required = {"context", "activity_id", "episode_id", "expected_episode_revision",
                        "expected_activity_revision"}
            key = (kind, item.get("episode_id"))
            if not required <= item.keys() or any(type(item[name]) is not int or item[name] < 1
                    for name in ("expected_episode_revision", "expected_activity_revision")):
                raise ValueError("fixed_activity_episode_and_both_revisions_required")
            if item.get("resume_known_source_rejection") and item.get("expected_growth_error") not in SOURCE_REJECTION_ERRORS:
                raise ValueError("growth_resume_requires_known_source_rejection")
        elif kind == "qq_inbox":
            required = {"event_key", "connection_id", "producer", "event_id", "event_revision", "expected_state"}
            key = (kind, item.get("event_key"))
            if not required <= item.keys() or item["expected_state"] != "accepted":
                raise ValueError("fixed_original_accepted_inbox_identity_required")
        else:
            raise ValueError("unsupported_recovery_kind")
        if not key[1] or key in seen:
            raise ValueError("duplicate_or_empty_recovery_identity")
        seen.add(key)
    if not seen:
        raise ValueError("empty_recovery_manifest")
    return value


def plan(manifest):
    manifest = validate_manifest(manifest)
    return {"manifest_id": manifest["manifest_id"], "manifest_hash": digest(manifest),
        "state": "prepared_not_executed", "items": [{**item,
            "method": "LifeService.repair_completed_imagined_episode" if item["kind"] == "imagined_episode"
                else "inspect original channels.events + runtime.job_keys; root resumes existing channels.ingest",
            "model_execution": "none", "inbox_replay": "none"} for item in manifest["items"]],
        "unknown_attempt_policy": "observe original attempt; never replace or recast as failed",
        "cutover": ["Root finishes all code and one focused verification pass.",
            "Root follows the single-writer cutover/affected-domain backup plan.",
            "Apply the three named migrations through the native migration owner.",
            "Review fixed Memory/Growth conversion inventories before activation.",
            "Repair only these saved completed checkpoints/Episodes; keep original IDs/times.",
            "Review each original QQ event and reply implications; resume the original task identity.",
            "Read back revision/coverage/original attempt state; do not invent completion."]}


def growth_contains(review, episode_id):
    blocked = review.get("blocked_material") or {}
    if blocked.get("episode_id") == episode_id:
        return True
    batch = review.get("batch") or {}
    return any(isinstance(item, (tuple, list)) and item and item[0] == episode_id
               for item in batch.get("inputs", []))


async def apply(manifest, config, output, code_root):
    sys.path.insert(0, str(code_root.resolve() / "src"))
    from tianshu.models.codec import context_from_wire
    from tianshu.runtime.commands import CommandStore
    from tianshu.runtime.composition import RuntimeServices
    from tianshu.runtime.config import Secrets, Settings
    from tianshu.runtime.credentials import CredentialStore
    from tianshu.runtime.database import Database, DatabaseConfig
    from tianshu.runtime.files import FileStore
    from tianshu.runtime.jobs import JobQueue
    from tianshu.runtime.product import build_product
    from tianshu.runtime.serialization import wire

    class NoModelExecution:
        async def generate(self, request):
            raise RuntimeError("fixed_id_recovery_never_generates_a_model_request")

        async def embedding_profile(self):
            raise RuntimeError("fixed_id_recovery_never_calls_a_model")

    variable = config.get("recovery_postgres_env")
    dsn = os.environ.get(variable) if variable else None
    if not dsn:
        raise ValueError("explicit_recovery_postgres_environment_required")
    database = Database(DatabaseConfig(dsn))
    await database.open()
    product = None
    result = {"manifest_id": manifest["manifest_id"], "manifest_hash": digest(manifest), "items": []}
    try:
        # Existing runtime schema/config only. Migration/deployment is root's
        # separate single-writer step, never an implicit side effect of repair.
        jobs = JobQueue(database.config.dsn)
        settings = Settings(config["app_settings"], Secrets(), output.parent)
        runtime = RuntimeServices(database, jobs, NoModelExecution(),
                                  FileStore(settings["artifacts"]), settings)
        runtime.commands = CommandStore(database)
        runtime.credentials = CredentialStore(database, settings.secrets)
        product = await build_product(runtime)
        for module in product.modules:
            if module.register_tasks:
                module.register_tasks(jobs, runtime)
        conversation = product.channels.conversation
        for item in manifest["items"]:
            if item["kind"] == "qq_inbox":
                async with database.transaction(read_only=True) as connection:
                    row = await (await connection.execute(
                        "SELECT id,connection_id,producer,event_id,revision,state,outcome FROM channels.events WHERE id=%s",
                        (item["event_key"],))).fetchone()
                    task = await (await connection.execute(
                        "SELECT key,job_id,task,cancelled_at FROM runtime.job_keys WHERE key=%s",
                        (item["event_key"],))).fetchone()
                    admission = await (await connection.execute(
                        "SELECT e.conversation_id,e.speaker_id,e.disposition,e.occurred_at,"
                        "c.kind AS conversation_kind,c.label,l.mode AS current_mode,l.reply_owner AS current_reply_owner "
                        "FROM identity.events e JOIN identity.conversations c USING(conversation_id) "
                        "LEFT JOIN identity.conversation_links l ON l.connection_id=e.connection_id "
                        "AND l.conversation_id=e.conversation_id WHERE e.connection_id=%s AND e.producer=%s "
                        "AND e.event_id=%s AND e.revision=%s",
                        (item["connection_id"], item["producer"], item["event_id"], item["event_revision"]))).fetchone()
                    turn = await (await connection.execute(
                        "SELECT turn_id,state,context->'delivery_allowed' AS original_delivery_allowed "
                        "FROM conversation.turns WHERE producer=%s AND event_id=%s AND event_revision=%s",
                        (item["producer"], item["event_id"], item["event_revision"]))).fetchone()
                matches = bool(row and all(row[column] == item[field] for column, field in (
                    ("connection_id", "connection_id"), ("producer", "producer"), ("event_id", "event_id"),
                    ("revision", "event_revision"))) and task and task["task"] == "channels.ingest")
                disposition = admission["disposition"] if admission else (row or {}).get("outcome", {})
                if isinstance(disposition, dict):
                    disposition = disposition.get("disposition")
                delivery = turn["original_delivery_allowed"] if turn else None
                possible_reply = (False if disposition in {"observe", "ignore", "ignored"} or delivery is False
                                  else True if disposition == "reply" and delivery is True else None)
                result["items"].append({"kind": "qq_inbox", "event_key": item["event_key"],
                    "state": "planned_only" if matches else "identity_mismatch",
                    "original_event": wire(row), "original_task": wire(task),
                    "original_admission": wire(admission), "original_turn": wire(turn),
                    "original_delivery_allowed": delivery, "possible_normal_reply": possible_reply,
                    "reply_assessment": "native_observe_or_ignored_no_reply" if possible_reply is False
                        else "may_enqueue_normal_reply" if possible_reply is True
                        else "unknown_review_actual_original_before_resuming",
                    "mode_note": "saved admission is original; current link mode is labeled current; missing delivery is unknown",
                    "next": "root reviews original event and resumes original job id; no new event/raw replay"})
                write_json(output, result)
                continue
            context = context_from_wire(item["context"])
            await product.authority.identity.check(context)
            before = (await conversation.growth.view(context))["review"] or {}
            repaired = await conversation.life.repair_completed_imagined_episode(
                context, item["activity_id"], item["episode_id"], item["expected_episode_revision"],
                expected_activity_revision=item["expected_activity_revision"])
            after = (await conversation.growth.view(context))["review"] or {}
            review_result = {"state": "existing_observer_or_original_attempt_controls_resume"}
            if (item.get("resume_known_source_rejection")
                    and before.get("last_error") == item["expected_growth_error"]
                    and growth_contains(before, item["episode_id"])
                    and after.get("last_error") == item["expected_growth_error"]
                    and after.get("can_request_review") is True):
                review_result = wire(await conversation.growth.request_review(context, {
                    "client_request_id": "memory-repair-review:" + digest({"manifest": digest(manifest),
                        "episode_id": item["episode_id"], "revision": item["expected_episode_revision"]})[:64],
                    "expected_revision": after["revision"]}))
            result["items"].append({"kind": item["kind"], "episode_id": item["episode_id"],
                "repair": wire(repaired), "growth_before": wire(before), "growth_after": wire(after),
                "growth_review": review_result})
            write_json(output, result)
        result["state"] = "fixed_episode_repair_complete; inbox_entries_planned_only"
        write_json(output, result)
    finally:
        if product is not None:
            await product.capabilities.close()
        await database.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "apply"))
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--private-root", required=True, type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--code-root", type=Path)
    args = parser.parse_args()
    try:
        manifest = validate_manifest(load_json(args.manifest))
        output = private_output(args.output, args.private_root)
        if args.mode == "plan":
            write_json(output, plan(manifest))
        else:
            if args.config is None or args.code_root is None:
                raise ValueError("apply_requires_private_config_and_integrated_code_root")
            if sys.platform == "win32":
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            asyncio.run(apply(manifest, load_json(args.config), output, args.code_root))
    except Exception as error:
        print(getattr(error, "code", str(error) if isinstance(error, ValueError)
                      else type(error).__name__), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
