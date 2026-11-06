"""Native admission keeps observer state separate from committed business execution."""

import pytest

from test_rbj_execution_repair import args, context, enqueue_repair, exhaust_normal, grant_on, repair_queue
from test_rbj_redaction import envelopes
from tianshu.runtime import jobs as runtime_jobs
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec


async def marked_normal(conn, queue, generation, event, key="new-normal", **kwargs):
    return await queue.enqueue(conn, JobSpec("conversation.result", key, {"event": event},
        data_generation=generation, execution_identity="original-result"), **kwargs)


def as_current(job_id, ordinal, generation):
    return runtime_jobs._execution_identity.set((job_id, ordinal, generation))


@pytest.mark.parametrize("purpose", ["normal", "repair"])
async def test_foreign_history_observer_preserves_current_native_admission(database, repair_queue, purpose):
    queue, _task, state = repair_queue
    async with database.transaction() as conn:
        if purpose == "repair":
            generation, digest = await exhaust_normal(conn, queue, state["event"])
            assert (await grant_on(conn, queue, generation, digest))["eligible"]
            job_id, payload = await enqueue_repair(conn, queue, generation, state["event"])
        else:
            generation = await queue.current_generation_on(conn)
            await queue.seed_execution_history_on(conn, **args(generation), history=[], complete=True)
            job_id = await marked_normal(conn, queue, generation, state["event"])
            payload = {"event": state["event"]}
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
    selected = {"repair_ref": "fix:source-eval", "consumer": "life_activity"} if purpose == "repair" else {}
    async with database.transaction() as conn:
        page = await queue.advance_execution_history_on(conn, **args(generation), job_id=job_id,
                                                         task_name="conversation.result")
        assert page["complete"] and len(page["native_inflight"]) == 1
        assert page["native_inflight"][0]["purpose"] == purpose and page["executions"] == []
        await queue.seed_execution_history_on(conn, **args(generation), history=[], complete=True)
        foreign = await queue.inspect_execution_on(conn, **args(generation), **selected)
        assert foreign["native_inflight_complete"] and foreign["reason"] == "execution_in_progress"
        assert foreign["unknown_held"] == 0 and not foreign["native_inflight"][0]["is_current"]
        if purpose == "repair":
            assert foreign["repair"]["reason"] == "execution_in_progress"
            again = await queue.bind_execution_repair_job_on(conn, **args(generation),
                repair_ref="fix:source-eval", consumer="life_activity", job_id=job_id)
            assert not again["bound"] and again["reason"] == "execution_in_progress"
        batch = await queue.observe_execution_history_on(conn, job_ids=[job_id], data_generation=generation,
                                                          task_name="conversation.result")
        assert batch["complete"] and batch["executions"] == []
        assert batch["native_inflight"][0]["job_id"] == job_id
        with pytest.raises(AppError, match="execution_current_job_mismatch"):
            await queue.inspect_execution_on(conn, **args(generation), **selected,
                current_job={"data_generation": generation, "job_id": job_id, "native_attempt": 0})
    token = as_current(job_id, 0, generation)
    try:
        current = {"data_generation": generation, "job_id": job_id, "native_attempt": 0}
        async with database.transaction() as conn:
            own = await queue.inspect_execution_on(conn, **args(generation), **selected, current_job=current)
            assert own["native_inflight"][0]["is_current"]
            assert (own["repair"]["eligible"] if purpose == "repair" else own["eligible"])
        # A foreign observer may run after cheap admission but before the begin transaction.
        async with database.transaction() as conn:
            page = await queue.advance_execution_history_on(conn, **args(generation), job_id=job_id,
                                                             task_name="conversation.result")
            assert page["native_inflight"][0]["job_id"] == job_id
            await queue.seed_execution_history_on(conn, **args(generation), history=[], complete=True)
        async with database.transaction() as conn:
            started = await queue.begin_execution_on(conn, **args(generation), job_id=job_id,
                                                      native_attempt=0, **selected)
            assert started["eligible"]
    finally:
        runtime_jobs._execution_identity.reset(token)
    async with database.transaction() as conn:
        await queue.advance_execution_history_on(conn, **args(generation), job_id=job_id,
                                                  task_name="conversation.result")
        fact = await (await conn.execute("SELECT outcome,evidence_ref FROM runtime.rbi_execution_facts "
                                         "WHERE data_generation=%s AND job_id=%s AND native_attempt=0",
                                         (generation, job_id))).fetchone()
        assert fact == {"outcome": "started", "evidence_ref": None}


async def test_only_new_physical_normal_jobs_receive_trusted_admission(database, repair_queue):
    queue, _task, state = repair_queue
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        payload = {"event": state["event"]}
        legacy = await queue.enqueue(conn, JobSpec("conversation.result", "legacy-pending", payload,
            data_generation=generation), queueing_lock="legacy-wake")
        reused = await marked_normal(conn, queue, generation, state["event"], key="new-alias",
                                     queueing_lock="legacy-wake")
        assert reused == legacy
        assert not await (await conn.execute("SELECT 1 FROM runtime.rbj_execution_admissions WHERE job_id=%s", (legacy,))).fetchone()
        unlocked = await queue.enqueue(conn, JobSpec("conversation.result", "legacy-unlocked", payload,
            data_generation=generation))
        await queue.adopt_wake_on(conn, unlocked, expected_task="conversation.result", expected_generation=generation,
                                   expected_payload=payload, wake_key="legacy-wake")
        assert not await (await conn.execute("SELECT 1 FROM runtime.rbj_execution_admissions WHERE job_id=%s", (unlocked,))).fetchone()
        fresh = await marked_normal(conn, queue, generation, state["event"], key="fresh-marked", queueing_lock="fresh-wake")
        marker = await (await conn.execute("SELECT owner,policy FROM runtime.rbj_execution_admissions WHERE job_id=%s", (fresh,))).fetchone()
        assert marker == {"owner": "conversation.result", "policy": "normal_begin_required"}
        with pytest.raises(AppError, match="execution_admission_identity_conflict"):
            await queue.enqueue(conn, JobSpec("conversation.result", "fresh-marked", payload,
                data_generation=generation, execution_identity="different-result"), queueing_lock="fresh-wake")
        with pytest.raises(AppError, match="execution_admission_scope_invalid"):
            await queue.enqueue(conn, JobSpec("conversation.result", "fake-marked-repair",
                {**payload, "repair_ref": "untrusted"}, data_generation=generation, execution_identity="original-result"))
        fake = await queue.enqueue(conn, JobSpec("conversation.result", "payload-cannot-self-authorize",
            {**payload, "execution_identity": "original-result"}, data_generation=generation))
        assert not await (await conn.execute("SELECT 1 FROM runtime.rbj_execution_admissions WHERE job_id=%s", (fake,))).fetchone()


async def test_marked_native_success_without_begin_or_exit_remains_unknown(database, repair_queue):
    queue, _task, state = repair_queue
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        await queue.seed_execution_history_on(conn, **args(generation), history=[], complete=True)
        job_id = await marked_normal(conn, queue, generation, state["event"])
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
        await conn.execute("SELECT procrastinate_finish_job_v1(%s,'succeeded',false)", (job_id,))
        page = await queue.advance_execution_history_on(conn, **args(generation), job_id=job_id,
                                                         task_name="conversation.result")
        assert page["complete"]
        status = await queue.seed_execution_history_on(conn, **args(generation), history=[], complete=True)
        assert status["unknown_held"] == 1 and status["reason"] == "execution_outcome_unknown"
        fact = await (await conn.execute("SELECT outcome,purpose FROM runtime.rbi_execution_facts "
                                         "WHERE data_generation=%s AND job_id=%s", (generation, job_id))).fetchone()
        assert fact == {"outcome": "unknown", "purpose": "normal"}


async def test_marked_normal_preflight_stop_is_exact_and_unmarked_is_unchanged(database, repair_queue):
    queue, _task, state = repair_queue
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        await queue.seed_execution_history_on(conn, **args(generation), history=[], complete=True)
        marked = await marked_normal(conn, queue, generation, state["event"])
        legacy = await queue.enqueue(conn, JobSpec("conversation.result", "unmarked", {"event": state["event"]},
                                                    data_generation=generation))
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=ANY(%s::bigint[])", ([marked, legacy],))
    stop_args = {"owner": "conversation.result", "identity": "original-result", "data_generation": generation,
                 "native_attempt": 0, "reason": "history_more", "observation_ref": "page:exact"}
    with pytest.raises(AppError, match="execution_current_job_mismatch"):
        async with database.transaction() as conn:
            await queue.record_execution_preflight_stop_on(conn, job_id=marked, **stop_args)
    token = as_current(legacy, 0, generation)
    try:
        async with database.transaction() as conn:
            unmarked = await queue.record_execution_preflight_stop_on(conn, job_id=legacy, **stop_args)
            assert unmarked["state"] == "unregistered"
    finally:
        runtime_jobs._execution_identity.reset(token)
    token = as_current(marked, 0, generation)
    try:
        async with database.transaction() as conn:
            stopped = await queue.record_execution_preflight_stop_on(conn, job_id=marked, **stop_args)
            assert stopped["state"] == "preflight_stopped" and stopped["purpose"] == "normal"
            assert await queue.record_execution_preflight_stop_on(conn, job_id=marked, **stop_args) == stopped
            with pytest.raises(AppError, match="execution_preflight_conflict"):
                await queue.record_execution_preflight_stop_on(conn, job_id=marked,
                    **{**stop_args, "observation_ref": "different-proof"})
            denied = await queue.begin_execution_on(conn, **args(generation), job_id=marked, native_attempt=0)
            assert not denied["eligible"] and denied["reason"] == "execution_preflight_stopped"
    finally:
        runtime_jobs._execution_identity.reset(token)
    async with database.transaction() as conn:
        await conn.execute("SELECT procrastinate_finish_job_v1(%s,'succeeded',false)", (marked,))
        history = await queue.observe_execution_history_on(conn, job_ids=[marked], data_generation=generation,
                                                            task_name="conversation.result")
        assert history["complete"] and history["executions"] == []


async def imported_repair_unknown(conn, queue, event):
    generation, digest = await exhaust_normal(conn, queue, event)
    assert (await grant_on(conn, queue, generation, digest))["eligible"]
    job_id, _payload = await enqueue_repair(conn, queue, generation, event)
    await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
    started = await (await conn.execute("SELECT id FROM procrastinate_events WHERE job_id=%s AND type='started' "
                                        "ORDER BY id DESC LIMIT 1", (job_id,))).fetchone()
    evidence_ref = "native-job-event:" + str(started["id"])
    await queue.seed_execution_history_on(conn, **args(generation), history=[{
        "job_id": job_id, "native_attempt": 0, "outcome": "unknown", "evidence_ref": evidence_ref}], complete=True)
    return generation, job_id, started["id"], evidence_ref


async def end_original_and_scan(conn, queue, generation, job_id):
    await conn.execute("SELECT procrastinate_retry_job_v2(%s,now(),NULL,NULL,NULL)", (job_id,))
    page = await queue.advance_execution_history_on(conn, **args(generation), job_id=job_id,
                                                     task_name="conversation.result")
    assert page["complete"]
    await queue.seed_execution_history_on(conn, **args(generation), history=[], complete=True)


async def test_observed_prebegin_unknown_resolves_without_rewriting_original_fact(database, repair_queue):
    queue, _task, state = repair_queue
    async with database.transaction() as conn:
        generation, job_id, start_id, evidence_ref = await imported_repair_unknown(conn, queue, state["event"])
        original = await (await conn.execute(
            "SELECT outcome,evidence_ref,recorded_at,repair_grant_id FROM runtime.rbi_execution_facts "
            "WHERE data_generation=%s AND job_id=%s AND native_attempt=0", (generation, job_id))).fetchone()
        await end_original_and_scan(conn, queue, generation, job_id)
        resolved = await queue.resolve_execution_on(conn, owner="conversation.result", identity="original-result",
            data_generation=generation, job_id=job_id, native_attempt=0, outcome="not_started",
            observation_ref="verified-preflight:original")
        assert resolved["recorded"] and resolved["outcome"] == "not_started"
        assert resolved["failed_executions"] == 4 and resolved["repair_failed_executions"] == 0
        assert resolved["unknown_held"] == 0 and resolved["repair"]["remaining"] == 1
        again = await queue.resolve_execution_on(conn, owner="conversation.result", identity="original-result",
            data_generation=generation, job_id=job_id, native_attempt=0, outcome="not_started",
            observation_ref="verified-preflight:original")
        assert not again["recorded"] and again["outcome"] == "not_started"
        with pytest.raises(AppError, match="execution_attempt_conflict"):
            await queue.resolve_execution_on(conn, owner="conversation.result", identity="original-result",
                data_generation=generation, job_id=job_id, native_attempt=0, outcome="not_started",
                observation_ref="different-proof")
        saved = await (await conn.execute(
            "SELECT outcome,evidence_ref,recorded_at,repair_grant_id,resolved_outcome,observation_ref "
            "FROM runtime.rbi_execution_facts WHERE data_generation=%s AND job_id=%s AND native_attempt=0",
            (generation, job_id))).fetchone()
        assert {key: saved[key] for key in original} == original
        assert saved["evidence_ref"] == evidence_ref and saved["resolved_outcome"] == "not_started"
        assert saved["observation_ref"] == "verified-preflight:original"
        # Native terminal observations may be reimported without downgrading the proof.
        await queue.seed_execution_history_on(conn, **args(generation), history=[{
            "job_id": job_id, "native_attempt": 0, "outcome": "failed",
            "evidence_ref": "native-job-event:" + str(start_id)}], complete=True)
        after = await queue.inspect_execution_on(conn, **args(generation), repair_ref="fix:source-eval", consumer="life_activity")
        assert after["unknown_held"] == 0 and after["failed_executions"] == 4


async def test_existing_observed_unknown_still_blocks_its_current_worker(database, repair_queue):
    queue, _task, state = repair_queue
    async with database.transaction() as conn:
        generation, job_id, _start_id, evidence = await imported_repair_unknown(conn, queue, state["event"])
    token = as_current(job_id, 0, generation)
    try:
        current = {"data_generation": generation, "job_id": job_id, "native_attempt": 0}
        async with database.transaction() as conn:
            page = await queue.advance_execution_history_on(conn, **args(generation), job_id=job_id,
                task_name="conversation.result", current_job=current)
            assert page["complete"] and not page["native_inflight"]
            await queue.seed_execution_history_on(conn, **args(generation), history=[], complete=True)
            snapshot = await queue.inspect_execution_on(conn, **args(generation), repair_ref="fix:source-eval",
                                                           consumer="life_activity", current_job=current)
            assert snapshot["unknown_held"] == 1 and snapshot["repair"]["reason"] == "execution_outcome_unknown"
            begin = await queue.begin_execution_on(conn, **args(generation), job_id=job_id, native_attempt=0,
                                                    repair_ref="fix:source-eval", consumer="life_activity")
            assert not begin["eligible"] and begin["reason"] == "execution_attempt_already_recorded"
            fact = await (await conn.execute("SELECT outcome,evidence_ref FROM runtime.rbi_execution_facts "
                                             "WHERE data_generation=%s AND job_id=%s", (generation, job_id))).fetchone()
            assert fact == {"outcome": "unknown", "evidence_ref": evidence}
    finally:
        runtime_jobs._execution_identity.reset(token)


@pytest.mark.parametrize("kind", ["normal", "true_begin", "missing_anchor"])
async def test_not_started_does_not_clear_unproven_or_nonrepair_unknown(database, repair_queue, kind):
    queue, _task, state = repair_queue
    async with database.transaction() as conn:
        if kind == "normal":
            generation = await queue.current_generation_on(conn)
            await queue.seed_execution_history_on(conn, **args(generation), history=[], complete=True)
            job_id = await marked_normal(conn, queue, generation, state["event"])
            await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
            started = await (await conn.execute("SELECT id FROM procrastinate_events WHERE job_id=%s AND type='started'",
                                                 (job_id,))).fetchone()
            start_id = started["id"]
            await queue.seed_execution_history_on(conn, **args(generation), history=[{
                "job_id": job_id, "native_attempt": 0, "outcome": "unknown",
                "evidence_ref": "native-job-event:" + str(start_id)}], complete=True)
        elif kind == "missing_anchor":
            generation, job_id, start_id, _evidence = await imported_repair_unknown(conn, queue, state["event"])
        else:
            generation, digest = await exhaust_normal(conn, queue, state["event"])
            assert (await grant_on(conn, queue, generation, digest))["eligible"]
            job_id, _payload = await enqueue_repair(conn, queue, generation, state["event"])
            await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
            start_id = (await (await conn.execute("SELECT id FROM procrastinate_events WHERE job_id=%s AND type='started'",
                                                  (job_id,))).fetchone())["id"]
            token = as_current(job_id, 0, generation)
            try:
                current = {"data_generation": generation, "job_id": job_id, "native_attempt": 0}
                await queue.advance_execution_history_on(conn, **args(generation), job_id=job_id,
                    task_name="conversation.result", current_job=current)
                await queue.seed_execution_history_on(conn, **args(generation), history=[], complete=True)
                begun = await queue.begin_execution_on(conn, **args(generation), job_id=job_id, native_attempt=0,
                    repair_ref="fix:source-eval", consumer="life_activity")
                assert begun["eligible"]
                await queue.finish_execution_on(conn, owner="conversation.result", identity="original-result",
                    data_generation=generation, job_id=job_id, native_attempt=0, outcome="unknown")
            finally:
                runtime_jobs._execution_identity.reset(token)
        await end_original_and_scan(conn, queue, generation, job_id)
        if kind == "missing_anchor":
            await conn.execute("DELETE FROM procrastinate_events WHERE job_id=%s AND id=%s", (job_id, start_id))
        with pytest.raises(AppError, match="execution_not_started_unproven|execution_history_incomplete"):
            await queue.resolve_execution_on(conn, owner="conversation.result", identity="original-result",
                data_generation=generation, job_id=job_id, native_attempt=0, outcome="not_started",
                observation_ref="candidate-not-started")
        saved = await (await conn.execute("SELECT outcome,resolved_outcome FROM runtime.rbi_execution_facts "
                                         "WHERE data_generation=%s AND job_id=%s AND native_attempt=0",
                                         (generation, job_id))).fetchone()
        assert saved == {"outcome": "unknown", "resolved_outcome": None}


async def test_incomplete_history_exposes_only_known_unresolved_lower_bound(database, repair_queue):
    queue, _task, state = repair_queue
    async with database.transaction() as conn:
        generation, job_id, _event, _evidence = await imported_repair_unknown(conn, queue, state["event"])
        partial = await queue.seed_execution_history_on(conn, **args(generation), history=[], complete=False)
        assert not partial["history_complete"] and partial["unknown_held"] is None
        assert partial["known_unknown_held"] == 1 and partial["known_active_executions"] == 0


async def test_current_marker_requires_accurate_identity_and_scanned_native_anchor(database, repair_queue):
    queue, _task, state = repair_queue
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        await queue.seed_execution_history_on(conn, **args(generation), history=[], complete=True)
        other_args = {**args(generation), "identity": "different-result"}
        await queue.seed_execution_history_on(conn, **other_args, history=[], complete=True)
        job_id = await marked_normal(conn, queue, generation, state["event"])
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
    token = as_current(job_id, 0, generation)
    try:
        current = {"data_generation": generation, "job_id": job_id, "native_attempt": 0}
        async with database.transaction() as conn:
            before = await queue.inspect_execution_on(conn, **args(generation), current_job=current)
            assert before["native_inflight_complete"] and before["native_inflight"][0]["anchor_state"] == "unscanned"
            assert not before["native_inflight"][0]["is_current"] and not before["eligible"]
            with pytest.raises(AppError, match="execution_admission_identity_conflict"):
                await queue.begin_execution_on(conn, **other_args, job_id=job_id, native_attempt=0)
            page = await queue.advance_execution_history_on(conn, **args(generation), job_id=job_id,
                task_name="conversation.result", current_job=current)
            assert page["complete"] and page["native_inflight"]
            await queue.seed_execution_history_on(conn, **args(generation), history=[], complete=True)
            eligible = await queue.inspect_execution_on(conn, **args(generation), current_job=current)
            assert eligible["eligible"] and eligible["native_inflight"][0]["is_current"]
            started_id = eligible["native_inflight"][0]["started_event_id"]
            await conn.execute("DELETE FROM procrastinate_events WHERE job_id=%s AND id=%s", (job_id, started_id))
            damaged = await queue.inspect_execution_on(conn, **args(generation), current_job=current)
            assert not damaged["native_inflight_complete"] and not damaged["eligible"]
            assert damaged["reason"] == "history_unknown" and not damaged["native_inflight"][0]["is_current"]
            assert damaged["unknown_held"] is None and damaged["active_executions"] is None
            assert damaged["known_unknown_held"] == 0 and damaged["known_active_executions"] == 0
    finally:
        runtime_jobs._execution_identity.reset(token)


async def test_current_exemption_never_hides_another_live_native_job(database, repair_queue):
    queue, _task, state = repair_queue
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        await queue.seed_execution_history_on(conn, **args(generation), history=[], complete=True)
        first = await marked_normal(conn, queue, generation, state["event"], key="first-live")
        second = await marked_normal(conn, queue, generation, state["event"], key="second-live")
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=ANY(%s::bigint[])", ([first, second],))
        for job_id in (first, second):
            page = await queue.advance_execution_history_on(conn, **args(generation), job_id=job_id,
                                                             task_name="conversation.result")
            assert page["complete"] and page["native_inflight"]
        await queue.seed_execution_history_on(conn, **args(generation), history=[], complete=True)
    token = as_current(first, 0, generation)
    try:
        current = {"data_generation": generation, "job_id": first, "native_attempt": 0}
        async with database.transaction() as conn:
            status = await queue.inspect_execution_on(conn, **args(generation), current_job=current)
            assert status["native_inflight_complete"] and status["reason"] == "execution_in_progress"
            assert {item["job_id"]: item["is_current"] for item in status["native_inflight"]} == {
                first: True, second: False}
            begin = await queue.begin_execution_on(conn, **args(generation), job_id=first, native_attempt=0)
            assert not begin["eligible"] and begin["reason"] == "execution_in_progress"
            assert not await (await conn.execute("SELECT 1 FROM runtime.rbi_execution_facts "
                "WHERE data_generation=%s AND job_id=%s", (generation, first))).fetchone()
    finally:
        runtime_jobs._execution_identity.reset(token)


async def test_redacted_trusted_native_tail_exits_without_importing_unknown(database, repair_queue):
    queue, task, _state = repair_queue
    old, redacted = envelopes()
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        await queue.seed_execution_history_on(conn, **args(generation), history=[], complete=True)
        job_id = await marked_normal(conn, queue, generation, old["event"],
                                     queueing_lock="redacted-normal", wake_object_key="original-result")
        result = await queue.redact_wake_on(conn, job_id, expected_task="conversation.result",
            expected_generation=generation, expected_payload=old, redacted_payload=redacted,
            invalidation_ref="source-change:normal", wake_object_key="original-result")
        assert result["state"] == "redacted"
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
        page = await queue.advance_execution_history_on(conn, **args(generation), job_id=job_id,
                                                         task_name="conversation.result")
        assert page["complete"] and page["executions"] == []
        assert page["native_inflight"][0]["source_invalidated"]
        await queue.seed_execution_history_on(conn, **args(generation), history=[], complete=True)
        snapshot = await queue.inspect_execution_on(conn, **args(generation))
        assert snapshot["native_inflight"][0]["source_invalidated"]
        assert snapshot["reason"] == "execution_in_progress" and snapshot["unknown_held"] == 0
    assert await task(context(job_id), **old) == {"state": "invalidated", "reason": "source_invalidated"}
    async with database.transaction() as conn:
        await conn.execute("SELECT procrastinate_finish_job_v1(%s,'succeeded',false)", (job_id,))
        history = await queue.observe_execution_history_on(conn, job_ids=[job_id], data_generation=generation,
                                                            task_name="conversation.result")
        assert history["complete"] and history["executions"] == []
        exit_row = await (await conn.execute("SELECT kind FROM runtime.rbj_wake_exits WHERE job_id=%s", (job_id,))).fetchone()
        assert exit_row == {"kind": "source_invalidated"}


async def test_known_unknown_cannot_be_relabelled_as_free_history_pagination(database, repair_queue):
    queue, _task, state = repair_queue
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        await queue.seed_execution_history_on(conn, **args(generation), history=[], complete=True)
        current_id = await marked_normal(conn, queue, generation, state["event"], key="current-normal")
        old_id = await queue.enqueue(conn, JobSpec("conversation.result", "old-unknown", {"event": state["event"]},
                                                   data_generation=generation))
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=ANY(%s::bigint[])", ([current_id, old_id],))
        await queue.seed_execution_history_on(conn, **args(generation), history=[{
            "job_id": old_id, "native_attempt": 0, "outcome": "unknown", "evidence_ref": "legacy-unknown"}], complete=False)
    token = as_current(current_id, 0, generation)
    try:
        async with database.transaction() as conn:
            common = dict(owner="conversation.result", identity="original-result", data_generation=generation,
                          job_id=current_id, native_attempt=0, observation_ref="proof:no-apply")
            with pytest.raises(AppError, match="execution_preflight_wait_unconfirmed"):
                await queue.record_execution_preflight_stop_on(conn, **common, reason="history_more")
            stopped = await queue.record_execution_preflight_stop_on(conn, **common, reason="unknown")
            assert stopped["state"] == "preflight_stopped"
            snapshot = await queue.inspect_execution_on(conn, **args(generation))
            assert snapshot["known_unknown_held"] == 1 and not snapshot["history_complete"]
    finally:
        runtime_jobs._execution_identity.reset(token)
