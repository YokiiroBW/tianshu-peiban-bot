# RBI-01 Runtime 交接

- 工作树：`C:/Users/Administrator/.codex/worktrees/rbi-runtime/tianshu-peiban-bot`
- 分支：`codex/rbi-runtime-20261106`
- 基线：`18d640c41bb3354ef82ffea41ff2e78b1338e3b3`
- 代码及必要用例提交：`24bbcad9928d7662e6882c68df545cd08323cf01`；本文件在其后单独提交，以便记录精确代码 SHA。
- 依赖 RBI-06 Models 提交 `7715afa5144be098a8b9431d1cda03b1a3aae4ec` 的 `tianshu.models.rbi_outcome_guard.lock_recovery_facts_on`。本窗口未跨树集成该提交。

## 实现与边界

1. `RecoveryPolicy.resume_repaired_on(connection, lineage_id, *, owner, expected_version, original_attempt_id, model_receipt, repair_ref)` 为已确认 failed、同版本修复后建立一次幂等 due；不重置预算、不变更原尝试或失败分类、不提前穿透依赖 cooldown/探针。返回 `eligible/reason/lineage_id/retry_at/repair_ref`；资格不是新物理尝试。领域仍须在事务中核对业务 head、固定材料和来源/权限。
2. 同步的 `validate_model_recovery_facts(facts, *, receipt, expected_status, data_generation)` 校验锁定的 Models 原尝试、提交摘要、时间和取消事实。Runtime 请求摘要与 Models submission 摘要是不同合同，不直接互比。拿锁顺序为来源/身份和领域 head → installation generation share → Models admission/attempt facts → Runtime lineage → dependency；不得持锁发 RPC。已保留非 Models 调用方的原有 `reserve_attempt_on(..., original_state='failed', model_receipt=None)` 兼容路径，故模型领域必须主动传入 fresh 原 `model_receipt` 才启用该事实围栏。
3. 纯计算的原始 `cancelled/consumer_interrupted/consumer_interrupted` 回执以 `classify_failure('consumer_interrupted') == FailureCategory.INTERRUPTED` 记作 Runtime failed，正常计入计算次数并有界延迟；不记 not-sent、不生成共享 provider cooldown。后继使用 `original_state='failed'` 和未改写的原回执；explicit cancel 和未知效果不能借此重试。`note_not_sent_on` 在该组合下仅复核事实及返回原预算，不返还次数。
4. `DomainModule` 新增 `maintenance`、`maintenance_roles`。`process.py` 复用 30 秒节奏，每领域独立任务、单回调 20 秒上界和局部状态记录，关闭时取消；回调角色未指定时沿用 start role。业务补漏、分页和短事务由各领域实现，未改任何 `module.py`。
5. 普通 native retry 若被并发的同主体 todo 占用队列锁，复用现有释放锁机制让原 doing job 保留原 key/载荷再重试；只有既有 retry strategy 真的允许重试时才释放。原有效永久键及最早 todo 唤醒语义不变。
6. `rbi_execution_budget` 为结果消费建立与 Models 次数分离的机械事实账本：同一 generation + 业务 identity + 原生 job ID/attempt 序号累计明确失败；typed rollback 已确认的 `RecoveryWait`/可重试数据库等待不扣失败额度；提交不确定、未确认回滚、在途尝试保持 unknown/in-progress 并阻断续接。保留原事实，只有领域持有效 observation 后可 `resolve_execution_on`。四次上限由调用方传入 `max_failures=4`，不能通过换 job key 重置。

## JobQueue 调用合同

- `current_job_execution()` 返回当前 `job_id/native_attempt/data_generation`；`begin_execution_on`、`finish_execution_on` 必须使用该上下文，在对应事务中处理。`inspect_execution_on` 只读额度；`resolve_execution_on` 需要领域的 `observation_ref`，仍在 todo/doing 的相同物理序号不可 resolve。
- `seed_execution_history_on(..., history, complete)` 接受带证据的原生历史；`complete=False` 持久标记整份历史未知。它支持增量追加，已证实事实不被粗粒度 native 观察覆盖；需显式 resolve unknown。`observe_execution_history_on(..., job_ids, task_name, current_job=None)` 的 complete 仅覆盖给定 ID 集合。
- 大历史使用 `advance_execution_history_on(connection, *, owner, identity, data_generation, max_failures, job_id, task_name, current_job=None, limit=128)`。它以 `(job_id,event_id)` 有序分页，每次最多处理 `limit` 个事件，在同一事务写事实和内部游标，并把全身份历史标记为 unknown。返回 `complete/reason/executions/events_read/job_status`；`execution_history_more` 表示后续页，`execution_history_job_missing_or_changed`、`execution_history_incomplete` 是证据缺口。complete 只代表此 job 当前尾部。后续同一调用可从游标观察新增事件；doing/aborting 且不是调用者当前 job 的原序号记为 unknown。只有精确的 `current_job_execution()` 可排除自身当前序号。
- 领域负责完整枚举原始及恢复 job ID、持久自身跨 tick 扫描进度、继续刷新可能追加事件的 job、处理证据缺口；仅全体 job 确认追平且无缺口后才 `seed_execution_history_on(..., history=[], complete=True)`。分页本身已写入本页 `executions`，不可把单页 complete 当全域完整。未知历史不能默认为零；native retry 事件本身不能证明 typed wait。

## 协调者装配

按以下名称将两个本域 SQL 分别加入 `product_migrations()` 和 `execution_migrations(jobs)`，排在已有 recovery/runtime schema 之后；后者的 `rbi_execution_budget` 必须排在 `0003_procrastinate` 之后。产品 baseline 已包含 native 表。不要只在一个入口注册：

```python
Migration("rbi_recovery_repairs", files("tianshu.runtime").joinpath("rbi_recovery_repairs.sql").read_text(encoding="utf-8")),
Migration("rbi_execution_budget", files("tianshu.runtime").joinpath("rbi_execution_budget.sql").read_text(encoding="utf-8")),
```

第二个迁移还创建 `public.procrastinate_events(job_id, id)` 索引，使单 job 历史可有界 seek；生产发布时由协调者按真实数据量安排索引创建。按各域实际 service 装配短 maintenance wrapper：Memory/Growth/Life 为 `bot-background`，ResultConsumer 为 `bot-foreground`。所有模型后继调用方先按上述锁序取得 facts 并传 fresh receipt；ResultConsumer 负责证明其枚举和额度 identity，Runtime 不读取 Operations 私有表。

## 必要验证与未完成

- 新增/更新用例覆盖同版本幂等、未知/取消/代际/预算与并发取消、原始 consumer interruption、跨领域 429 冷却和单探针、doing/todo/native retry、角色回调隔离、跨新 job 的四次失败、typed wait 与 unknown、原生序号/分页及事务回滚。只编写了用例，**尚未运行**。
- 已做 10 个变更 Python 文件的 AST 解析和提交 diff 的 `git diff --check`，均通过；未执行测试、构建、烟测、真实模型/QQ 或生产操作。最终由协调者集成后运行受影响验证，区分静态结果、集成测试与真实业务证据。
- 集中验收需将白名单外 `tests/runtime/test_rbf_probe_leases.py` 中使用合成 unknown 回执的旧夹具更新为真实隔离 Models attempts/submissions 事实；新 guard 不再接受缺失原账本/提交摘要的伪回执。必要时检查其他域同类假回执夹具。此窗口未改白名单外文件。
