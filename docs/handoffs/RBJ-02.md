# RBJ-02 交接：持久唤醒、执行资格与单向去敏

- 工作树：`C:/Users/Administrator/.codex/worktrees/rbj-runtime/tianshu-peiban-bot`，分支 `codex/rbj-runtime-20261106`。
- 实际基线：`ec4b26a652d53d3dc40ab42a9f61734ff12e320d`；本卡代码及用例提交：`ed5251c22523b521c8f390163ecbb9863b1b78d7`。本交接由后续单独提交承载。
- 对齐不可变公共合同 1.7：`a5e5c5f0db09221e2e2fe8b7262b74054b004b61`，含 1.6 的 preflight-stop、覆盖压平和来源去敏。

## 实现与接口

- 在既有 `JobQueue`、`job_keys` 和原生 job lock 上登记 `life.activity_reconcile`、`conversation.result`。逻辑 wake key、完整原载荷摘要、代际与对象键持久化；不同变体使用各自逻辑键、共用对象原生 lock。保留最早合法 due 和依赖等待下界；retry 释放原生 queueing lock 不删除逻辑身份。
- 新增 `adopt_wake_on`、`cover_wake_on`：领域先核验原信封和覆盖依据，Runtime 核对 task/代际/载荷和物理状态，不改原 body/attempt。其他 worker 的 doing 不强制取消。多层旧覆盖按最多 32 条一页压平，`waiting/wake_cover_partial` 必须先提交本页再重试；追加覆盖事实保留历史证据，后来的指针改变不撤销已有精确 ordinal 的退出事实。
- 新增可信修复注册、授权、job 绑定、关闭及 `record_repair_preflight_stop_on`。授权以原 normal 失败摘要 CAS、耗尽预算、完整历史及无未决执行为前提；同一原身份最多一个有效待修复目标，每个缺陷和 consumer 的真实 repair 失败最多一次。带 repair 参数的 job 须在 enqueue 同事务 bind，延迟约束在提交时检查；payload 字符串不能自行发资格。`inspect_execution_on`/`begin_execution_on` 可选 `repair_ref` 和 `consumer`，normal 与 repair 统计分开，原始物理 `(generation, job_id, native_attempt)` 及 unresolved 保留。
- begin 与精确 preflight-stop 使用相同身份互斥；stop 只接受当前原生 worker 的 `closed/blocked/unknown` 和已持久化领域证据引用，不能覆盖已开始事实。已有资源 wait 保留资格，`local_result_application_unconfirmed` 记 unknown；真实 57014 没有被一律改为免费重试。covered、preflight-stop 和 source-invalidated 的精确退出事实使 native succeeded 不冒称业务 apply；无证明时保留 unknown。
- `JobQueue.redact_wake_on` 委托同一公开的 `tianshu.runtime.rbj_wake.redact_wake_on` conn-only 实现，保留 job ID、原摘要和原执行事实，仅保存新旧摘要、失效引用及有效摘要，不保存旧正文副本。兼容旧/空代际和历史 metadata 缺失或冲突，成功去敏可标记 `metadata_unverified`。只允许固定 source-invalidated 墓碑和原身份字段保留；正常/repair 外壳使用同一个事件摘要锁及失效摘要，禁止重新入队同一旧事件正文。有效返回 `redacted/already_redacted/changed/missing/waiting`；真正 try-lock 冲突才返回 `waiting/wake_lock_busy`，含数据库时钟的 `retry_at`。
- `observe_on` 增加 `covered_by`、`source_invalidated`；已有合法 Runtime 事件携带机械退出诊断，不能把物理 job 成功当业务完成。历史无元数据的行仍须领域依据原信封 adopt 或 cover，Runtime 不推测业务归属。

## 根集成与验证要求

1. 由根统一将 `rbj_wake.sql`、`rbj_execution_repair.sql` 加入正式执行迁移注册；放在既有 `rbi_execution_budget.sql` 之后，先 `rbj_wake` 后 `rbj_execution_repair`。测试 fixture 使用相同迁移名；不要另改已有 schema 或旧迁移。代码在上述新表和字段注册前不可投入运行。
2. Conversation 的来源派生通知收到 `waiting/wake_lock_busy`，须在该事务外层抛版本化 `RecoveryWait`，使用返回的 `retry_at` 和 `runtime:wake_redaction` 依赖，使现有 resource recovery 重试；权威 Source 撤回已先提交。`changed` 仅作有界重读后重新校验，`missing` 不冒称已消费。已开始/unknown 须沿原物理事实对账。
3. Life/Conversation 对旧格式或不同 payload 的覆盖须先完成领域 CAS/来源验证；压平 partial 需提交后继续。可信 fix 的实际版本注册与精确业务缺陷凭证由根和业务域提供，本提交不登记线上修复。
4. 收齐各域后由根集中执行实际 PostgreSQL/原生领取和联合用例，重点核对 retry 与后继的交错、跨队列对象锁、覆盖/撤回并发和历史重定向、repair preflight 前后、真实失败/资源 wait/unknown 分类、旧代际和 metadata 冲突、来源通知 rollback/retry、normal/repair 相同事件防重入。SQL、Procrastinate 接线和事务行为目前均未经运行验证。

## 实际检查与边界

- 已对全部改动的 8 个 Python 文件做 AST 语法解析；已审阅本卡差异并执行 `git diff --cached --check`，通过。
- 已编写定向用例代码：`test_rbj_wakes.py`、`test_rbj_execution_repair.py`、`test_rbj_redaction.py`，并为既有 RBI fixture 增加本卡迁移。按本批授权，**未运行测试、构建、烟测、PostgreSQL、模型、QQ 或生产操作**；不得据此宣称验证、集成或部署完成。
- 未修改共享 module/product、全局迁移注册、配置、其他领域或生产；未推送、部署、清理或重放外部未知副作用。

## 1.8 成功修复后的重复证明观察返修

- 对齐公共合同 `1d56370f` 第 16 节。已关闭 grant 只有在首次机械 `close_observation_ref` 精确指向同 generation/identity/grant 的 `repair`/`completed` 原物理 fact、历史完整且无 active/unknown 时，才将首次可信领域证明引用绑定到该 fact 既有 `observation_ref`。原 `closed_at`、首次机械关闭引用、原成功与失败事实保持；同领域引用重入幂等，不同引用仍冲突。手动/resolve 关闭、失败和等待不走该分支。
- 未新增迁移或公开接口。Conversation 负责将预算对账 resolve 与 close 使用的原事件/目标/持久证明引用统一；Runtime 不读取领域表或仅凭 closed 判定业务完成。
- 补充机械层用例代码，覆盖真实 completed 后绑定及重复/冲突、首次关闭引用保持、失败和等待的伪机械引用、history unknown/active/unknown、跨 grant 事实。按分工未运行测试或 PostgreSQL；根需集中复验这些用例及真实领域成功后重复恢复。
