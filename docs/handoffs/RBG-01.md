# RBG-01：Life 旧规划退出与原模型准入封存

基线 `5cd309ae67c9896feb787ad03cc65272c4884173`，分支 `codex/rbg-life-20261105`。本文件随本卡提交；最终提交 SHA 由提交回执给出。仅本地实现，未合并或操作生产。

## 实现

- Life 按当前 watermark、原 generation/recovery 状态、原 request 元数据和业务截止时间识别不再有效的规划。先持久化原生 `life.plan` 恢复唤醒，再封存原 Models attempt 的未来准入，最后按原指针、revision 和代际 CAS 退役；中断后可从原身份继续。保留原请求、回执、来源与 `unknown`，不将缺失回执解释成历史未发送或无费用，也不改变 `last_planned_at` 冒充完成。相同材料不会因为退役而重新消费预算；有效的新语义材料仍走正常规划和恢复预算。
- Models 以原 attempt、Life recovery lineage、actor、principal、原场景 owner 和 `data_generation` 共同确认封存权威；新准入与封存共用事务级 attempt 锁。原无回执时不制造 Models attempt；已有 queued/not-started 可实际取消，running/unknown 等原状态不被改写。常规材料换代使用观察状态 CAS，期间原请求转为 running 等情况会继续观察而非误封存。
- 明确取消只取消后续恢复 lineage，不把原 unknown 物理尝试改写为 failed。unknown 续尝试仍需同一 attempt/代际、实际完成且带 UTC offset 的 Models 回执，并遵守来源与最终 CAS。
- `RBGPlanOperationsRecovery` 复用领域恢复动作与 `life.plan` 原生唤醒；历史退役对象只低频对账，不作为当前业务故障、不重发或应用旧模型结果。`PlanningEngine.defer_timeout(actor_id)` 仅为已确认本地且事务回滚的超时提供同一代际原生唤醒；本卡不对 `TimeoutError`/`CancelledError` 做笼统捕获，不推断 job 18182 历史超时发生层。现有已持久唤醒、启动恢复和 Operations 入口处理可观察的中断。

## 主协调者装配

1. 注册 `src/tianshu/models/rbg_admission_retirement.sql` 与 `src/tianshu/life/rbg_plan_retirement.sql` 到正式产品迁移和对应 DomainModule；Models SQL 还需进入 `execution_migrations(jobs)`，因为现有 Models ledger 准入路径也读取封存表。迁移必须先于新代码运行。
2. 实例化 `RBGPlanOperationsRecovery(worker, guard_on)`，将 `registration()` 接入现有 RecoveryAction 集合，候选合并 `await adapter.candidates(limit=32)`。`life.plan`、`life.plan.prepare` 是现有对象的条件恢复；`life.plan.retired` 是 `business_blocked=false`、`notify_on_change=true` 的历史对账，不能进入普通业务进展告警。
3. 在现有 Operations 周期调用 `await adapter.observe_due_retirements(limit=32)`。返回 `{"observed": int, "unavailable": int, "changes": [...]}`，变化项含 `actor_id`、`generation_key`、`attempt_id`、`previous_status`、`status`。周期入口只处理当前代际到期历史项，持久领取避免并发重复查询；查不到或不可用时按十分钟起、最长六小时退避，不制造模型请求。只有真实状态变化才在 `changes` 中返回。
4. 可在确有原 attempt ID 和代际的真实回执通知时调用 `await adapter.receipt_changed(attempt_id, data_generation)`，使对应历史观察提前到期；现有冻结 completion payload 不改写，当前没有已核实可接的自动提前通知时，依靠上述周期到期对账。
5. 如接入 `defer_timeout`，调用者必须证明捕获的是本地阶段超时且事务已回滚，保持外部准入/执行及任务取消的不确定性；不能给全部 `TimeoutError` 或 `CancelledError` 套重试。

## Models 公共接口

```python
await models.inspect_admission_retirement(
    attempt_id, lineage_id=..., data_generation=...,
    context=current_manager_context, original_context=frozen_plan_context)
await models.seal_admission(
    attempt_id, lineage_id=..., data_generation=...,
    context=current_manager_context, original_context=frozen_plan_context,
    reason="superseded" | "expired", expected_status=None | "absent" | model_status)
```

前者 `state` 为 `unsealed`/`sealed`；后者为 `sealed_absent`、`sealed_queued`、`sealed_existing`、`explicit_cancelled` 或 `observation_changed`。两者返回原 attempt、lineage、代际、当前原始状态、dispatch 及明确取消标识；检查接口另返回 `error_code`。`sealed_absent` 仅表示封存时本地缺原行，不证明历史外发结果。`observation_changed` 没有写封存，调用方继续观察原 ID。当前管理权限需要有效 Identity 与 `life.manage`，原场景按 Runtime lineage owner 校验，不读取失效原正文。身份/owner 不匹配报 `forbidden`、`model_admission_owner_unproven` 或 `attempt_identity_conflict`；代际过期报 `data_generation_expired`；对已封存且无原行的迟到创建报 `model_admission_retired`。HTTP 对应 `POST /internal/v1/models/attempts/{attempt_id}/admission-retirement`、`/seal-admission`。

## 验证与局限

新增定向用例 `tests/models/test_rbg_admission.py`、`tests/life/test_rbg_plan_retirement.py`，并更新历史模型假体；覆盖缺回执终态、迟到准入、并发、CAS 竞争、来源撤回、明确取消、unknown、历史低频对账与恢复唤醒。关联的原有回归包括 `tests/life/test_rbf_planning_recovery.py`、`tests/life/test_rb_plan_submission.py`、`tests/life/test_pure_unknown_recovery.py`、`tests/conversation/test_generation_recovery.py`。测试、构建和烟测依授权留给主协调者集中执行；定向用例需要隔离 PostgreSQL/pgvector。静态检查结果及最终 SHA 随提交回执报告。无真实模型调用、生产恢复或业务验收，job 18182 的具体超时层仍无直接证据。
