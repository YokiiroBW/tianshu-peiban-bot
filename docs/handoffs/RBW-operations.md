# RBW-operations 交接

## 基线与提交

- 分支 `codex/rbw-operations-20261105`，基线 `e2529c95a92857f368111d9ae5b23ee66800b636`。
- 主体实现 `72d305316e85054f77dc8eeb0e71667611218b9a`；跟进 Models 原配置回执 `5b69206101570a1683f5ae6890e8ea8677bad94f`；接入 Growth 恢复窄口 `3123d3b40f800d11d63b45052ab3856161a32e90`。主集成需要按顺序取三者；本文是其后的独立交接提交。
- 追接 Models 最终分页合同的代码增量为 `f265c20e0c8315fa12a3ff88167b0fe564be4455`，依赖 Models 的 `7944756689108edfeeeb061a21a63933facdfcc9`。此代码提交之后为本次交接更新。
- 本卡只改 `src/tianshu/operations/`、`deploy/core/operations/`、`tests/operations/` 和本文。工作树 `.codex/config.toml` 的正常速度配置由协调者设置，保留为未提交修改，不属于本卡提交。

## 已实现

- 独立 Operations loop 消费 Logs 的增量游标与结构化错误，按依赖、错误族、版本聚合、按 event ID 去重。合格且超过 due 的领域工作可由 `progress` 窄口发现；暂停、取消、执行中和等待依赖的正常状态不报无进展。就绪只说明循环状态，不代表业务收敛。
- 模型诊断使用现有 Models；配置主路由和可选备用路由，输入仅为受限结构化事实。默认每次事件一个诊断，仅在原诊断确定失败时允许一次备用；结果只能选择登记动作、观察或升级，不能执行代码、SQL、shell、删数据或重放未知外部效果。设置保存者的可信上下文被保存并在调度时重新授权；未配置时明确升级。
- 确定性动作：`model_configuration_refresh` 对 Models 每条原 `configuration_event_id` 和 revision 调用登记重载并读取原回执；`life.activity.reconcile` 仅核对原行动；`memory.index.rebuild` 通过 Memory 原授权窄口恢复单对象派生索引；`growth.retry.resume` 通过 Growth 原授权窄口唤醒指定失败批次；`capability_reroute_pending` 只转移确定尚未开始的原 Action。域管理/观察动作只建立有界运维控制记录，物理模型尝试仍由领域自己的 RecoveryPolicy 管理。未知结果只观察原权威，不盲目重发。恢复成功必须有实际领域观察；多个目标逐项验证。
- QQ/SMTP 通知有稳定 ID、独立 outbox、重试与原回执；QQ 经既有绑定场景、渠道及身份授权，未知发送只查原结果；SMTP 接受不表示阅读。未配置接收人不猜测。宿主 watcher/spool 在 `deploy/core/operations/`，只检测显式登记的本产品目标，重启只允许符合产品/角色/代际标签且确定已停止的容器，详细部署约束见该目录 README；没有在本窗口执行或安装。
- 支持显式 `root_incident_id` 因果关系：仅所有相关根因仍在本轮事件中活动且没有无关联发生时抑制下游 opened 通知；未解决下游在根因结束或出现独立故障后重新告警，严重升级不抑制；根事件详情返回 `caused_incidents`。不从相同依赖名称猜测因果。
- 统计投影按权威事件 ID 和 revision 增量订正 minute/hour 桶，支持迟到用量、未知值、有限维度、样本/覆盖/水位、1–7 天、模块/角色/路由过滤；使用现有运行资源、可选 Prometheus 和基础设施 host 视图。不以调用数替代业务数，不生成无分布依据的 p95。模型/领域统计必须由各 owner 接入；投影失败不回滚业务，缺口须由 owner 补扫。

## 公开装配与域迁移

`build_operations(runtime, identity, *, actions=(), diagnostic_context=None, qq_send=None, progress=None, policy=None, host_spool_directory=None, prometheus_url=None, capability_actions=None, context_for_action=None, life_worker=None, index_jobs=None, growth=None, channels=None, infrastructure=None)` 返回 `OperationsComponents(module, statistics, incidents, executor, agent)`。主组装在 HTTP 所在 role 注册 `module` 的路由，在 `operations` role 启动同一模块的 loop；其 `start` 注册 `services.role_ready = agent.status`。该 role 不应启动 Life、Memory、Growth、Channels 的业务 owner；要确保对应窄命令产生的 `life.recovery_observe_activity`、`memory.index_retry`、`companion.growth.retry`、渠道发送 job 在各自 owner role 有注册并运行。

- `life_worker` 为可执行该窄恢复口的 `LifeWorker`；`index_jobs` 为 `MemoryWorkers.index_jobs`，本函数调用其 `bind_observability` 并注册 `recovery_inspect/invoke/observe`；`growth` 为暴露 `set_operations_guard` 和 `operations.inspect/invoke/observe` 的 GrowthService，注册观察/领域托管唤醒，由 Growth 原 owner 和 RecoveryPolicy 核对物理尝试预算；`capability_actions` 是拥有 `inspect_recovery`、`invoke_registered_recovery`、`observe_recovery`、`context_for_action` 的原 Actions；`channels` 是现有 Channels，绑定后使用 `notify_operations` 和 `operations_notification_status`。必要时可显式注入经重新授权的 `context_for_action`。不要用主聊天/模型上下文取代原授权。
- `progress` 是无参 async callable，返回事实迭代项：`eligible=True`、带时区的 `due_at`、`dependency`、`material_id`、`version`，可给 `error_family`、`role`、`paused/cancelled/executing/waiting_dependency`、`root_incident_id` 和精确 `target`。Life 的 target 需 `actor_id/activity_id`，Memory 索引需 `kind/id/revision`，Growth 需 `actor_id/scope_key/retry_key`；每个接口应从领域权威事实给出机械身份，不传正文或猜 ID。主协调者已将这些窄口分配给 Life/Index/Growth owner；本提交不冒称它们已装配。
- Models 最终合同须具备 `pending_configuration_reloads(limit=100, cursor=None)`，返回 `{items,next_cursor}`，以及 `configuration_reload_status(event_id)` 和 `reload(configuration_event_id=..., expected_revision=...)`。Ops 按原样保存 `next_cursor` 到 `operations.consumer_checkpoints`，后续调度和进程重启继续当前轮次，游标为 null 后下一轮从头回扫；单条事件入库或通知失败不会阻止后续条目，原记录仍 pending 时下轮重试。覆盖信息限定为这一有限轮次，含处理/失败/无效计数。
- `incident.ingest(event)` 可接收明确且已存在的未解决 `root_incident_id`。目前 Logs `log_changes` 白名单未提供该字段，只有 `dependency_ref` 不能证明上下游因果，需由可靠的领域/日志事件或 progress 事实接入；无证据时通知各自独立。`material_id`/`failure_id` 是源事实，不应把任意 failure ID 当成领域对象 ID。
- 统计生产者复用 `statistics.record_safely_on(conn, event)`，失败返回 False 且由原 owner 标记不完整并按权威账本补扫；`mark_producer_on(conn, producer, watermark=..., complete=..., detail=...)` 保存覆盖。需要各 owner 注入同一个投影接口并完成自己的回填/水位：channels、conversation.messages/delivery、models、memory、growth、life.plan 或 life.planning、life.experience、capabilities.media.jobs 或 media；Operations 对自己的恢复尝试可重放投影。`recovery_evaluated` 是已验证/失败的可评估事件分母；缺该指标的域，修复率显示未知而非猜分母。
- 模块自带 `Migration('operations-0001', .../schema.sql)`，主迁移注册时要先完成 Runtime 的 `rb_runtime.sql`、installation generation 和恢复策略表，再注册本域 DDL；共享 `pyproject.toml` 增加 `tianshu.operations` 的 `*.sql` package data，测试发现包含 `tests/operations`。不在本卡修改共享入口、迁移主线或部署根。

## HTTP 与设置 DTO

所有路由均为 POST 并要求现有 `config.publish`：

- `/api/v1/system/statistics`：`{start?, end?, timezone?, module?, actor_id?, route_id?}`；时间为带时区 ISO 字符串，范围最多 7 天，返回 `range/overview/cumulative/series/breakdowns/resources/recovery_rate/freshness/coverage/as_of`；异常时返回 unavailable 和不完整覆盖。
- `/api/v1/operations/incidents`：`{state?, cursor?, limit?}`；`/api/v1/operations/incidents/get`：`{incident_id}`，包含诊断、恢复、逐目标状态、通知、明确下游及 `registered_actions`。
- `/api/v1/operations/settings` 读取；`/api/v1/operations/settings/save` 使用现有 command envelope，包含 `client_request_id`、`expected_revision`、`value`，可选 `smtp_password` 或 `clear_smtp_password`。`value = {diagnosis:{primary_route_id,backup_route_id}, notifications:{qq,smtp}}`；QQ 为 `{connection_id,recipient_id}` 或 null；SMTP 为 `{host,port,sender,recipient,username,credential_ref,tls}` 或 null。密码使用现有 CredentialStore 的 `sealed:operations:smtp` 引用，响应不返回密码；调用者本人需有原有权限。
- `/api/v1/operations/recover`：`{client_request_id, incident_id, action, expected_revision, target?}`，只接受已登记动作，返回原 command/attempt 回执；目标及来源由领域命令重新检查。

## 未验证和后续

遵照本批授权，本窗口没有运行测试、构建或烟测，也没有调用真实模型、QQ、宿主 Docker、生产或部署。已静态审阅变更并通过 `git diff --cached --check`；回归用例已写入，须在主协调者完成所有组件和共享装配后集中执行。尤其需要真实检查 Logs/领域进展目标、Models 回执与公平分页、逐目标恢复、投影覆盖与补扫、QQ/SMTP 原回执及宿主 spool/故障场景；健康不等于这些业务路径已通过。宿主整体断电即时告警仍需外部探测。
