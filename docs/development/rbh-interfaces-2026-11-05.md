# RBH 公共实现合同

本文件由根单写。接口若确有不适合之处，先向根回报具体替代；根统一通知消费者，不能各窗口自行改名或直接读写其他领域表。

## Models 权威证据（RBH-02）

继续使用 ModelReceipt 已有 `diagnostic`、`http_timing`、`request_digest`、`data_generation` 和 normal get/result 查询，不新增并行回执 API。在 `diagnostic.transport` 增加安全结构：

```
schema_version: "rbh.transport.v1"
phase: "dns" | "connect" | "tls" | "request" | "response" | "close" | "unknown"
error_class: string
os_errno: integer | null
tls_reason: string | null
host: hostname | null
port: integer | null
headers_sent: boolean | null
body_sent: boolean | null
evidence: "confirmed_not_sent" | "sent_or_unknown"
```

字段有长度/类型约束；不保存异常 message、URL path/query/userinfo、凭据、提示词、请求正文、供应商错误正文。明确未发送只接受连接建立阶段受信任的 connector 异常及一致的无发送/无输出证据；unknown、读超时、发送后断流、关闭异常、普通 AppError 不转成明确未发送。保持原结果状态和原尝试身份，不能把历史 failed 改 queued 重发。

历史回执的安全兼容输入为 `status=failed`、`dispatch=not_started`、`error_code=transport_not_sent`、`finished_at` 非空、`diagnostic.category=not_sent`、无输出且 `http_timing` 中实际开始建连、连接未完成、headers/body/response/output 均未出现，代际和 ID 匹配。缺证据保持原预算。新的证据字段由 Runtime 一个校验实现消费，领域不能复制弱化判断。

## Runtime 单一预算权威（RBH-01）

在现有 RecoveryPolicy 增加：

```
await policy.note_not_sent_on(connection, attempt_id, *, owner, expected_version, model_receipt)
await policy.computation_budget_on(connection, lineage_id, *, owner, expected_version)
```

两者返回 Mapping，包含 `total_attempts`、`computation_attempts`、`connection_attempts`、`max_computation_attempts`、`max_connection_attempts`、`can_retry`、`retry_at`、`dependency`、`reason`。`can_retry` 只表示预算/lineage 条件，不替代依赖门、来源和当前权限检查。第二个只读；第一个在已有 reserved→failed 正常归档后或历史 failed 状态上幂等记录权威分类，仍核对 owner/current_version/attempt version/代际/回执/取消/撤回；返回 `retry_at` 为可恢复的持久 due，不能给已有更晚尝试回拨 due。

原 `attempts`/`attempt_number` 永远保留单调的实际尝试序号。`computation_attempts` 为原总数扣除已被权威证明未发送的连接尝试，保守计入 reserved、unknown、实际已发送和纠正。计算预算维持已有领域 max_attempts；连接恢复另有有界预算，初始上限 16 次/lineage，不由每次 domain resume 重置。现有同依赖单探针租约、退避及其过期 CAS 继续唯一控制准入。若认为该具体上限或 API 需要调整，先给根依据。

共享 inspect/claim/reserve/fail 和到期查询全部使用一致的有效预算判断，不能只改一个 if；普通未分类尝试行为保持。连接退避有上界、同时只有一个有效探针；仍服从真实业务截止、来源撤回、暂停、取消和安装换代。不增加无授权的无限探测或新的全局服务。

证据校验、分类审计和有效预算由 Runtime 拥有，可用新 `rbh_*.sql` 增量扩展现有账本；不直接读取 Models 表，由领域从 Models 正常查询获得原 ModelReceipt。不要用修改 attempts/max_attempts 或重置原失败实现恢复。


### 已确认的判证返回与时间语义

身份合法的任意权威回执都可传给 `note_not_sent_on`。普通已发送失败、unknown、缺失或矛盾的发送证据返回同一预算 Mapping 和未分类 `reason`，原预算保持；领域不复制证据规则，也不吞掉真实身份错误。不存在 attempt 报 `recovery_attempt_not_active`，回执 attempt 不符报 `recovery_model_receipt_mismatch`，owner 不符报 `recovery_owner_mismatch`，代际不符报 `job_generation_expired`，当前/attempt 版本及暂停、取消、撤回校验失败报 `recovery_version_changed`。

`retry_at` 为仍有效的 lineage due、依赖 cooldown、probe 租约到期及 claim 到期中的最大时间，即全部时间门同时放行的最早持久时间。`can_retry` 仍只表达预算和 lineage 条件，不表示依赖门或来源/权限已准入。重复历史分类不得给后继尝试回拨 due。正常 fail 归档后分类，非合格证据不得导致原归档回滚。


精确 reason：未分类为 `not_confirmed_not_sent`，已确认并可继续为 `confirmed_not_sent`，计算/连接预算耗尽分别为 `recovery_budget_exhausted` / `recovery_connection_budget_exhausted`。严格未发送证据只用于预算分类；是否自动重试仍服从原 failure_category，只有 TRANSPORT/INFRASTRUCTURE 可恢复 due。不能仅凭 connector/TLS 类型推断必须人工修配置而新增永久阻断；服务端修复仍可走同一有界连接恢复。既有明确永久/配置错误、暂停/取消/来源撤回和 unknown 语义保持。

## Growth / Memory / Life 消费（RBH-03/04/05）

在已有 fail/observe 原回执边界调用 note；判断最大次数时消费公共预算，领域 attempt_number 继续作为序号。不能因依赖冷却提前把行改永久失败，不能在每个调用点实现自己的预算扣减。

历史恢复需要从现有 owner 账本取准确原 attempt，使用 Models get/result 权威回执；批次当前材料/来源/版本/权限有效后在领域事务内归档分类并安排已有恢复任务。所有原 ID、正文、原失败历史和 unknown 保留。幂等、并发、迟到事件不能重复恢复或覆盖新版本。

为集中定向恢复/持续有限恢复提供领域端口（可在既有恢复组件上实现，避免增加平行 service）：

```
await domain.reconcile_transport_waits(limit=32)
# bounded scan/classification/scheduling only, not a loop that executes every model
# returns {inspected:int, scheduled:int, ineligible:int, changes:[safe identities/reasons]}
```

具体对象路径由窗口交接给根注册到已有 Operations progress/tick。真实模型、网络或渠道执行仍走现有工作队列。队列恢复不是业务完成；最终记录由原结果决定。一次调用无界扫描所有历史或批量清理旧失败均不允许。

Memory 可把已有分区等待合并到最早持久 due，不能让旧隔离批次挡住独立新材料，也不能取消合法显式定向恢复；接口变化用正确任务身份和迁移交根说明。Life 提前醒来应在同一事务重新排最早 repair/review，保持静态人格、任务身份和语义去重。

## 对话及指标（RBH-06 / RBH-01）

ContextAssembler 对齐真实 MemoryService.projection，无 `reflection_limit` 伪兼容。原反思历史与当前行为表达的消费职责保持；用真实 service 方法联合用例覆盖，不能以接受任意 kwargs 的 fake 验收合同。

保留现有 transaction_errors 的累计含义，新增清楚的等待/事务冲突/数据库故障等可区分计数及有限安全异常分类；不把任意异常说成 SQL 故障。不往日志写真实内容或凭据。根负责迁移注册、Product 装配和巡检消费新指标。

请求绑定沿用领域原冻结 request 的 attempt_id/intent_id/代际校验；非空 Models request_digest 用既有 submission_digest(request) 验证。Runtime request_digest 的归一化语义不同，不直接跨值比较；旧回执缺摘要不伪造，仍按本合同完整历史证据保守判证，不能声称摘要已核验。
