# RBW-runtime 交接

## 基线、提交与范围

- 基线：`e2529c95a92857f368111d9ae5b23ee66800b636`；分支 `codex/rbw-runtime-20261105`。
- 运行时代码及回归用例提交：`278a007ccf35f1fef9e87429a182fe44e4b5d9ea`；本文在后续交接提交中。
- 配置修复恢复、执行代际上下文和任务路由增量：`040017fce7fb3ddf421a5a3e36a73248b7caccf7`，在前述两个提交之后应用。
- 工作树 `.codex/config.toml` 是协调者设置的普通速度改动，保留在工作树，不属于上述提交。
- 本窗口未操作生产、清理数据、推送或部署。**未运行测试、构建、烟测或真实模型/QQ 调用。**完成静态 diff 审阅和 `git diff --cached --check`；这不是运行验收。

## 已提交的运行时能力

- `JobQueue` 保留 Procrastinate 同连接入队，普通任务以 task、payload、subject、matter、版本和数据代际核对幂等身份。显式同一 wake lock 才可合并；无 lock 输入不被吸收。合并后的每个 key 均持久保存，单 key 取消不会取消其他有效 key。旧格式未绑定代际的任务不会进入当前代际。
- `register_route`/`register_static_routes` 提供不绑定消费者的生产路由；动态任务 `conversation.run`、`capabilities.execute`、`capabilities.media_job` 必须提供 resolver 或明确 lane。重算 API 使用 dirty revision、短租约、事务内结果与消费回执、独立覆盖水位、失败材料和历史；到期/过期恢复限定当前代际且仅在有注册 handler 时启动。
- 进程角色为 `interactive`、`model-broker`、`bot-foreground`、`bot-background`、`independent`、`operations`。队列按角色分组；单消费者和子进程有限重启，观测进程故障不退出其他角色。运维角色不创建空队列，健康依据真实 agent 状态。Docker 健康默认 `live`，`--probe ready` 可单独检查 API ready；业务进展为 unknown，角色、模块状态单独报告。
- `FairJobManager` 在现有 Procrastinate 账本内按组轮转队列，并由 `runtime.fair_claim_job` 根据域、主体、实际已用和进行中的时长及等待时间领取；任务锁、due 和 `SKIP LOCKED` 仍在短事务内处理。未提供 subject 时以 task 作为主体。完成后记账；崩溃时不把丢失的执行结果写成成功。
- `RecoveryPolicy` 提供分型、随机退避和 Retry-After、依赖冷却/单探测、分域/主体领取、原材料 lineage 物理 attempt 预算、未知结果观察、兼容路由与闭合拆分检查。成功物理 attempt 的合法 continuation 保留原成功记录与共享预算。运行时不代替领域判定来源、业务结果或执行外部操作。
- `resource_budget` 统计各角色业务/控制/同步池、队列池及监听、Broker owner、DB 线程、worker 并发、分角色 ModelClient 上限、Broker upstream/diagnostic HTTP、日志 writer 和每个能力实例的文件线程配置。整机文件/浏览器/媒体子进程资源仍需结合产品装配计数。

## 公开合同

- 数据代际唯一来源是 `runtime.installation_generation(singleton,id,created_at)`。`await jobs.current_generation_on(conn)` / `await jobs.current_generation()` 查询；`await jobs.assert_generation_on(conn, generation)` 在领域结果提交事务内持锁复核；同步仓储对应 `current_generation_sync_on` / `assert_generation_sync_on`。`JobSpec.data_generation` 默认绑定当前值，显式旧值拒绝。只在任务开始复核不足以防止任务进行时切代，领域在迟到结果/外部副作用提交前仍需调用事务断言。
- 生产端 `jobs.register_route(name, execution_lane=...)` 或 `register_static_routes()` 可入队而无可执行 handler；真正消费者必须 `register(...)`。重算生产端须 `register_recompute_route(...)`，消费端须 `register_recompute(task, prepare, execution_lane=...)`；`recompute_changed_on(conn, domain=..., key=..., change_id=..., task=..., payload=...)` 中 `change_id` 是原始变化身份。`prepare(claim)` 无 DB 连接，返回 `async apply(conn, claim)`；它在短事务内写领域结果，可返回 `RecomputeCoverage(isolated={material_id: reason}, repaired=(...))`。
- `RecoveryPolicy.register_on(conn, domain=..., subject_id=..., owner=..., material_id=..., material_digest=..., dependency=..., current_version=..., max_attempts=...)` 使用代际、领域和原始材料确定 lineage，不能使用子 job ID。`update_dependency_on(conn, lineage_id, owner=..., expected_version=..., expected_dependency=..., new_dependency=...)` 在原 attempt 已确定终态时切换依赖，不重置预算，旧 claim 失效；每个 attempt 保留实际 dependency。
- `claim_lineage_due_on(conn, lineage_id, owner=..., lease_seconds=30)` 只领取指定到期项；暂停、未到期、被占用或耗尽返回 `None`，成功返回含 `claim_token`/`claim_expires_at` 的行。`claim_due_on` 供领域按域/主体轮转批量领取。两者的 claim 用于 `reserve_attempt_on(..., original_state='failed', claim_token=...)`；unknown 原结果须先 `resolve_unknown_on` 记权威观察。
- 原物理 attempt 已成功而业务需继续时，先 `claim_continuation_on(conn, lineage_id, owner=..., expected_version=..., prior_attempt_id=..., reason=...)`，reason 是 `need_context`、`schema_correction` 或 `capacity_repartition`；随后 `reserve_attempt_on(..., original_state='succeeded', claim_token=claim['claim_token'])`。新 attempt 记录 `continuation_of`/reason，原成功状态与计数保留；领域仍负责校验原结果、来源和完整依赖闭包。
- 新路由：`life.diary_episode_changed -> DIARY`；`life.recovery_observe_activity`、`life.statistics_replay`、`life.statistics_backfill -> LIFE_CONTROL`；`companion.episode_changed`、`companion.growth.statistics -> GROWTH`；`sources.notify`、`sources.dispatch -> SOURCE_LIFECYCLE`；`conversation.model_complete -> CONVERSATION`、`conversation.statistics_reconcile -> CONTROL`。`capabilities.control -> CAPABILITY_CONTROL`。

## 迁移与共享装配

1. 在 `0001_runtime` 和 `0003_procrastinate` 之后、业务消费者之前，将 `rb_runtime.sql` 以 `Migration('0007_rb_runtime', files('tianshu.runtime').joinpath('rb_runtime.sql').read_text())` 注册到公共 migrations。该 SQL 创建安装代际、任务摘要/别名索引、调度服务计量及函数、重算和恢复账本。测试 PostgreSQL fixture 若先注册同名迁移，可与本包 fixture 复用，不能再次直接执行 DDL。
2. 协调者在 `__main__.py`/配置 schema 与 `Settings.database` 接六角色及 `resource_budget`；实际 `process.py` 使用 `settings.model_transport_for(role)`，Broker 构造需要 `jobs=jobs`、`statistics=services.statistics`、`upstream_connections`、`diagnostic_connections`。Diagnostics 构造需要 `events=events`。Supervisor 使用 Logs 包的 `EventLog(prefix='stdout-', uid=..., gid=..., hard_max_pending=..., drain_timeout=...)` 与 `emit_output(line, secrets)`，所以必须先合入相应 Logs/Models 接口。
3. `build_product(services)` 和各域生命周期须按角色切片。`DomainModule.start_roles` 可覆盖默认 owner；注册任务与启动副作用分开。`PROACTIVE` 同时包含 Conversation 和 Life，`SOURCE_LIFECYCLE` 同时包含 Sources 和 Memory purge；每个角色需注册本角色所有真实 handler，不能只注册 producer route。Life clock/Sources 下游消费者只由后台拥有，QQ owner 只在 interactive，Broker 只在 model-broker；独立能力承接媒体及其控制任务。Capabilities 现有显式 `ExecutionLane.CONTROL` 必须换 `CAPABILITY_CONTROL`，Memory 的旧 `runtime.role == 'worker'` 与 Capabilities 的旧 worker 判定必须按新 owner 修改。
4. Operations 模块以 `start_roles=('operations',)` 启动实际 agent，并设置 `services.role_ready` 为检查 agent 存活的同步/异步 callable，返回 bool 或含 `ready` 的 dict；未注册时 role not ready。协调者负责每角色轻量注入 `services.statistics` 和 API 的 `X-Tianshu-Data-Generation` 中间件。

## 未闭环与集中验证重点

- 本包提供重算/恢复公共机制，但现有 Memory、Growth、Life、Models、Operations 的领域 owner 仍需实际接入相同 lineage 和结果复核；原 `JobQueue.register(..., retryable=True)` 的 Procrastinate 固定三次及各域旧重试仍存在。未完成替换前不能称 RB-22 端到端统一恢复已完成。
- `product.py`、`config.py`、schema、公共迁移、CLI、日志包、Models、领域 owner 和 deploy 根不在本窗口单写范围。上面的装配完成之前，六角色和共享统计、代际及业务生命周期不能宣称联通。
- 集中验证需覆盖真实 PostgreSQL DDL/公平领取 SQL 与持续负载成本、同 lane 多主体推进和锁语义、队列提交前后崩溃、变更期间重算、别名取消/重放、代际切换、成功 continuation/unknown/换依赖共享预算、单项领取、各角色唯一 owner、可选能力故障以及完整关停。现有旧测试对未命名 wake 合并、旧 queueing_lock 字面值、三角色和无 generation 的假设需要按最终合同核对，不能把原用例失败机械视为新行为正确。
- 对外结果、模型提交和 QQ 动作的异步等待及关停语义仍由各领域与 Models 原账本实现和联合验证；本窗口没有证明真实供应商、实际资源上限或业务收敛。

## 配置修复恢复增量

- `await policy.update_version_on(conn, lineage_id, owner=..., expected_version='v1', current_version='v2', resume_due=True)` 只在原版本与新版本不同、当前未暂停/取消/源撤回、末次物理 attempt 已失败、没有 reserved/unknown 且预算未耗尽时将 `due_at` 设为数据库当前时间。失败时分别返回 `recovery_version_unchanged`、`recovery_version_changed` 或 `recovery_resume_not_eligible`；原 attempt、failure_category（包括 OTHER）、lineage 与已用次数不改变。版本身份必须由拥有配置的领域提供，不能凭空生成。
- 可选参数 `paused`、`cancelled`、`source_withdrawn` 的 `None` 表示保持，显式 bool 表示设置。当前已暂停/取消/源撤回的状态不能通过同一次 `resume_due=True` 调用清除并恢复。权威领域收到来源撤回时须在自己的事务记录 `source_withdrawn=True`，并在后续领域提交前复核真实来源；已分类为 SOURCE_INVALID 且错误码含 withdraw/retract/revoked/forgotten 的失败也会记录撤回。
- 在 `0007_rb_runtime` **之后**追加 `Migration('0008_rb_recovery_change', files('tianshu.runtime').joinpath('rb_recovery_change.sql').read_text())`；SQL 添加 `source_withdrawn` 并回填旧账本中已明确记录的撤回。公共 product migrations 和通用 PG fixture 由协调者接线，不能只改 0007 的已执行定义。
- `from tianshu.runtime.jobs import current_execution_generation` 返回当前 worker 已验证的原 job 信封代际；不在 job 中返回 `None`，不查当前数据库。handler 的 ContextVar 在退出时复位；迟到结果仍需在提交事务调用 `assert_generation_on`。
- 新路由：`companion.growth.input_retry -> GROWTH`；`life.experience_statistics_backfill -> DIARY`；`conversation.supplement_notify`、`conversation.example_usage`、`conversation.statistics_reconcile`、`channels.statistics_reconcile -> CONTROL`；`conversation.model_complete`、`channels.media_supplement`、`channels.media_supplement_expire`、`channels.resume_delivery -> CONVERSATION`。没有新增不存在的 Conversation 媒体/送达任务名。
- 回归代码覆盖 OTHER 分类保留、相同/过期版本、暂停/取消/撤回、未知/执行中/成功及预算耗尽的拒绝、并发 job 的原代际隔离和异常退出复位。**本增量仍未运行测试、构建或烟测**；协调者集中验证迁移回填、领域撤回传播及实际 handler 接线。
