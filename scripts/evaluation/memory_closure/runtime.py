"""Serial driver of the selected checkout's native product graph, never a second Memory."""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from uuid import uuid4
import os
import traceback

from .broker import EvaluationModels
from .dataset import comparison_key, digest, stream_plan, write_json
from .metering import aggregate_calls


# The conservative arm retains generous context/candidate/output budgets while
# dividing the SAME arrived prefix into smaller native batches. Optimized is the
# shipped default, except the wall-clock quiet delay is replaced by checkpoints.
CONSERVATIVE_POLICY = {"quiet_seconds": 0, "max_input_tokens": 32000,
    "max_output_tokens": 4096, "reasoning_reserve_tokens": 8192, "max_episodes": 2,
    "max_subjects": 16, "max_materials": 8, "max_old_records": 64,
    "context_messages": 12, "max_source_characters": 12000}
OPTIMIZED_POLICY = {"quiet_seconds": 0}


@asynccontextmanager
async def disposable_database(config, output):
    """Same explicit admin variable and tianshu_test_UUID ownership as tests/conftest.py."""
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from tianshu.runtime.database import Database, DatabaseConfig

    dsn = os.environ.get(config.get("postgres_admin_env", "TIANSHU_TEST_POSTGRES_ADMIN_DSN"))
    if not dsn or config.get("isolated_postgres") is not True:
        raise ValueError("explicit_isolated_postgres_admin_required")
    if conninfo_to_dict(dsn).get("port") == "25018":
        raise ValueError("production_postgres_port_forbidden")
    name = "tianshu_test_" + uuid4().hex
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
    database = None
    created = False
    try:
        await admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        created = True
        database = Database(DatabaseConfig(make_conninfo(dsn, dbname=name)))
        await database.open()
        write_json(Path(output) / "database.json", {"database_name": name,
            "isolated": True, "retained": bool(config.get("keep_database", False))})
        yield database
    finally:
        if database is not None:
            await database.close()
        if created and not config.get("keep_database", False):
            # Only the UUID name created above can be removed, never a supplied target.
            await admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        if created:
            write_json(Path(output) / "database.json", {"database_name": name, "isolated": True,
                "retained": bool(config.get("keep_database", False)),
                "retained_reason": config.get("retained_reason")})
        await admin.close()


async def build_graph(database, config, output, run_id, variant):
    from tianshu.runtime.commands import CommandStore
    from tianshu.runtime.composition import RuntimeServices
    from tianshu.runtime.config import Secrets, Settings
    from tianshu.runtime.credentials import CredentialStore
    from tianshu.runtime.files import FileStore
    from tianshu.runtime.jobs import JobQueue
    from tianshu.runtime.migrations import migrate, product_migrations
    from tianshu.runtime.product import build_product

    class EvaluationQueue(JobQueue):
        def __init__(self, dsn):
            super().__init__(dsn)
            self.native_handlers = {}

        def register(self, name, handler, **kwargs):
            self.native_handlers[name] = handler
            return super().register(name, handler, **kwargs)

    jobs = EvaluationQueue(database.config.dsn)
    await migrate(database, product_migrations(jobs))
    policy = dict(CONSERVATIVE_POLICY if variant == "conservative" else OPTIMIZED_POLICY)
    policy.update(config.get("policies", {}).get(variant, {}))
    policy["quiet_seconds"] = 0
    document = {"identity": {"installation_seal": str(output / "installed"), "setup_token_ref": None},
        "artifacts": str(output / "files"),
        "capabilities": {"state_directory": str(output / "capabilities")},
        "channels": {"owner_url": "http://127.0.0.1:9", "owner_token_ref": "EVAL_OWNER"},
        "context": config.get("context", {}),
        "memory": {"consolidation": policy, "retrieval": config.get("retrieval", {})}}
    settings = Settings(document, Secrets({"EVAL_OWNER": "synthetic-unused-channel-owner"}), output)
    models = EvaluationModels(database, None, config, output, run_id)
    runtime = RuntimeServices(database, jobs, models, FileStore(output / "files"), settings)
    runtime.commands = CommandStore(database)
    runtime.credentials = CredentialStore(database, settings.secrets)
    product = await build_product(runtime)
    models.sources = product.authority.sources
    for module in product.modules:
        if module.register_tasks:
            module.register_tasks(jobs, runtime)
    await models.open()
    # No domain.start or general worker is started. Only native memory handlers
    # are driven below; no clock, contact, delivery, or unrelated paid work runs.
    return product, jobs, models


async def prepare_scene(product, story, events, models):
    from tianshu.identity.management import IdentityManagement
    from tianshu.identity.types import IncomingEvent

    conversation = product.channels.conversation
    identity = product.authority.identity
    result, _ = await identity.sessions.setup({"username": "synthetic_owner",
        "password": "synthetic-evaluation-password", "client_request_id": "synthetic-setup"})
    owner = await identity.context(result.data.principal.principal_id)
    actor = await conversation.companion.save_actor(owner, {"name": "合成记忆评测角色",
        "enabled": True, "expected_revision": 0, "client_request_id": "synthetic-actor"})
    actor_id = actor.data["actor_id"]
    models.allowed_actors.add(actor_id)
    management = IdentityManagement(identity)
    connection_id = "synthetic-evaluation-connection"
    await management.execute(owner, "connections.save", {"connection_id": connection_id,
        "actor_id": actor_id, "namespace": "synthetic", "self_external_id": "evaluation-bot",
        "expected_revision": 0, "client_request_id": "synthetic-connection"})
    group = await management.execute(owner, "conversations.link", {"connection_id": connection_id,
        "external_id": "synthetic-room", "kind": "group", "mode": "observe", "label": "合成评测群",
        "target_ref": "group:synthetic-room", "expected_revision": 0,
        "client_request_id": "synthetic-room"})
    context = await identity.context(owner.principal_id, actor_id=actor_id,
                                     conversation_id=group.data["conversation_id"])
    roster = {}
    # Static roster only. Empty identity-admission events are never ingested as
    # messages/Sources/Episodes; no future message body/question/rubric is exposed.
    for index, name in enumerate(dict.fromkeys(event["speaker"] for event in events)):
        decision = await identity.admit_event(connection_id, IncomingEvent(
            "synthetic:" + connection_id, "synthetic-roster:" + str(index), 1,
            events[0]["occurred_at"] - timedelta(seconds=1), "synthetic-room", "group",
            "speaker-" + str(index), sender_display_name=name))
        if decision.context is None:
            raise ValueError("synthetic_roster_admission_failed")
        roster[name] = {"external_id": "speaker-" + str(index),
                        "person_id": decision.context.person_id}
    return context, connection_id, roster


async def table_rows(database, schema, table):
    from psycopg import sql

    async with database.transaction(read_only=True) as connection:
        exists = await (await connection.execute("SELECT to_regclass(%s) AS name",
                                                  (schema + "." + table,))).fetchone()
        if not exists["name"]:
            return []
        rows = await (await connection.execute(sql.SQL(
            "SELECT to_jsonb(item)-'embedding' AS body FROM {}.{} item").format(
                sql.Identifier(schema), sql.Identifier(table)))).fetchall()
    return [row["body"] for row in rows]


async def snapshot(database):
    names = ("episodes", "claims", "claim_revisions", "matters", "matter_revisions",
             "semantic_links", "reflections", "consolidation_state", "consolidation_batches", "model_steps")
    result = {name: await table_rows(database, "memory", name) for name in names}
    result["sources"] = await table_rows(database, "sources", "versions")
    result["actions"] = await table_rows(database, "capabilities", "actions")
    result["queue_keys"] = await table_rows(database, "runtime", "job_keys")
    return result


async def database_statistics(database):
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute(
            "SELECT xact_commit,xact_rollback,blks_read,blks_hit,tup_returned,tup_fetched,"
            "tup_inserted,tup_updated,tup_deleted,deadlocks,blk_read_time,blk_write_time "
            "FROM pg_stat_database WHERE datname=current_database()" )).fetchone()
    return dict(row)


def progress_key(episodes, batches=()):
    # A known truncation creates native shrink work without advancing an Episode
    # cursor. Batch progress matters too; timestamps/physical row order do not.
    return digest({"episodes": [{key: row.get(key) for key in (
        "episode_id", "revision", "extraction_state", "extraction_cursor", "extraction_missing")}
        for row in sorted(episodes, key=lambda item: item["episode_id"])],
        "batches": [{"batch_id": row["batch_id"], "status": row["status"],
            "error_code": row.get("error_code"), "step_ids": row.get("step_ids"),
            "source_budget": (row.get("manifest") or {}).get("source_budget")}
            for row in sorted(batches, key=lambda item: item["batch_id"])]})


async def flush_memory(database, jobs, variant, output, stage, maximum=128):
    """Native handlers, bounded progress, no arbitrary changes to cursors/states."""
    traces = []
    for iteration in range(maximum):
        episodes = await table_rows(database, "memory", "episodes")
        batches = await table_rows(database, "memory", "consolidation_batches") if variant != "legacy" else ()
        before = progress_key(episodes, batches)
        pending = [row for row in episodes if row.get("active")
                   and row.get("extraction_state") in {"pending", "running"}]
        if variant == "legacy":
            work = [("memory.extract", {"episode_id": row["episode_id"], "sequence": row["sequence"]})
                    for row in sorted(pending, key=lambda item: item["sequence"])]
        else:
            states = await table_rows(database, "memory", "consolidation_state")
            work = [("memory.consolidate", {"partition_id": row["partition_id"]})
                    for row in states if row.get("active_batch_id") or row.get("wake_due_at")]
        if not work:
            break
        for task, arguments in work:
            started = perf_counter()
            await jobs.native_handlers[task](**arguments)
            traces.append({"stage": stage, "iteration": iteration, "task": task,
                           "arguments": arguments, "elapsed_ms": (perf_counter() - started) * 1000})
        after_rows = await table_rows(database, "memory", "episodes")
        after_batches = await table_rows(database, "memory", "consolidation_batches") if variant != "legacy" else ()
        after = progress_key(after_rows, after_batches)
        if after == before:
            # In particular do not spin/reissue a submitted unknown or failed batch.
            traces.append({"stage": stage, "state": "no_progress_observe_original_or_review_error"})
            break
    else:
        traces.append({"stage": stage, "state": "native_step_limit_reached"})
    write_json(output / (stage + "-dispatch.json"), traces)
    return traces


async def index_changed(database, jobs, indexed, variant):
    work = []
    claims = await table_rows(database, "memory", "claims")
    revisions = await table_rows(database, "memory", "claim_revisions")
    current = {(row["claim_id"], row["current_revision"]) for row in claims}
    for row in revisions:
        if (row["claim_id"], row["revision"]) in current and row["state"] == "active":
            work.append(("hypothesis" if row.get("memory_kind") == "hypothesis" else "claim",
                         row["claim_id"], row["revision"]))
    if variant != "legacy":
        for kind, table, key in (("episode", "episodes", "episode_id"),
                ("matter", "matters", "matter_id"), ("reflection", "reflections", "proposal_id")):
            work.extend((kind, row[key], row["revision"]) for row in
                        await table_rows(database, "memory", table) if row["active"])
    timing = []
    for kind, object_id, revision in work:
        identity = (kind, object_id, revision)
        if identity in indexed:
            continue
        started = perf_counter()
        if variant == "legacy":
            await jobs.native_handlers["memory.index"](claim_id=object_id, revision=revision)
        else:
            await jobs.native_handlers["memory.index_object"](kind=kind, id=object_id, revision=revision)
        indexed.add(identity)
        timing.append({"kind": kind, "id": object_id, "revision": revision,
                       "elapsed_ms": (perf_counter() - started) * 1000})
    return timing


def content_incomplete(result):
    completion = getattr(result, "completion", None)
    if completion is not None and completion.state == "incomplete":
        return True
    # The actual old checkout predates ModelCompletion. Preserve its product
    # behavior; the evaluation answer still labels a reported truncation honestly.
    native = result.native_response or {}
    if native.get("status") == "incomplete":
        return True
    reasons = [native.get("finish_reason"), native.get("stop_reason")]
    reasons.extend(choice.get("finish_reason") for choice in (native.get("choices") or [])
                   if isinstance(choice, dict))
    return any(reason in {"length", "max_tokens", "max_output_tokens", "content_filter"}
               for reason in reasons if isinstance(reason, str))


async def answer(product, models, context, question, config, output):
    from tianshu.models.types import ModelMessage, ModelPart, ModelRequest, ToolSpec
    from tianshu.runtime.serialization import wire
    from tianshu.sources.dependencies import unique_refs
    from tianshu.types import Action, OriginKind, RequestBudget, ServiceClass, SourceRef

    conversation = product.channels.conversation
    budget = RequestBudget(datetime.now(UTC) + timedelta(seconds=config.get("answer_seconds", 120)))
    question_refs = await product.authority.sources.record_event(context, producer="web",
        event_id="synthetic-final-question", revision=1, occurred_at=datetime.now(UTC),
        parts=({"kind": "text", "text": question},), origin_kind=OriginKind.HUMAN)
    # A real, paused synthetic activity supplies the native one-trace lifecycle
    # identity. It is never activated, dispatched, or represented as an action success.
    activity = await conversation.life.save_activity(context, {"title": "合成评测中读取记忆依据",
        "kind": "memory_evaluation", "state": "paused", "waiting_reason": "evaluation_read_only",
        "source_refs": wire(question_refs), "expected_revision": 0,
        "client_request_id": "synthetic-evidence-activity"})
    choices = [choice for choice in await conversation.catalog.discover(context)
               if choice["definition"].id.startswith("memory.")
               and choice["definition"].effect == "read" and choice["can_execute"]]
    tool_map = {}
    tools = []
    for choice in choices:
        definition = choice["definition"]
        name = (definition.model_tool or {}).get("name", definition.id.replace(".", "_"))
        tool_map[name] = definition.id
        tools.append(ToolSpec(name, definition.description, definition.input_schema))
    pack = await conversation.assembler.assemble(context, "chat", question, budget,
        persona_text=config.get("persona_text", "你是天枢的合成评测角色。按授权记忆回答，缺少依据时说明未知。"),
        tools=tuple(tools), max_input_tokens=config.get("answer_input_tokens", 16000),
        reserved_output_tokens=config.get("answer_output_tokens", 2048))
    write_json(output / "context-pack.json", wire(pack))
    dependencies = unique_refs(question_refs, pack.source_refs)
    # The assembler supplies its dynamic memory materials afresh on each round;
    # only conversation/tool history belongs in the continuation.
    continuation = [ModelMessage("user", (ModelPart("text", text=question),))]
    transcript = []
    final_text = None
    for round_number in range(config.get("max_answer_rounds", 4)):
        budget.check()
        # Reuse the native whole-unit budget for growing tool continuations.
        current_pack = await conversation.assembler.assemble(context, "chat", question, budget,
            persona_text=config.get("persona_text", "你是天枢的合成评测角色。按授权记忆回答，缺少依据时说明未知。"),
            recent_messages=tuple(continuation), tools=tuple(tools),
            max_input_tokens=config.get("answer_input_tokens", 16000),
            reserved_output_tokens=config.get("answer_output_tokens", 2048)) if round_number else pack
        request = ModelRequest("synthetic-answer:" + str(round_number),
            "synthetic-answer:" + str(round_number), "chat", ServiceClass.INTERACTIVE,
            context, budget, tuple(current_pack.messages), system=current_pack.system,
            tools=tuple(tools), required_capabilities=frozenset({"text", "tools"} if tools else {"text"}),
            max_output_tokens=config.get("answer_output_tokens", 2048),
            estimated_input_tokens=current_pack.token_usage.estimated_tokens,
            source_refs=unique_refs(dependencies, current_pack.source_refs))
        result = await models.generate(request)
        transcript.append({"round": round_number, "request": wire(request), "result": wire(result)})
        write_json(output / "answer-transcript.json", transcript)
        if result.receipt.status != "succeeded":
            break
        if content_incomplete(result):
            break
        result_parts = tuple(replace(part, call_id=part.call_id or
            "evaluation-call-" + str(round_number) + "-" + str(index)) if part.kind == "tool_call" else part
            for index, part in enumerate(result.parts))
        calls = [part for part in result_parts if part.kind == "tool_call"]
        if not calls:
            final_text = "\n".join(part.text for part in result.parts if part.kind == "text" and part.text)
            break
        continuation.append(ModelMessage("assistant", result_parts))
        for index, call in enumerate(calls):
            if call.name not in tool_map:
                raise ValueError("evaluation_tool_outside_memory_read_scope")
            action_id = str(uuid4())
            action = Action(action_id, "evaluation-answer:" + str(round_number) + ":" + str(index),
                tool_map[call.name], context.actor_id, context, call.arguments or {}, OriginKind.HUMAN,
                dependencies, activity_id=activity.data["id"], deadline_at=budget.deadline_at,
                current_input_refs=question_refs)
            receipt = await conversation.actions.invoke(action)
            transcript.append({"round": round_number, "action": wire(action), "receipt": wire(receipt)})
            # Only actual returned source refs can authorize a later raw trace.
            def selected_refs(value):
                found = []
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key == "source_refs" and isinstance(item, list):
                            found.extend(SourceRef(**ref) for ref in item if isinstance(ref, dict)
                                         and {"source_id", "revision", "locator"} <= ref.keys())
                        else:
                            found.extend(selected_refs(item))
                elif isinstance(value, list):
                    for item in value:
                        found.extend(selected_refs(item))
                return found
            returned = selected_refs(wire(receipt.result))
            await product.authority.sources.check(context, tuple(returned))
            dependencies = unique_refs(dependencies, returned)
            continuation.append(ModelMessage("tool", (ModelPart("tool_result",
                call_id=call.call_id, name=call.name, result=wire(receipt)),)))
            write_json(output / "answer-transcript.json", transcript)
    return {"text": final_text, "state": "answered" if final_text else "incomplete_or_unavailable",
            "rounds": len([item for item in transcript if "request" in item]),
            "context_missing": wire(pack.missing), "human_semantic_verdict": "pending_human_review"}


async def run_story(story, corpus_hash, config, output, run_id, variant, provenance):
    from tianshu.conversation.types import InputMessage, InputPart
    from tianshu.identity.types import IncomingEvent
    from tianshu.runtime.serialization import wire

    output.mkdir(parents=True, exist_ok=False)
    config = dict(config)
    stream = config.get("stream", {})
    events = stream_plan(story, stream)
    report = {"story_id": story.id, "split": story.split, "variant": variant,
        "comparison_key": comparison_key(story, corpus_hash, stream), "corpus_hash": corpus_hash,
        "code": provenance, "run_id": run_id, "state": "running",
        "evaluation_kind": "real_broker_synthetic_material", "semantic_verdict": "pending_human_review",
        "time_semantics": "occurred_at follows synthetic input; recorded_at remains actual native write time; no backdating",
        "dispatch_mode": "serial_native_memory_handlers; model_queue_uses_existing_broker",
        "index_timings": [], "stream_checkpoints": [], "error_code": None}
    report["execution_config_fingerprint"] = digest({"model_context": config["model_context"],
        "persona_text": config.get("persona_text", "你是天枢的合成评测角色。按授权记忆回答，缺少依据时说明未知。"),
        "context": config.get("context", {}),
        "retrieval": config.get("retrieval", {}), "answer_seconds": config.get("answer_seconds", 120),
        "max_answer_rounds": config.get("max_answer_rounds", 4),
        "answer_input_tokens": config.get("answer_input_tokens", 16000),
        "answer_output_tokens": config.get("answer_output_tokens", 2048),
        "max_native_steps": config.get("max_native_steps", 128)})
    write_json(output / "report.json", report)
    product = models = None
    started = perf_counter()
    async with disposable_database(config, output) as database:
        try:
            product, jobs, models = await build_graph(database, config, output, run_id, variant)
            context, connection_id, roster = await prepare_scene(product, story, events, models)
            report["subjects"] = roster
            report["policy"] = wire(product.channels.conversation.memory.consolidation.policy) if variant != "legacy" else {
                "default": "actual_single_episode_worker_from_" + provenance["source_sha"]}
            report["sql_statistics_before"] = await database_statistics(database)
            indexed = set()
            for event in events:
                # Only this arrived message is passed to native admission/ingest.
                models.stage = "ingest:" + str(event["index"])
                person = roster[event["speaker"]]
                parts = (InputPart("text", event["text"]),)
                event_id = "synthetic-message:" + str(event["index"])
                incoming = IncomingEvent("synthetic:" + connection_id, event_id, 1,
                    event["occurred_at"], "synthetic-room", "group", person["external_id"],
                    parts=({"kind": "text", "text": event["text"]},), sender_display_name=event["speaker"])
                decision = await product.authority.identity.admit_event(connection_id, incoming)
                if decision.context is None:
                    raise ValueError("synthetic_message_not_admitted")
                received = await product.channels.conversation.ingest(decision.context,
                    InputMessage(incoming.producer, event_id, 1, event["occurred_at"], datetime.now(UTC),
                        parts, reply_required=False, conversation_kind="group", autonomous_eligible=False))
                write_json(output / ("arrival-" + str(event["index"]) + ".json"),
                           {"event": wire(event), "admission": wire(decision), "received": wire(received)})
                if event["flush"]:
                    stage = "checkpoint-" + str(event["index"])
                    models.stage = stage
                    await flush_memory(database, jobs, variant, output, stage,
                                       config.get("max_native_steps", 128))
                    models.stage = stage + ":index"
                    report["index_timings"].extend(await index_changed(database, jobs, indexed, variant))
                    state = await snapshot(database)
                    write_json(output / (stage + "-state.json"), wire(state))
                    report["stream_checkpoints"].append({"arrived_messages": event["index"],
                        "last_input_occurred_at": wire(event["occurred_at"]),
                        "actual_snapshot_at": datetime.now(UTC).isoformat(),
                        "state_file": stage + "-state.json"})
            models.stage = "answer"
            answer_started = perf_counter()
            report["answer"] = await answer(product, models, context, story.question, config, output)
            report["answer_elapsed_ms"] = (perf_counter() - answer_started) * 1000
            report["state"] = "completed_for_human_review"
        except Exception as error:
            report["state"] = "failed_or_unknown"
            report["error_code"] = getattr(error, "code", type(error).__name__)
            report["error_location"] = [{"file": Path(frame.filename).name,
                "line": frame.lineno, "function": frame.name}
                for frame in traceback.extract_tb(error.__traceback__)]
            config["keep_database"] = True
            config["retained_reason"] = "failed_evaluation_preserves_original_application_state"
            # No exception str/trace containing config/DSN/token is exported.
        finally:
            if models is not None and any(item["state"] not in {"succeeded", "failed", "cancelled"}
                                           for item in models.mapping.values()):
                config["keep_database"] = True
                config["retained_reason"] = "original_model_attempt_unresolved; observe_only"
            if product is not None:
                final_state = await snapshot(database)
                write_json(output / "final-state.json", wire(final_state))
                report["episode_coverage"] = [{key: row.get(key) for key in (
                    "episode_id", "person_id", "speaker_id", "revision", "extraction_state",
                    "extraction_cursor", "extraction_missing", "coverage")}
                    for row in final_state["episodes"]]
                report["batch_results"] = [{"batch_id": row["batch_id"], "status": row["status"],
                    "error_code": row.get("error_code"),
                    "target_results": (row.get("commit_refs") or {}).get("target_results"),
                    "unresolved": (row.get("commit_refs") or {}).get("unresolved"),
                    "context_unfulfilled": (row.get("commit_refs") or {}).get("context_unfulfilled")}
                    for row in final_state["consolidation_batches"]]
                report["sql_statistics_after"] = await database_statistics(database)
                report["sql_measurement"] = {"statement_latency_ms": None,
                    "scope": "actual isolated pg_stat_database counters; stats may lag; no SQL cost inferred"}
                await product.capabilities.close()
            if models is not None:
                report["model_usage"] = aggregate_calls(models.calls)
                await models.close()
    report["elapsed_ms"] = (perf_counter() - started) * 1000
    write_json(output / "review.json", {"story_id": story.id, "question": story.question,
        "answer": report.get("answer"), **story.rubric()})
    write_json(output / "report.json", report)
    return report
