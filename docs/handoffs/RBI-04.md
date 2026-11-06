# RBI-04 Life 交接

## 基线与范围

- 工作树：`C:/Users/Administrator/.codex/worktrees/rbi-life/tianshu-peiban-bot`；分支：`codex/rbi-life-20261106`；开发基线：`18d640c41bb3354ef82ffea41ff2e78b1338e3b3`。本包提交 SHA 以提交回执为准。
- 实现范围为 Life 原 Action 的观察与补漏、结果回调与联系人投影、规划决策追踪、原模型恢复及完成后派生唤醒。按协调者追加授权覆盖 events、contacts、generation_control、rbh_transport_recovery、diary_retry、diary_automation、writing 的本次必要接线。未改共享注册、现有 schema、配置或其他域。

## 原 Action 观察

- `rbi_activity_reconciliation.ensure_reconcile_on(life, connection, *, activity_id, action_id, data_generation=None, due_at=None, reason="observe", observed=False, immediate=False, retire_due=False, not_before=None)` 是本域统一安排入口。调用方在本业务事务中保持 Sources/Identity 和活动锁；函数核对当前安装代际和活动绑定，以 `activity_id/action_id/data_generation` 作为稳定载荷，使用每次新物理 key 和绑定活动、Action、代际的 native queueing lock 合并待执行唤醒。运行中任务允许保留一个后继；对合并成功的 todo 保留最早合法 due。
- `checkpoint_data.reconciliation` 单独保存 Action、代际、观察 due、观察次数、原因和对应 native job id，不替换 `frozen_action`、原 Action deadline 或活动执行截止。资源等待使用 `not_before`；显式恢复的原有 retry due 保存在 `capability_recovery`。旧 `context/sequence` 任务参数可读，执行时重新取得活动自身的上下文和 Action 绑定。
- 404、unknown 和资源等待继续安排原 Action 的观察。`capability_queued` 且无回执也不能证明原 invoke 从未开始：原 invoke 可能尚在准入或其事务未提交，404 不触发再次 invoke，而是保持 unknown。真正提交前已崩溃且无更强准入证据的情况同样无法自动区分，必须继续对账，不能按成功或明确未发送处理。仅既有明确失败且满足原 deadline、原 receipt/attempt、来源、代际和双重 CAS 的正常 capability recovery 仍可调用 `actions.resume`。
- `await LifeWorker.reconcile_due(limit=32)` 返回 `scanned/woken/complete`。它在未决活动观察和完成后派生唤醒两类本域记录间轮换配额，以进程内稳定游标分页，每次总扫描数不超过 `limit`（最大 250）；只安排缺失/失败的 native job 或运行中任务的后继，不调用 Action、模型或外部效果。`complete=true` 只表示两类当前轮次的候选索引都到末尾，不证明全域业务完成。重启后游标从头继续，来源、授权、暂停、替换、代际及明确停止仍单独核验。

## 结果回调和完成后唤醒

- 活动、承诺和 detached 回调要求 RBI-09 的 `on_complete.source_refs` 和 `await on_complete.fence(connection)`；它们将回调来源并入本次来源预锁，事件自身 support refs 保持原业务语义。活动/承诺在 fence 后锁定业务行，并将其与预锁快照比较，变化则整笔回滚重读。业务行、原 receipt/revision 证据与消费者完成证据使用同一事务。Action head 已前进时由 RBI-09 在事务退出后观察并安排真实 head，不能吞掉同 revision 不一致等其他错误。
- 成功结果预读原 Goal、原 planning context 和 Goal 来源；通过 RBI-10 的 `prepare_event_writes(..., scoped_refs=((goal_context, goal_refs),))` 与主事件来源一次预锁，再校验 Action head、活动和 Goal 完整快照；Goal 仍仅在自身 context 对新回执来源可见时在该事务更新。Goal 不拆异步。直接 `capability_result` 和恢复到期路径使用 `DirectResultFence` 调用同一 Capabilities 公共 head 端口；Life 不读取 Capabilities 私表。联系人 `record_delivery(..., *, connection=None)` 支持复用表达式事务，默认原独立事务不变。
- 非续作的 `activity_completed` 派生唤醒在主结果事务写入 `life.rbi_activity_wake_intents` 和 native job，冻结当时目标表、ID、revision、原事件来源/上下文和 due；后创建或已改变的等待不被旧事件唤醒。每次只处理一个目标，按事件和目标各自 context 合批预锁来源，再做目标 CAS；业务更新和游标推进同事务。native job 失败或提交结局不明时，通过持久游标和有界维护继续，已完成目标不会重做，明确不可读目标记录 skipped，事件自身授权/来源不可用记录 blocked。`life.events.wake_target` 复用既有事件唤醒规则，保留原事件和目标已有两者更早的 due。

## 规划与现场证据

- `rbi_planning_trace.py` 将新请求的触发类别、语义/恢复指纹、Models 冻结请求摘要和当时合法 `decision_review_at` 记录到新列；旧行 NULL 不回填。合法的定时复评、跨日和语义变化仍沿用原业务流程。请求摘要不同只证明冻结请求身份不同，不能单独证明语义材料不同。
- 只读补证的两次历史规划均已应用，语义指纹相同、冻结请求摘要不同，但历史复评边界缺失；没有证据证明当时重评无效，因此没有据此取消该业务策略。
- 已核实原预算 3/3 耗尽、`repair_due_at=NULL` 后，普通通知会让旧路径虚构新的 retry-wait。现在通过 Runtime 公共 `computation_budget_on` 核对同一 lineage/version：耗尽维持终态，存在合法 retry_at 才安排；真实语义变化仍可申请新工作但继续服从依赖冷却，不重写旧 receipt。
- 规划、日记、章节和 RBH 现场补证在事务外观察原冻结请求与新鲜 Models 回执，保留原 Models `submission_digest`，在领域读集/行锁后先锁 Models 原 attempt 再写 Runtime lineage/claim/reserve；多 attempt 补证按稳定顺序取事实锁。精确 `cancelled + consumer_interrupted + consumer_interrupted` 保持原 raw receipt，按 Runtime 的纯计算 `INTERRUPTED` 计入计算预算并按原 due 恢复；`explicit_cancel` 继续阻断，既不伪称未发送也不返还次数。缺失或变化的事实继续观察。
- 日记材料更换事务中的当前 diary 来源与原 generation recipe/request 来源按各自真实 context 经 `scoped_refs` 一次预锁，必要 refresh 先于来源锁；随后 diary 整行快照 CAS、原请求/recipe 复核和 Models facts，再做 Runtime 原账本操作。原 context 对材料不可见不会由当前 context 代为授权。
- job 78316 的现有证据：`life.activity_reconcile` 失败于 `job_identity_conflict`，旧 args 有 `context/sequence/activity_id`，同一 Action 的旧序列 0–5 已成功。被拒的传入载荷及精确冲突字段/摘要未被保留，不能断言究竟哪个字段变化。两个 Life 消费分支均曾因 `QueryCanceled` 留在 pending、attempts=2、lease 为空；当时具体 SQL、每次错误链及提交结局缺失。

## 根集成要求

1. 先注册 `src/tianshu/life/rbi_activity_reconciliation.sql`，其中包含活动扫描索引、`life.plan_generations.decision_trace jsonb NULL`、派生唤醒 intent 表与 pending 索引；新版代码启动和测试都依赖该 DDL。共享 Life 测试夹具（包括 `tests/life/test_planner_execution.py` 的 setup）需安装。不要回填旧决策或修改旧事实。
2. 在根单写的 `life/module.py` 中装配 bot-background 维护 `await worker.reconcile_due(limit=32)` 并保留启动恢复；注册 `queue.register('life.activity_completed_wake', worker.activity_completed_wake, execution_lane=ExecutionLane.LIFE, resource_recovery=True)`。
3. 合入 RBI-10 `scoped_refs`（支持 `events=()` 且不可见 scoped 组不升级为主事件失败）、RBI-09 的 `complete.source_refs`/`complete.fence(connection)` 与 Capabilities `assert_result_head_on`，以及 Models `lock_recovery_facts_on` 和 Runtime `validate_model_recovery_facts`、`INTERRUPTED`。共享 Actions、Identity、Sources 假端口和规划/日记 Broker 夹具需跟随合同更新；Models 内存回执不能冒称实际 attempt/submission 表事实。
4. 测试源覆盖旧键/上下文刷新、404/资源等待、合并/运行中后继/native retry、未来 due、代际与取消、分页、Goal 来源并发 CAS、独立 context 源闭包、派生唤醒回滚与补漏、联系人事务、QueryCanceled 回滚、提交后 ACK 丢失模拟、head 前进、纯通知/合法复评/跨日、429 耗尽、真实新约束及纯计算中断。集中运行本次 `tests/life/test_rbi_*.py` 和受影响的活动、规划、日记、写作、RBH 与结果消费既有用例，并由根执行真实 Sources/Identity/Capabilities/Conversation/Life/Models/Runtime 联合并发验证。ACK 用例是已知提交后丢失通知的模拟，不证明真实数据库提交不确定结果。

## 验证状态

静态解析了本包 27 个 Python 文件，`git diff --cached --check` 通过。本窗口不运行测试、构建、烟测、生产或真实模型/QQ。模块装配、集成测试、实际恢复和生产验收均未完成；不得把源码或测试文件称为已通过的联合验证。
