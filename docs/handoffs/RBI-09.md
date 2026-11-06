# RBI-09 结果消费恢复交接

本包基线 `18d640c41bb3354ef82ffea41ff2e78b1338e3b3`，分支 `codex/rbi-result-consumption-20261106`。交接提交以该文件所在提交为准。该包尚未集成、运行测试或发布。

## 实现

- 复用 `result_deliveries` / `result_consumer_progress`；新 `rbi_result_wake.py` 将首次发布、旧头切换、Operations 恢复和领域补漏收敛到同一原始 receipt、action、revision、generation。只安排原始消费，不重新执行 Action、模型或渠道发送；有效的原生待办和消费者租约继续使用。
- `ResultRecovery.reconcile_due()` 从当前 generation 的 pending 领域账本按 due 有界扫描、`SKIP LOCKED`，单批最多 128 项和 5 秒；由根接入 bot-foreground maintenance。Ops 候选以 delivery 的索引为主；旧 native 任务没有 delivery 的情况使用 `rbi_result_legacy_scan` 的固定上界、每批 16 项和持久游标补登记，不把恢复任务伪认成原任务。`observe` 区分已完成、活跃任务、确定的等待、缺失执行、失败预算用尽和历史未知，不把没有后继的停滞永久称作 pending。
- 逐物理 job/native attempt 接 Runtime 统一执行账本，额度跨新 job 固定 4 次失败；确认的等待保留 due，未知和缺失历史不能算作全新零次。领域账本在原事务持久保留已知 `rbi_job_ids`、固定 job 上界及扫描位置，Runtime 持久 job 内 event 游标；每次最多推进 16 个 job 页、每页最多 128 个 events，所有已知 job/key 和分页证据完整后才标记 complete。`recover_result(..., known_job_ids=())` 接收经权威来源关联的旧 job ID，缺失会持久保留为 unknown。对账只依据原 native terminal failed 及同事务领域进度，撤销可原子回滚的 Life/Memory 旧 token；非原子 renderer/dialogue 中断保持 blocked。没有将 failed job 等同业务未提交。
- 各消费者独立领取并写入真实 proof；renderer 的未决状态阻断依赖它的 dialogue/detached/memory，但不阻断独立 Life 分支。Life/Memory 的业务效果与 proof 同事务，收到不确定数据库结果先读 proof；过期 owner 不能提交迟到效果。已确认的 `RecoveryWait` 保留原下界。
- 新增公开 `Actions.assert_result_head_on(connection, context, *, action_id, expected_receipt, result_revision, data_generation, source_refs=())`：校验原 receipt 全量序列化、权限/来源/代际和最新 revision，`FOR SHARE` 持锁至调用方事务结束。调用方先按完整读集预锁来源与身份，再调用端口。`complete.begin` 只校验 generation；`complete.fence` 和 `complete.source_refs` 供 Life 原子回调使用，proof 必须与 fence 属同一物理事务。Memory fallback 预锁同一 refs 后 fence，再调用原 `observe_episode(connection=...)`，不依赖额外 Memory hook。
- renderer、tool receipt / turn 消费和 channel expression 的结果回调及相关直接路径均先准备完整来源、事务内 fence，再锁业务行；多个 Action 按 ID 排序加锁，快照变化以既有 `MaterialSnapshotChanged` 回滚重取。渲染结果进入 `ready` 后，原 expression release 任务与 renderer 真实 proof 同事务入队；release 也复核已固定的各个 render Action 头。Action 关联存在但头不可得时停在 unknown，只有无 Action 的 Channel 结果或本地生成 unknown 保留原 Channel/Expression 语义。表达式渠道回执的联系人投影需要 Life 的 `record_delivery(..., connection=...)` 端口，见下方装配。

## 主协调者装配

1. 按 RBI-01 注册 `tianshu.runtime/rbi_execution_budget.sql`，确保 `current_job_execution()` 及 `seed_execution_history_on`、`inspect_execution_on`、`begin_execution_on`、`finish_execution_on`、`resolve_execution_on`、`advance_execution_history_on` 可用。注册 `tianshu.conversation/rbi_result_reconcile.sql`，顺序在 `rbg_result_consumption`、Runtime 和 Procrastinate 基础表之后；该迁移增加领域调度元数据、持久 job ID/补扫游标和索引。
2. `conversation/module.py` 的 `DomainModule` 接入 `result_consumer.reconcile_due()` 到 bot-foreground maintenance；不要只依赖 Operations。`runtime/product_operations.py` 创建 `ResultRecovery` 时传 `actions=actions`，并保留现有 `RecoveryExecutor.guard_on`。`runtime/product.py` 构造 `ExpressionService(..., sources=sources, actions=caps.actions)`。
3. RBI-04 Life 回调按完整多上下文 Sources/Identity 预锁 → `complete.fence(connection)` → Life 领域行锁 → 业务效果和 `complete(...)` 同事务。来源批锁使用 RBI-10，不能借当前 context 冒充其他 context 权限。由 Life 或根在 `contacts.record_delivery(context, plan, result, *, connection=None)` 支持现有事务，默认调用行为不变；09 的 fenced channel 结果已传 `connection=connection`。
4. 对既有 RBG 测试夹具注册本包和 Runtime SQL；注入真实/明确 fake Action fence、`ResultRecovery(actions=...)` 及支持 `result_fence` 的 renderer/conversation mock。集中验证真实 Life、Memory、Sources、Capabilities、表达和 Procrastinate 组合；避免以 fake 回调冒称联合验收。

## 验证与已知限制

仅做 AST 静态语法检查和 `git diff --check`，未执行测试、构建、烟测、真实模型、QQ 或生产读写。`tests/conversation/test_rbi_result_consumption.py` 写有 QueryCanceled 独立分支、已提交 proof/丢失确认、原/恢复任务均失败、原生等待、共享 4 次预算、缺失 job 历史、长历史跨批推进、legacy 补登记、过期 token、真实 Action 行锁及旧头阻止三个业务投影的定向用例；具体是否通过留待根集中运行。

根提供的脱敏证据表明两个目标的最新 `life_activity` 标记分别为 `QueryCanceled`，其原/恢复任务均持久为 failed；它不证明每次异常来自同一 SQL，也不证明未知提交的业务结果。原任务和仍存在的恢复任务可由原生 event 身份发现；如果历史 recovery job 已被删除、而唯一关联只保存在 Operations 且未传给本域，09 无法凭空证明该历史完整。根需通过公开领域登记或已知 job 引用补齐，缺证维持 `history_unknown`，不得把当前存续行当成全部历史。

09 静态核对 RBI-01 候选时发现原生终态会把 `attempts` 加一，而原候选 observer 未正确计数；Runtime 已修正，完整依赖链是 `24bbcad9928d7662e6882c68df545cd08323cf01` → `09c8350e263de115e41bb4ddbed5ba70521c020f` → `d9e4fd08edf8054d1d1e4d14b3683a5a9973b55c`，需统一集成验证。Runtime 已告知它会校验 checkpoint 锚点，锚点之前任意中间事件被删除的完整性仍需遵守其证据保留合同；native 事件遍历有固定页上限，本域逐次核对已知 job 的存在、代际和原信封，该引用核对与已登记 ID 数组读写的成本随该 receipt 的 job 数增长，不能称整个事务为绝对常数上界。真实跨域组合、目标历史是否完整、原始业务结果和线上收敛均需根的集中验收，不能据本交接宣称已完成。
