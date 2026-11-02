# NR-05B：Memory 分区待唤醒合并

- 基线：`c11e99af07c9a98e4692651fde6ecf63e5b8a666`。
- 分支：`codex/nr05b-memory-wakeup`。
- 实现与测试：`caefa018a9736a10683fbdbe6cbc7137730306c9`。
- 唯一工作树：`C:\Users\Administrator\.codex\worktrees\tianshu-memory-wakeup-20261102\tianshu-peiban-bot`；未使用已归档旧工作树。
- 状态：代码与用例已提交，待根集中验证和串行发布。代理未运行任何测试、typecheck、build、真实服务、模型、QQ 或生产命令。
- 无 DDL、数据库迁移、新依赖或作者配置变更。未修改 retrieval、embedding、channels、模型步骤及事实算法。

## 问题与最终行为

`MemoryWorkers.recover()` 每 30 秒按当前时间生成不同永久 key，在待执行任务尚未取得分区 lease 时重复排队。约 80 条 todo 是重复唤醒，不等于 80 次模型请求。

现在只有 Memory 分区唤醒显式使用 JobQueue.enqueue 的可选 `queueing_lock`，其他任务默认行为不变：

- `_schedule_on` 持有原 partition 行锁，把同分区唤醒合并到最早原 todo 的 job_id/key；有更早 due 就前移原 scheduled_at，并使用既有原生通知协议唤醒消费者。后来的请求不推迟已有承诺。
- Procrastinate 既有 queueing_lock 唯一索引只限制 todo，允许 doing 保留一个待执行后继。没有添加执行锁、第二套队列或新的资源准入系统。
- 仅无 todo 时生成新的唯一事件 key，避免已完成或已取消的永久 job_keys 挡住后续同 due 唤醒。合并时不会插入一条无意义的新永久 key。
- 新输入总会确保存在一个合并唤醒。执行提前于最新 quiet due 时沿用原重检与重新安排逻辑，不改变 quiet/max_wait 合批规则。
- owner 结束时只按原 owner CAS 释放自己的 lease，并为无 active batch 的剩余 dirty 输入补回一个合并唤醒。旧 idle 清理追加已有 dirty_generation 条件，不能清掉读取后新来的输入。旧 owner 不为 successor 修改状态或安排唤醒。
- recover 每个候选分区独立短事务，持原行锁再次核对到期/active/quarantine 与 lease；忙锁跳过，后续正常恢复再处理，不在一轮事务内保留多把分区锁。

JobQueue 的合并需要调用方持业务资源锁，当前唯一产品调用方是上述 Memory 分区。它锁定同一 queueing_lock 或相同 task/payload 的未标记 todo；已 doing、终态、不同 partition/payload 不在取消集合。普通碰撞原位返回原 job_id，不触发 AlreadyEnqueued，也不使父输入事务失效。运行中的工作不因有后继唤醒而取消。

Sources/Identity 检查、模型请求冻结、ModelSteps、CAS 应用和未知隔离保持原实现。quarantined 唤醒仍只能观察原 ledger，不生成新请求；新的后续材料可按原流程整理。active 原请求的恢复仍受既有明确状态与原 ID 控制。

## 旧重复 todo 的精确收口

根部署后执行一次既有 `MemoryWorkers.recover()`，或等待其正常周期；本次已知 5 个 eligible 分区在原 64 项候选上限内，无需新增恢复入口或直接更新业务状态。

对于每个已取得分区锁的 eligible partition，本轮保留最小原 job_id 的 todo，把所有候选与新请求的最早 due 合入该项；其余已确认相同 task/payload 的 todo 通过原生 `cancel_job_by_id_async(abort=False, delete_job=False, connection=当前事务)` 变成 cancelled。doing/终态、attempts、原 payload、所有 job_keys 原行和已有事件均保留；取消追加原生 cancelled 事件，不删除、不伪造 succeeded、不取消 doing。取消的权威状态在原生队列和事件中，job_keys 不被重写。

根可用以下只读查询核对待执行重复项；它不是模型执行次数或模型成功率查询：

```sql
SELECT args->>'partition_id' AS partition_id,
       count(*) FILTER (WHERE status='todo') AS todo,
       count(*) FILTER (WHERE status='doing') AS doing,
       array_agg(id ORDER BY id) FILTER (WHERE status='todo') AS todo_ids
FROM procrastinate_jobs
WHERE task_name='memory.consolidate'
  AND jsonb_typeof(args->'partition_id')='string'
GROUP BY args->>'partition_id'
HAVING count(*) FILTER (WHERE status='todo')>1;
```

若某分区本轮仍持有效 lease 或忙行锁，会保持原 doing 并跳过，随执行结束/新输入/下一次正常 recover 收敛。只有未来 quiet due、没有 active/quarantine 的分区不因清理提前执行模型，原唤醒到期时自行重检并合并。没有全表清理或 unknown 自动恢复操作。

## 写好的精确验证目标

代理只完成相关调用链、完整 diff 静态阅读与 `git diff --check` / `git diff --cached --check`，无空白错误。以下全部未在本代理执行；需要根提供的可销毁 PG+pgvector，沿用现有 fixture，不新增矩阵。

Runtime：根可运行整个 `tests/runtime/test_transactions.py`，其中新增三项：

```text
tests/runtime/test_transactions.py::test_coalesced_signal_keeps_original_job_moves_earlier_and_retains_cancelled_audit
tests/runtime/test_transactions.py::test_coalesced_signal_keeps_doing_work_and_cancelled_keys_do_not_block_a_fresh_successor
tests/runtime/test_transactions.py::test_coalesced_signal_cancellation_and_earlier_schedule_rollback_with_business_changes
```

Memory：新增四项及相关既有精确目标：

```text
tests/core/test_consolidation_pipeline.py::test_memory_wake_coalesces_earlier_input_concurrent_recovery_and_repeated_ticks
tests/core/test_consolidation_pipeline.py::test_memory_wake_recover_retires_only_original_partition_todo_and_preserves_doing_audit
tests/core/test_consolidation_pipeline.py::test_memory_wake_leaves_one_successor_and_rearms_if_it_ran_before_owner_finished
tests/core/test_consolidation_pipeline.py::test_memory_wake_idle_cas_preserves_new_input_and_same_due_after_cancelled_key
tests/core/test_consolidation_pipeline.py::test_unknown_is_held_without_replay_while_later_material_advances
tests/core/test_consolidation_pipeline.py::test_prepared_repair_survives_interruption_without_another_repair_identity
tests/core/test_consolidation_pipeline.py::test_input_budget_change_resumes_the_same_unadvanced_material_without_a_new_episode
tests/core/test_consolidation_pipeline.py::test_expired_owner_does_not_settle_a_batch_already_taken_over
```

新增用例覆盖更早 due、重复恢复、dirty/recover 并发父事务成功、原 todo/key/events 保留、doing 不取消、原生 fetch 后的后继唤醒、lease 尚未释放时提前消费后继的收尾补唤醒、idle/new input generation 竞争、已取消永久 key 不阻挡相同 due 的新唤醒，以及队列更改随业务事务整体回滚。既有 unknown 用例增加反复 recover 后仅一个 todo、原冻结步骤整行不变、无新模型调用断言。

## 原生能力依据

实现核对项目固定 `procrastinate==3.10.0` 的官方源代码：[todo queueing_lock 既有唯一索引与状态机](https://raw.githubusercontent.com/procrastinate-org/procrastinate/3.10.0/procrastinate/sql/schema.sql)、[支持调用方事务的原生取消 API](https://raw.githubusercontent.com/procrastinate-org/procrastinate/3.10.0/procrastinate/manager.py)。没有修改第三方 schema/functions，也没有引入兼容服务。

没有已运行测试或生产数据收敛证据，不能把本地实现称为已验证或已上线；根完成集中验证后单写生产。
