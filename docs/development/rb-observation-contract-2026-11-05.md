# RB 统计与恢复装配合同

本文件由主协调者发布，各域在既有事务/账本中接线，不新增独立事实权威。先完成实现，最后统一验证。

## 统计

主装配注入 `statistics`，域不导入 Operations 具体实现。调用 `await statistics.record_safely_on(conn, event)`；该方法用 savepoint，失败返回 False，不回滚业务。域需提供按原账本可重建的补投/游标路径，未补完不能标 complete。完成已覆盖范围后调用 `await statistics.mark_producer_on(conn, producer, watermark=..., complete=..., detail=...)`。

event: `family`, `event_id`, `revision`, `occurred_at`, `module`, `actor_id`, `route_id`, `dimensions`, `metrics`。

- family: messages/models/memory/growth/life/media/recovery。
- event_id 是原稳定业务或物理 attempt 身份，revision 为递增整数；时间带时区。同 family + event_id 的新修订替换旧贡献，不能晚到用量重复加总。
- module: channels/conversation/models/memory/growth/life/media/operations/independent/runtime。
- dimensions 仅受控 `state`；不可将任意对象 ID 当聚合维度。
- metrics 值为非负数或 None；未知用 None，估算用独立字段。
- 消息 metrics: received, processed, reply_required, delivered, withdrawn。
- 模型 metrics: physical_attempts, business_success, input_tokens, output_tokens, estimated_input_tokens, estimated_output_tokens, cached_input_tokens, cache_write_tokens, reasoning_tokens, actual_cost, estimated_cost, queue_ms, execution_ms, ttft_ms, pool_wait_ms, dns_ms, connect_ms, send_ms, response_wait_ms, read_ms, effective_output_ms, http_total_ms。
- 业务 metrics: memory_applied, memory_failed, growth_material_blocked, growth_review_blocked, life_scheduled, life_intentional_idle, life_deferred_no_slot, life_stale_state, life_invalid_proposal, uncovered_material_age_seconds, media_completed, media_failed, recovery_eligible, recovery_verified, recovery_failed。
- state: received/processed/reply_required/delivered/withdrawn/queued/running/succeeded/failed/cancelled/unknown/applied/blocked/scheduled/intentional_idle/deferred_no_slot/stale_state/invalid_proposal/eligible/verified。

物理请求、业务结果分开。HTTP 各阶段不能相加冒充总耗时。Life 逐候选状态分开。各域回执明确注入方法及补投方法，主协调者装配。

## 共同恢复记账

`tianshu.runtime.recovery_policy.RecoveryPolicy` 是事务内共享预算。领域仍拥有实际业务提交和结果。

- `register_on(conn, *, domain, subject_id, owner, material_id, material_digest, dependency, current_version, max_attempts)` 返回 lineage 行；所有身份和 version/digest 为非空字符串。
- `reserve_attempt_on(conn, lineage_id, *, owner, expected_version, attempt_id, request_digest, route, original_state, claim_token=None)` 返回 AttemptReservation(attempt_id,lineage_id,number,status,probe_token)。首尝试原状态 none，后续必须 failed；同 attempt 同摘要幂等，unknown/进行中不重放。
- `fail_attempt_on(conn, attempt_id, *, code, category, retry_after=None, probe_token=None)` 持久化故障分类、due、依赖冷却；`classify_failure(code, external_outcome_unknown=False)` 提供共用分类。
- `succeed_attempt_on(conn, attempt_id, *, probe_token=None)` 仅标物理尝试，不能替代业务提交验收。
- `claim_due_on(conn, *, owner, limit=64, lease_seconds=30)` 返回带 claim_token 的公平短租约集合；重试用相同 lineage，换 attempt/route/子批不重置预算。
- `update_version_on(conn, lineage_id, *, owner, expected_version, current_version, paused=False, cancelled=False)` 用于版本变动与暂停/取消。
- `resolve_unknown_on(conn, attempt_id, *, observation_ref, state, code=None, category=None, retry_after=None, probe_token=None)` 仅凭原权威确定回执解除 unknown。

共同 API 应接入本域真实物理尝试/重试链，不能仅导入 helper。原队列 retryable 的 3 次调用不等于物理预算。依赖故障公平恢复，有界 schema 修正和材料闭包拆批须继承原 lineage；已知撤回/取消不恢复。

## Operations 窄恢复口

`RecoveryAction(name,error_families,inspect,invoke,observe,max_attempts=3)`。

- inspect(incident_or_recovery,target) -> eligible,version,material_id,material_digest,dependency,original_state,paused?,cancelled?
- invoke(recovery,target,facts,attempt_id) -> state accepted/failed/unknown 等事实。
- observe(recovery,target,facts,attempt_id) -> state verified/failed/pending/unknown。
- invoke 的实际领域提交事务必须调用注入的 `await guard_on(conn, attempt_id, recovery['owner_token'])` 并做原版本/CAS、身份/来源验证。未知外部效果不得重新派发。

优先绑定明确单对象的索引重建、已失败计算恢复、原 Action 状态核对；不能用全量 recover 冒充精确动作。返回 verified 必须有真实业务结果证据。各域给出可装配窄口与可用错误类别，缺能力如实 unavailable。
