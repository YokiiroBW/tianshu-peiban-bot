# RBW-models 交接

- 基线：`e2529c95a92857f368111d9ae5b23ee66800b636`。
- 实现提交：`38e4ac346ad08e058649aa77c6f197d240599fd1`；本交接随其后的独立文档提交交付，集成时取本分支 HEAD。
- 配额 profile 增量实现：`76c56aae0c3a799898b0631590d617a57fb6544a`；该提交之后仍需包含本交接更新。
- 范围：`src/tianshu/models/`、`tests/models/test_rb_*.py` 和本交接；没有修改共享 Runtime 装配、公共类型、迁移注册、配置根或其他领域。

## 实现

- Models 的 attempt 是唯一物理请求与结果权威。接受时在同一事务保存原冻结请求及其摘要、可选完成通知；同 ID 同内容返回原状态，不同内容冲突。结果完成后从持久 outbox 补投可信 `JobSpec`，结果先于订阅也可以补登记；消费方必须读取原 receipt/result 并自行做业务 CAS。
- broker 重启时，未提交的持久请求按原 ID/内容及已选路由恢复，已提交但不确定结果的请求保留 `unknown`。发送前复核来源、路由和供应商版本；记录编译后的 wire 摘要。
- `generate`、`stream`、原生代理复用同一 attempt。流的已记录片段按 sequence 顺序重放；观察者断开不取消云请求，明确取消才请求取消。receipt 中的 `stream_coverage` 标识片段记录和截断情况。原生代理若调用方提供 `X-Request-Id`，同一客户端使用稳定派生 ID。
- 云请求与外部资源 permit 分开：云侧不保留 unknown 并发占位，不继承旧固定前台闲置位；外部 permit 保留原 handle 与资源占位。实际上游额度、可选 cloud_concurrency 和本地连接容量分别生效。429 按 quota_group、503 按 route 冷却，遵守 Retry-After，冷却等待不占业务 worker；退避加入有界抖动。
- 绑定可保存有序 `fallback_routes`，路由必须有能力和 `verification.context_window_tokens` 证据，JSON 模式需要已验证 `json` 能力；仅对可无损翻译的 canonical 协议参数进行跨协议适配，无法证明兼容的备选不发送。嵌入备选必须与既有 profile 的完整 fingerprint 相同，同维度不代表同空间。请求同时固定 route_id 和 route_revision 后保持该路由，即使 `allow_configured_fallback=True` 也不改选；需要备选时先选择新尝试的实际路由，再固定该路由及版本。原生 payload 和 `ops.diagnose` 不静默切换。
- 每个 attempt 记录可观察 HTTP 时间点、连接复用、未知阶段、脱敏内容形状和实际路由/协议。DNS 与合并建连可能嵌套，供应商内部等待和计算保持未知。响应不完整时保留可取得的 usage、provider handle 及形状，不保存正文到诊断。
- `ops.diagnose` 有独立本地连接池/并发容量，真实相同 quota_group 仍共享上游额度。配置写入或删除与 `broker_reload_pending` 事实同事务保存，可按原事件及版本补载，不创建新配置命令。
- 统计从 attempt 账本原事务调用注入的 `statistics.record_safely_on(conn, event)`，以 `family=models`、原 attempt ID 和递增 revision 替换贡献；实际、估算及未知用量分开，模型回执不冒充业务应用成功。投影失败标记 incomplete，周期性从原账本补投。

## 公开接口和装配

- `await models.submit(request: ModelRequest, *, completion: JobSpec | None = None) -> ModelReceipt`；`status(attempt_id)`、`result(attempt_id, context=...)`、`cancel(attempt_id, reason="explicit_cancel")` 保留。`consumer_interrupted` 对云请求只表示观察者中断；真实外部资源所有者中断继续走其 permit 处理。
- `generation_profile(request)` 返回选中 plan 的 `quota_group`；`embedding_profile()` 返回其配置路由的 `quota_group`；receipt 的 `quota_group` 仍是实际物理 attempt 的额度域。非特权 embedding 请求可使用当前 embedding profile 的 route_id/revision 和 native payload，仍复核路由版本及向量空间 fingerprint。
- 调用方先持久化稳定 attempt ID 和请求内容摘要。receipt 的 `request_digest`、`completion_digest` 可核对丢失的 ACK；同请求而完成通知未能确认时，使用相同 request 和 JobSpec 重提交。`ModelEvent.sequence` 供观察者识别重放片段。
- `await models.observe_usage(attempt_id, *, context, provider_handle, usage: ModelUsage)` 只接受匹配原 handle、当前授权有效的 reported 计数；晚到用量递增原账本 revision，不改变 unknown 结果。
- 配置恢复：`pending_configuration_reloads(limit=100)`、`configuration_reload_status(event_id)`、`reload(configuration_event_id=event_id, expected_revision=revision)`；保存响应含 `configuration_event_id` 和 `broker_reload`，pending 时含 `error_code=broker_reload_pending`。
- Models 进程需独立注册 JobQueue 的 producer-only 路由并 `await jobs.open()`，将 `jobs=jobs` 注入 `ModelBroker`，停止时关闭；producer 需覆盖各域 completion task 和可信 lane。未注入 jobs 时 outbox 保留 pending，不能认为通知已送达。
- `ModelBroker(..., statistics=services.statistics, upstream_connections=<正整数>, diagnostic_connections=<正整数>)`；默认真实上游连接池 64 + 诊断池 4，须纳入部署资源总预算。`ModelClient` 的 `max_connections`、`due_connections`、`background_connections`、`control_connections` 可按角色切片。统计补投可调用 `await broker.ledger.backfill_statistics(batch_size=200)`，生产者名 `models`。
- OpenCode 会话哈希只读 `runtime.installation_generation` 单例 `id`；Runtime 迁移必须先创建并初始化单例。缺失返回 `installation_generation_missing`，不从旧配置导入代际。
- 各领域持有材料、claim 和恢复预算，按 Runtime RecoveryPolicy 预留/结算独立 attempt；Models 不自行构造领域 lineage 或自动重复物理请求。这项分工已由协调者确认。

## DDL 与清空

由协调者将 `src/tianshu/models/async_execution.sql` 注册到 Models 既有 `0002`、`0004`、`0005`、`0006` 之后，并在 Sources 忘却调用 Models 清理入口之前完成迁移。SQL 新增 submissions、stream_events、quota/route cooldown、configuration_reloads，以及 attempts timing/diagnostic/wire/route/provider revision/input estimate 字段；绑定新增 fallback_routes，quota group 新增可选 cloud_concurrency。

配置导出/种子需保留绑定的 `fallback_routes` 和作者显式设置的 `cloud_concurrency`。attempt、submission、stream event、cooldown 和 configuration reload 均是本轮应归零的天枢业务数据；统一 `runtime.installation_generation` 必须在新库重新生成，不作为配置回灌。

## 未执行验证及集成注意

- 本窗口**未运行测试、构建或烟测**；只做静态 diff 审阅和 `git diff --cached --check`。新增用例覆盖请求/通知身份、重启、补投、晚用量、回执回放、无正文诊断、连接阶段及路由约束，需由协调者集中运行。
- 集成前旧测试中有需要按新合同调整的假设：`test_retry.py` 断言固定无抖动秒数；`test_execution_timing.py` 中云 ModelRequest 仍假设保留前台空闲位；`test_cancellation_reasons.py` 的部分客户端断连断言仍假设调用自动取消；`test_native_settlement.py`、`test_output_budget.py` 等直接 mock 旧私有 `_events`/ledger，需适配实际持久接口。上述为静态识别，未运行，不能称为已验证失败或通过。
- 最终集成仍需核实 Runtime 的 `JobSpec` 新字段、producer-only route、代际、StatisticsProjection 的实际接口与迁移顺序，以及各领域 completion/RecoveryPolicy/CAS 接线；统计业务应用由领域事实证明。真实云服务、真实 QQ、部署、清库及生产均未操作。
- Retrieval 原有 `Embeddings.profile()` 对整个 profile 做 hash；新增 quota_group 会改变该哈希。仅额度域变化不代表向量空间变化，已提醒协调者由 Retrieval 根据向量空间/语义字段维护 profile 身份。增量用例 `test_rb_profile_quota.py` 未运行。
