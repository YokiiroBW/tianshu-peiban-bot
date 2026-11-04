# RBW-models 交接

- 基线：`e2529c95a92857f368111d9ae5b23ee66800b636`。
- 实现提交：`38e4ac346ad08e058649aa77c6f197d240599fd1`；本交接随其后的独立文档提交交付，集成时取本分支 HEAD。
- 配额 profile 增量实现：`76c56aae0c3a799898b0631590d617a57fb6544a`；该提交之后仍需包含本交接更新。
- 代际与行为引用增量实现：`d3c2ff50cd280a6c643f33048c9f35aa0cde5f71`；本交接随其后的独立文档提交交付，集成时取本分支 HEAD。
- 配置补载公平分页增量实现：`7944756689108edfeeeb061a21a63933facdfcc9`；本交接随其后的独立文档提交交付，集成时取本分支 HEAD。
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
- 配置恢复：`pending_configuration_reloads(limit=100, cursor=None)` 返回 `{"items": [...], "next_cursor": cursor | None}`；这是从旧 list 返回值调整的接口。每条仍按原 `event_id` 和 `revision` 调用 `reload(configuration_event_id=event_id, expected_revision=revision)`，状态可通过 `configuration_reload_status(event_id)` 查询；保存响应含 `configuration_event_id` 和 `broker_reload`，pending 时含 `error_code=broker_reload_pending`。
- Ops 首次以 `cursor=None` 开始一轮，逐页原样传回 `next_cursor`，本页单个事件失败时继续其他事件和后续页；`next_cursor=None` 表示本轮结束，下次调度从 `cursor=None` 回扫失败或新增事件。若每次调度限制页数，需跨调度保留当前游标，不能每次固定取第一页。游标内部以 `(created_at,event_id)` 作为位置和本轮上界；上界使持续新增不会无限延长本轮。它不是跨请求数据库快照：并发事务可能在游标之后才提交较早事件，由下一轮回扫覆盖。原事件 ID、revision、状态均不因分页修改。
- Models 进程需独立注册 JobQueue 的 producer-only 路由并 `await jobs.open()`，将 `jobs=jobs` 注入 `ModelBroker`，停止时关闭；producer 需覆盖各域 completion task 和可信 lane。未注入 jobs 时 outbox 保留 pending，不能认为通知已送达。
- `ModelBroker(..., statistics=services.statistics, upstream_connections=<正整数>, diagnostic_connections=<正整数>)`；默认真实上游连接池 64 + 诊断池 4，须纳入部署资源总预算。`ModelClient` 的 `max_connections`、`due_connections`、`background_connections`、`control_connections` 可按角色切片。统计补投可调用 `await broker.ledger.backfill_statistics(batch_size=200)`，生产者名 `models`。
- OpenCode 会话哈希只读 `runtime.installation_generation` 单例 `id`；Runtime 迁移必须先创建并初始化单例。缺失返回 `installation_generation_missing`，不从旧配置导入代际。
- `ModelRequest` 新增可信、可序列化字段 `data_generation: str | None`、`behavior_refs: tuple[Mapping[str, Any], ...] = ()`、`behavior_purpose: str | None = None`。任务或派生 Action 必须传自身原冻结 UUID；普通同步/native 请求无显式值时使用 Broker 启动时冻结的代际。`completion: JobSpec` 必须带原 `data_generation`；接受事务同时核对 request 显式值、completion、Broker 冻结值和当前 Runtime 单例，缺失/非法不以当前值代填。引用和显式代际参与请求身份，字段为空的旧请求身份保持稳定。
- `ModelBroker(..., validate_behaviors=callback)` 由协调者在 Models 进程注入轻量 Growth 包装：`await callback(context, refs, purpose=purpose)`，仅 `True` 表示通过，冲突或暂不可读抛相应 `AppError`。refs 非空必须同时有 purpose、绑定校验器；请求经配额与来源/路由检查后及实际 provider 调用前再次复核，未发出的失败记录为 `dispatch=not_started`。空 refs 不要求绑定。各领域仍须从被选中的材料传递完整可信引用；此接口本身不能推断调用方遗漏的引用。
- `ModelReceipt` 新增 `data_generation`、`completion_state`（`none`/`pending`/`enqueued`/`expired`）、`completion_error_code`。完成通知跨代际时保留原 attempt 及其状态，通知以 `expired/data_generation_expired` 终态退出 outbox；旧代际、未发送队列在恢复时取消，已提交不确定请求仍为 `unknown`。外部资源 permit 也在接收、准入及标记 submitted 时复核代际，但其所有者仍负责自身物理调用边界。
- 各领域持有材料、claim 和恢复预算，按 Runtime RecoveryPolicy 预留/结算独立 attempt；Models 不自行构造领域 lineage 或自动重复物理请求。这项分工已由协调者确认。

## DDL 与清空

由协调者将 `src/tianshu/models/async_execution.sql` 注册到 Models 既有 `0002`、`0004`、`0005`、`0006` 之后，并在 Sources 忘却调用 Models 清理入口之前完成迁移。SQL 新增 submissions、stream_events、quota/route cooldown、configuration_reloads，以及 attempts timing/diagnostic/wire/route/provider revision/input estimate 字段；绑定新增 fallback_routes，quota group 新增可选 cloud_concurrency。

协调者还须在 `async_execution.sql` 之后注册 `src/tianshu/models/generation_fence.sql`，并确保 Runtime 的 `installation_generation` 单例及 `JobSpec.data_generation` 已装配。此迁移只从旧 completion 中已冻结的合法 UUID 回填 attempt 代际，不猜测未知历史代际；不能证明代际的旧排队/通知会进入上述终态。`*.sql` 已由现有 Models package-data 包含。

配置导出/种子需保留绑定的 `fallback_routes` 和作者显式设置的 `cloud_concurrency`。attempt、submission、stream event、cooldown 和 configuration reload 均是本轮应归零的天枢业务数据；统一 `runtime.installation_generation` 必须在新库重新生成，不作为配置回灌。

## 未执行验证及集成注意

- 本窗口**未运行测试、构建或烟测**；只做静态 diff 审阅和 `git diff --cached --check`。新增用例覆盖请求/通知身份、重启、补投、晚用量、回执回放、无正文诊断、连接阶段及路由约束，需由协调者集中运行。
- 集成前旧测试中有需要按新合同调整的假设：`test_retry.py` 断言固定无抖动秒数；`test_execution_timing.py` 中云 ModelRequest 仍假设保留前台空闲位；`test_cancellation_reasons.py` 的部分客户端断连断言仍假设调用自动取消；`test_native_settlement.py`、`test_output_budget.py` 等直接 mock 旧私有 `_events`/ledger，需适配实际持久接口。上述为静态识别，未运行，不能称为已验证失败或通过。
- 最终集成仍需核实 Runtime 的 `JobSpec` 新字段、producer-only route、代际、StatisticsProjection 的实际接口与迁移顺序，以及各领域 completion/RecoveryPolicy/CAS 接线；统计业务应用由领域事实证明。真实云服务、真实 QQ、部署、清库及生产均未操作。
- 本增量新增代际拒绝、切换后 outbox 终态、行为引用跨 RPC 及准入后真实发送前失效的回归用例，仅做静态审阅和 `git diff --cached --check`，尚未执行；需 Runtime 新 `JobSpec` 和协调者集中验证。数据库检查与外部 provider 网络调用不构成分布式原子事务，已发出的调用仍只能依原回执/unknown 处理。
- 配置补载公平分页增量新增 105 条相同时间戳、前 100 条保持 pending、跨 HTTP 传游标和并发插入回扫用例；本窗口未运行，需协调者集中验证及 Ops 接线。
- Retrieval 原有 `Embeddings.profile()` 对整个 profile 做 hash；新增 quota_group 会改变该哈希。仅额度域变化不代表向量空间变化，已提醒协调者由 Retrieval 根据向量空间/语义字段维护 profile 身份。增量用例 `test_rb_profile_quota.py` 未运行。
