# RBJ-09：原生结果任务准入与历史对账

- 基线：`a6be2936501d8b12a5e906ce549463f691479d06`；代码和用例提交：`1e16b553b788fe7a73493f62e979408a6dbc3087`；分支：`codex/rbj-native-runtime-20261106`。
- 范围：Runtime 的 RBJ-09 / 2.1。Conversation 生产者及业务状态由 RBJ-10 实现；集成、集中验证和生产操作由主协调者负责。

## 实现和装配

1. `JobSpec.execution_identity` 仅供可信 Python 发布者使用，限 `conversation.result` 普通结果。只有同一事务中实际新建的物理任务登记 `runtime.rbj_execution_admissions`；复用、合并或收养旧任务不会补记。payload 无法自授权；已有 marker 的身份冲突会拒绝。
2. 非分页和分页历史观察把可信活跃原生尾部放在 `native_inflight`，不将其误导入业务 `unknown`。身份锁下的快照验证绑定、原始负载摘要、任务状态和原生事件锚点；只有 wrapper 真实当前任务且锚点已扫描可豁免自身，其他活跃尾部继续阻断。无效/缺失锚点、abort、超过上限均不作完整历史推断。来源撤回仍走已有撤回出口。
3. 历史不完整时，`unknown_held` / `active_executions` 为 `None`；`known_unknown_held` / `known_active_executions` 是锁下已知下界。普通任务的 `record_execution_preflight_stop_on` 在 marker、身份及当前物理序号一致且尚未 begin 时写精确退出记录；未登记返回 `unregistered`，已有未知不能冒充 `history_more`。
4. `resolve_execution_on(..., outcome="not_started")` 仅处理已持久化的、由原生 started 事件导入的绑定 repair `unknown`。Runtime 核对原始证据、绑定和冻结负载、完整有界事件和游标、终止状态及其他未决执行，保留原 outcome、证据、时间和原序号；后续粗粒度历史导入不覆盖此结论。普通任务、真实业务 begin 后的 unknown、锚点缺失及冲突均不得借此清除。
5. 新增 `src/tianshu/runtime/rbj_native_admission.sql`，须在 `rbi_execution_budget`、`rbj_wake`、`rbj_execution_repair` 之后注册执行。它新增普通准入表并扩展现有 outcome / exit 约束；不得修改已发布的旧迁移。主协调者已在其工作区单写注册，集成时核对实际迁移顺序。

## 恢复边界

Runtime 校验的是可机械确认的历史和身份，不能证明领域业务没有执行。对线上历史调用 `not_started` 前，主协调者及领域所有者仍须核实冻结版本的 begin/seed 串行化、唯一且对应原序号的持久 preflight 失败证据，以及原 Action / 来源 / Life 的状态和 CAS；不得用其他序号的退出、当前状态猜测或缺失日志代替证据。保留原事实、请求、回执和未知结果，不自行重发外部副作用。

## 已检查与待集中验证

- 对修改的 Python 源码及用例完成 AST 解析；暂存 diff 通过 `git diff --check`，并静态审阅了本卡 diff。
- 编写了 `tests/runtime/test_rbj_runtime_admission.py`，覆盖观察者与 begin 竞态、当前与其他活跃任务、旧任务不补登记、来源撤回、损坏锚点、已知 unknown、原 repair unknown 的精确 `not_started` 和再导入。既有 Runtime 夹具增加新迁移及当前原生锚点准备。
- **未运行**测试、PostgreSQL 迁移、构建、烟测或生产操作；本地静态检查不代表集成、业务收敛或真实消息交付通过。请主协调者集成 RBJ-10 后，集中执行受影响 Runtime / Conversation 定向用例并验证新迁移；若验证揭示集成差异，仅补受影响检查。
