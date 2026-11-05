# RBF-05 来源、身份与领域提交锁顺序交接

## 状态与边界

- 工作树 `C:/Users/Administrator/.codex/worktrees/rbf-sources/tianshu-peiban-bot`，分支 `codex/rbf-sources-20261105`。
- 基线 `33e5282f60fe4c803c3b16d498d1d924621ed1b9`；本卡代码和用例依次为 `539754b0a89015ddf1236283eb86d08ca1eb91d8`、`754fc8d3203cc640c020076424c263a114141dc2`。本交接位于后续文档提交。
- 这是隔离工作树中的实现和静态审阅，不代表集成、测试通过、部署或线上业务恢复。未访问生产，未执行测试、构建或烟测。
- 先集成 RBF Runtime 的 `tianshu.runtime.recovery_policy.RecoveryWait`，再集成本卡；还要集成领域窗口对同一事务入口和 Jobs 可重观察信号的接线。没有 SQL 迁移或配置变更。

## 已实现

1. `sources.archives.lock_selected` 保留来源支持图、别名闭包和原有权威验证，先按别名 key、写入根 ID 取得事务级锁，再按 source ID 顺序取得行锁。实际修改的根及其直接别名目标排他，纯 support/其他依赖共享；尚不存在的根也按其 ID 协调，不以角色或全系统为单位串行。
2. 锁集在等待期间扩大时不追加乱序锁，而是抛 `RecoveryWait('source_lock_set_changed', retry_at, dependency='sources:lock_set')`；`retry_at` 为当前 UTC 时间加 5 秒。`validate_persistence` 将此类型原样抛出，不把它归为 409 来源失效。保留原 AND/OR 证明、失效/遗忘、授权和实际领域 CAS。
3. 增加 `prepare_event_write`/`prepare_event_writes`，同一调用内汇总事件根、旧分段、别名、support、来源媒体和调用方指定的其他必要材料，再选锁、证明；`record_event` 自身复用该逻辑。相同持久 evidence unit 的事件版本先取得同一事务级事件锁，多事件一次按 key 排序。旧分段按 source ID 稳定顺序处理。
4. 发布、投影、媒体补充、撤回及部分来源写入调整为身份锁先于来源锁；发布 scope 已在身份锁阶段按 scope ID 取得所需最终锁模式，避免在同一 scope 上先共享再升级。对外部传入连接的 `disclose` 使用同连接 savepoint：后续证明或登记失败时，不留下已改变的受众；`RecoveryWait` 仍须继续向外抛出整个外层事务。
5. 已检查本卡相关传入连接的事件登记、身份校验、证据证明和发布路径：这些路径使用传入连接，内部 savepoint 不从池重新取连接。Growth 的 `apply_affect(connection=...)` 内额外开启身份校验事务已由主协调转交 Growth 窗口；不能据此断言它单独解释历史 PoolTimeout。
6. 归档批量登记沿用完整预备锁集登记各行，减少重复扩锁。对 `sources.propagate`、`sources.dispatch`、`sources.notify` 三个事务内可重入来源任务显式启用 Runtime 的 `resource_recovery=True`：按原 change/job key 保持原 payload，经确认回滚后才由原 job 延后重观察。当前内建消费者的撤回处理及派生任务入队使用同一连接；未来消费者若引入外部副作用，必须通过独立持久状态处理，不能依赖本任务重放。

## 调用合同与集成注意

- 单事件：`await sources.prepare_event_write(conn, context, producer=..., event_id=..., revision=..., parts=..., support_refs=..., additional_refs=())`。
- 多事件：`await sources.prepare_event_writes(conn, context, events, additional_refs=())`，其中每项为包含 `producer`、`event_id`、`revision`、`parts`、`support_refs` 的 dict；`support_refs` 可省略。返回值是按输入事件顺序排列的 native alias，领域调用方一般无需使用。
- 该准备必须在**任何相关来源锁和领域行锁之前**、使用与最终写入相同的连接和事务调用；一次提供本事务所有事件及所有必须同时有效的普通来源。后续 `record_event` 的事件标识、版本、parts 和 support 必须与准备一致。领域快照随后按稳定顺序加锁并做 CAS；若事件/依赖集或领域版本改变，放弃事务并重新读取，不能在持有领域锁后补锁。
- `additional_refs` 和事件的 `support_refs` 是必须全部有效的 SourceRef。含 `EvidenceSetRef` 的一般证明沿用 `prove_many_on(conn, context, references, lock=True)`，它先展开并一次锁定混合 SourceRef/EvidenceSetRef，再按原 required AND、alternative OR 求值；不能把替代组压平成全部必须有效。当前事件准备入口不接收 EvidenceSetRef；如实际领域调用需要同事务混合事件和 EvidenceSet，必须先确定完整集合并在来源域补充统一入口，不能分两次扩锁。
- `RecoveryWait` 必须逃出整个当前事务并完成回滚；由 opt-in Jobs/领域按原 job 延后重观察，重新验证来源、权限、版本与 CAS。它不是模型失败或远端熔断证据，不得按错误字符串盲重放外部副作用。实际来源撤回仍走既有终态。历史 unknown 和原请求/回执保持原样。
- 已通知协调者：基线 `memory/recovery.py:160-203` 先按快照收集来源，再锁 state/batch/episode，之后 `resume_rpc_on` 用原 authority 再验证，需核对锁后 batch 依赖与原 authority 是否仍已覆盖；不能把 `consolidation_retry.py` 相邻的不同函数误当成同一倒置链。`life/writing_progress.py` 循环可在锁首个 goal 后继续为后续目标补来源，应由 Life 窗口汇总后再按领域 CAS 提交。

## 必要集中验证与局限

- 新增 `tests/sources/test_rbf_lock_order.py`：共享 support 的独立事件并发、撤回等待、相同事件不同版本与旧分段、反向多事件顺序、未提交来源的同连接证明、闭包扩张 typed wait 不被吞、三个来源生命周期任务的 resource-recovery 注册、发布失败的 audience savepoint 回滚、已有 publication scope 上两个发布者的锁顺序。
- 主协调集成 Runtime 和领域改动后，可在明确的隔离 PostgreSQL+pgvector 测试库执行：`python -m pytest -q tests/sources/test_rbf_lock_order.py tests/sources/test_rb_authority.py tests/sources/test_rb_proof_batches.py tests/sources/test_rb_source_generation.py tests/identity/test_rb_scene.py`。项目夹具要求显式 `TIANSHU_TEST_POSTGRES_ADMIN_DSN`，不要使用生产数据库；再补领域窗口受影响的定向检查。
- 本工作树只完成完整 diff 静态审阅和 `git diff --cached --check`，后者无输出且退出码为 0；未执行以上测试，也未验证真实 PostgreSQL 并发或业务链。
- 历史日志中的连接池超时、锁等待与死锁有事实记录，但此前没有完整数据库服务端锁链；本卡修复的是源码可确认的锁次序/扩锁问题，不可宣称已证明全部历史死锁的具体成因或已经恢复真实业务。
- 集成后正常恢复应由协调者按领域原有调度和明确授权执行：确认旧事务已回滚，按原材料与原请求重读来源、权限和 CAS，观察已记录的模型/外部回执；只对已确认可重试的本地计算恢复，不伪造成功或盲重放未知外部效果。
