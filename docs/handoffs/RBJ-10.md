# RBJ-10：结果任务的原生准入与有界历史续读

本工作树 `C:/Users/Administrator/.codex/worktrees/rbj-native-conversation/tianshu-peiban-bot`，分支 `codex/rbj-native-conversation-20261106`，起点 `a6be2936501d8b12a5e906ce549463f691479d06`；执行依据为冻结合同 2.1 的第 18–19 节及根后续对普通 preflight 出口的补充，2.1 文档提交 `70c7faee`。产品代码、用例和本交接在同一提交，准确 SHA 由回执给出。

## 变更

- `execution_budget_on` 向 Runtime inspect 及其后 resolve 传当前真实原生执行上下文。repair 根据持久 proof 关闭资格时，inspect、resolve 和 close 都传相同上下文；外部观察者不获得当前任务豁免。
- 只在可信发布者的新普通 `conversation.result` JobSpec 上传现有 `execution_identity`；repair 仍使用既有 grant 绑定。Runtime 必须只在真正创建新物理任务时持久化 admission；已有 key、coalesce 和 adopt 不追补标记。
- 已扫描历史确有后续页、无缺口、native inflight 完整，且同身份锁下已知 unknown 和 active 均为零时，允许先提交该 ordinal 的准确 preflight stop，再以数据库时间生成的内部短 due 延期原物理任务继续读页。真实消费者租约等待使用其原到期时间，无效租约保持 unknown。缺锚、已知真实 unknown、真实 begin 或提交未知不会作为可续页面放行。
- 对新标记的普通任务，经过实际预算判断而不 apply 的当前 ordinal 使用 Runtime 的 `closed`、`blocked`、`unknown` 精确出口；真实分页和可证明的 Runtime 等待才附 due。旧未标记任务按 Runtime 的 `unregistered` 返回维持原路径，已有本 ordinal 执行事实不以出口替代；异常和 SQL 失败不改写成免费退出。
- 保持原事件、Action/receipt、代际、物理 job/attempt、失败预算和 repair grant。这里没有生产数据对账脚本、直接业务数据修复或迁移注册。

## Runtime 装配合同

依赖 RBJ-09 的 `JobSpec.execution_identity`、事务内新物理任务 admission、`inspect_execution_on`/`resolve_execution_on`/`close_execution_repair_on` 的 `current_job` 校验、typed `native_inflight` 及 `native_inflight_complete`。历史不完整时还依赖同一身份锁下的 `known_unknown_held` 和 `known_active_executions`；二者为 0 仅表示目前没有已知未决事实，不表示历史已完整。`record_execution_preflight_stop_on` 必须按 2.1 返回 `preflight_stopped` 或 `unregistered`，同 ordinal 的已有 begin/fact 和不匹配身份仍拒绝；其 reason 包括 `history_more`、`execution_wait`、`execution_in_progress`、`closed`、`blocked`、`unknown`。

根负责将 RBJ-09 新增的 `rbj_native_admission.sql` 注册在既有 `rbi_execution_budget` 和 `rbj_execution_repair` 之后，并串行集成两个工作树；本卡没有修改共享迁移主线。数据库时间的内部扫描 due 不代表 provider Retry-After、业务 hold 或消费者 lease。联调时应确认 Runtime 对页外活跃尾、精确 current、unscanned anchor 和 known counts 的实际返回与上述一致。

`resolve_execution_on(outcome="not_started")` 只是 Runtime 的机械证据窄口：它不验证原窗口日志或 Sources/Action/Life 业务事实。原历史对象的受限对账由根先单独核验冻结代码行为、同 ordinal 原错误日志、来源与 Action 头、领域 owner/revision/CAS、租约及完整 native 历史后执行；不得仅凭后续 ordinal 退出、native succeeded 或当前 Life 行推断旧 ordinal 未执行。原 unknown、evidence_ref 和 recorded_at 保留，解析追加观察事实；普通或外部 unknown 不适用。

## 验证与限制

新增 `tests/conversation/test_rbj_native_admission.py`，编写了 repair 和新普通任务被外部观察者抢先读取 `doing`、真实 Sources/Life/Goal/Memory 联合应用、已有准确 proof 的同任务关闭、真实分页沿原 native 重试、旧任务 adopt/coalesce 不追补标记、真实 unknown（包括历史尚未扫完）、缺失历史锚点、消费者有效/无效租约以及原 imported unknown 的精确 `not_started` 对账后继续同一 grant 等场景。Runtime 自身负责不同 current、伪 payload、不同证明冲突、真 begin unknown 与不受信普通历史等机械负例。

本窗口仅静态审阅 diff、检查空白及解析 Python AST；**未运行测试、构建、烟测、PostgreSQL、真实模型、QQ 或生产操作**。上述用例及跨工作树接口仍需根在集成后的隔离 PostgreSQL 集中运行，并按实际失败修正；没有以编写测试、静态检查、服务健康或模型回执宣称业务结果已收敛。
