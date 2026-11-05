# RBF-01：运行时等待协议与数据库资源

## 交付边界

- 基线：`33e5282f60fe4c803c3b16d498d1d924621ed1b9`。
- 代码及回归用例提交：`36c88b8adffb21badb7d23dbaac1f0a96abeffb2`；本交接单独随后的提交以 Git 记录为准。
- 工作树：`C:/Users/Administrator/.codex/worktrees/rbf-runtime/tianshu-peiban-bot`，分支 `codex/rbf-runtime-20261105`。
- 没有 schema、配置或全局依赖变更；没有生产操作、真实模型/QQ 调用或业务数据清理。

## 实现与接入

### 依赖等待

- `tianshu.runtime.recovery_policy.RecoveryWait` 继承 `AppError`，保留原 `code`，提供 `reason`、UTC `retry_at` 和 `dependency`。依赖冷却、正在探测、其他 claim 占用或已失效、明确尚未到期均表达为可复查等待；未预留新模型 attempt、不调用失败记账、不消耗模型预算。
- `await policy.inspect_wait_on(conn, lineage_id, owner=..., expected_version=...)` 只观察当前 due、claim 与依赖状态，不取得 claim 或预留 attempt。返回 `None` 不等于新 attempt 已获授权。探测持有者未知时有界延后复查；探测时间到期不等于原结果已确定。
- `recovery_claim_lost` 不赋予旧 owner 写权限。领域须从持久状态重新取得 claim 并通过来源、权限、版本、代际和自己的 CAS 检查，才能写入或继续。
- `RecoveryWait('source_lock_set_changed', retry_at, dependency='sources:lock_set')` 可用于 Sources 锁集合变化。必须先让该异常穿过整个当前事务并回滚，不能在已有部分写入的事务内捕获并提交。经 `Database` 事务退出时，异常的 `transaction_rollback_confirmed` 表明回滚是否已确认；同一 task 嵌套本 `Database` 的事务时需逐层回滚，明确为 `False` 不会被 Jobs 自动重排。事务外产生等待时，领域必须保证既有提交可从原持久状态安全恢复。
- RPC 显式类型合同：`recovery_wait_to_wire(wait)` 仅接受 `RecoveryWait`，输出六个字段 `kind='recovery_wait'`、`version=1`、`reason`、UTC ISO-8601 `retry_at`、`dependency`、布尔值 `rollback_confirmed`。发送端本地事务确认回滚且没有先前远端否定证据时才输出 `true`；未确认一律为 `false`。`recovery_wait_from_wire(document)` 严格验证字段集合、类型、版本、内容与时区，非法 envelope 抛 `ValueError`，普通 AppError/503 不隐式升级为等待。
- 接收端的 `remote_rollback_confirmed` 与本地的 `transaction_rollback_confirmed` 是独立证据。`recovery_wait_retryable(wait)` 和 opt-in Jobs 同时检查两者；远端为 `False` 时，本地后续回滚不能改变远端状态或让它自动重试。再次序列化也不能将远端 `False` 升格。既有仅本地等待的行为保持，领域仍须负责 claim/CAS 与持久状态。

### 资源失败与连接

- `tianshu.runtime.database.classify_database_failure(error, *, phase=None, rollback_confirmed=None)` 返回 `DatabaseFailure | None`。字段为 `reason`、`failure_phase`、`pool_name`、`role`、`sqlstate`、`rollback_confirmed`、`retryable`、`receipt_required`、`retry_at`。原异常类型保留，`Database` 将已知事实附在异常的 `database_failure`、`failure_phase`、`pool_name`、`database_role` 等字段上。
- 仅按 `PoolTimeout` 类型及 PostgreSQL `55P03`、`40P01`、`40001` 分类，且只有本地事务未开始或已确认回滚才能标记可延后。commit 阶段连接、I/O 或取消等结果不明时 `receipt_required=True`、`retryable=False`。不要根据异常文本推断远端失败或盲目重放。`57014` 可能是主动取消或 SQL 超时，本接口不猜测。
- `async with database.transaction(connection=conn)` 和同步 `sync_transaction(connection=conn)` 显式复用原 owner 的连接；不会再从池中借第二条，不产生新的提交边界或改变外层隔离。仍优先直接使用已有 `_on(conn, ...)`/`jobs.enqueue(conn, ...)`。单个连接不可供并发 task 共用；跟踪嵌套回滚的范围是同一 `Database` 实例与同一 task/同步线程。
- business、control、sync 和 queue 连接使用 `tianshu:<role>:<pool>` application_name。进程心跳 `registration.database_resources` 提供每池取连接等待、事务占用、失败计数、原生 pool stats 和 sync slot 等信息；control 池保持独立。没有扩大连接池或统一降低并发。进程间历史池身份与过去的阻塞链仍不能由此倒推。
- 同步线程/异步 SQL 取消时，等待原清理完成后才释放相关连接或 slot；多次取消不应使线程容量提前归还。

### 原 Jobs 安全重唤醒

- `jobs.register(name, handler, ..., resource_recovery=True)` 是显式 opt-in，默认关闭。领域必须保证该 handler 每次从原持久状态、动作及回执恢复，不盲目重复外部效果。已有 `retryable=True` 的普通重试与此设置独立；typed RecoveryWait 不会落入普通三次重试，只有显式 opt-in 且本地/远端证据允许时才会重排；其他普通错误的原重试行为保持。不能将未确认回滚或 commit 未知当作资源重试。
- 已确认的本地资源争用复用 Procrastinate 原 job、业务 key、payload 和 generation，10 秒起指数退避，单次最多 300 秒；`RecoveryWait` 使用其 `retry_at`。这是同一队列工作重新观察，不新增模型物理请求或 attempt。
- 当原 doing job 带 todo-only `queueing_lock`，重排前释放该原 job 的合并锁，避免与并发后继 todo 冲突；保留原 job ID、业务去重键、payload 和后继。领域仍负责对象级 CAS 与回执，重复唤醒不能重复应用业务。
- 等待事件为 `job.deferred` / `deferred`；数据库等待附已知的 `failure_phase`、`pool_name` 和 `dependency_ref=database:<pool>`，不冒充模型失败。**接线要求：**RBF-08 的 `events.py` 必须接收该事件、结果和两个元数据字段，根的 Supervisor 日志白名单需保留字段；在此接线前旧 EventLog 会拒绝该事件，Jobs 会按既有 best-effort 日志约定吞掉记录错误。
- 若队列本身不可用，不能声称已经成功延后：原生 worker/队列状态与后续进程恢复仍是权威。提交不明时先用原 `CommandStore.find`/`replay` 或领域原始动作/模型回执对账；仅确认本地事务回滚后重做该本地阶段，不能重放发送、下载等不明副作用。

## 回归用例与集中验证

新增 `tests/runtime/test_rbf_recovery_wait.py`：覆盖冷却、探针占用、claim 丢失、到期、预算耗尽与未创建新 attempt。

新增 `tests/runtime/test_rbf_database_jobs.py`：覆盖池身份及独立 control、连接复用、池竞争、嵌套事务逐层回滚、SQLSTATE、commit 未知、原 job 重排与队列合并锁、来源等待、取消 SQL 和重复取消同步线程。

补充 `tests/runtime/test_rbf_recovery_wait_rpc.py`：覆盖版本化 typed envelope 的严格校验、跨 RPC 原因与时间、远端未确认回滚、本地逐层回滚和多次转发不能洗白远端否定证据。

建议协调者在完成 RBF-08 事件装配及领域接线后，用本批隔离 PG 驱动先运行：

```text
files tests/runtime/test_rbf_recovery_wait.py tests/runtime/test_rbf_recovery_wait_rpc.py tests/runtime/test_rbf_database_jobs.py tests/runtime/test_rb_recovery_policy.py tests/runtime/test_rb_jobs.py tests/runtime/test_execution_lanes.py tests/runtime/test_transactions.py tests/runtime/test_events.py tests/runtime/test_rb_roles.py
```

再按各领域交付运行 Memory/Life/Growth 对应的定向用例，检查资源等待不会写 terminal 或重发模型/外部动作，以及原业务记录和唤醒在事务内一致。

## 实际验证和限制

按任务卡，本窗口未运行测试、构建或烟测；只做静态源码/diff 审阅及 `git diff --cached --check`。新增用例尚未执行，不能据此宣称集成通过、部署成功或实际业务恢复。队列连接失败、跨实例/跨 task 的事务关系、用户权限和最终业务 CAS 仍须在各自权威层处理。
