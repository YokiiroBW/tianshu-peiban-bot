# RBW-conversation 交接

## 范围与代码

- 本卡覆盖 RB-06、RB-12、RB-16；基线 `e2529c95a92857f368111d9ae5b23ee66800b636`，分支 `codex/rbw-conversation-20261105`。
- 主代码提交：`b86e7c27fcf776bd0b3662680b292c650ce5abe0`；配置修复唤醒与事务装配增量：`46020a9ea6d853a1eb6ebe1001e7370270f1ae89`。
- 保留协调者修改的 `.codex/config.toml`（普通速度、Fast 关闭），不纳入本卡提交。

## 实现

- 消息原事件、分片顺序和摘要固定；缺失媒体以带原因的结构化单元参与上下文。迟到附件以原 part/source 的独立补充修订接入，通知 Memory 更新同一 Episode，不重新入站或自动回复；附件源在读取和落库时复核。来源撤回取消受影响的未完成输入工作，已提交动作的已知/未知回执保留。
- 初始下载失败纳入逐片、包含首次尝试的最多三次恢复预算；仅瞬时/基础设施故障自动延迟重试，路径映射缺失等待配置变更。桥接插件持久 journal 的旧事件按安装数据代际 `created_at` 下界确认并忽略，新输入引用旧内容走现有显式授权路径。
- 上下文先保留人格、当前输入及必要的指代/回复/工具单元，再考虑可选 Memory、Growth 和 Life 材料；清单按材料身份、修订和完整依赖记录选择与舍弃原因，选中后复核来源和行为语义。普通聊天预算保留 16000/2048，后台聊天预算为 200000 输入加输出预留；估算和 provider usage 分开。
- 聊天模型请求冻结后交给异步提交和原 attempt 回执观察，不让模型等待占住对话执行槽；未知结果只观察原尝试，不重发旧外部效果。行为依赖贯穿模型、工具、表达、渲染与发送；多段发送保留已送达、未决和未知的真实回执。
- `DialogueResult` 增加 `context_selection`、`token_usage`、`provider_usage`、`flow`，其中 flow 的输入、各轮 accepted/parsed/applied/expression/delivered 来源于已保存事实或回执；没有阶段材料时使用 `not_applicable`，旧记录缺少材料时 coverage 为 `not_recorded`。
- 新增基于原账本的接收、处理、撤回、投递和媒体/恢复统计投影及有游标的失败补投；群词只记录查询的 matched/no_match，未推测模型已解释或使用。
- 运维 QQ 通知复用唯一 Channels/Actions 发送者：`notify_operations(system_context, *, notification_id, target_ref, text, source_refs=())`、`operations_notification_status(context, *, notification_id, reconcile=False)`；调用者须提供可信 SYSTEM 场景，通知不创建角色对话 turn。

## 主协调装配

- 来源消费者请构造 `ConversationDependencies(jobs=runtime.jobs)` 并 `bind_statistics(statistics)`；保留其 `actors_for_source`、`invalidate_actor` 对象绑定。`ConversationService.bind_statistics(statistics)`、`Channels.bind_statistics(statistics)`、`Channels.bind_recovery_policy(policy)`、`Channels.bind_behavior_resolver(growth)` 均需装配；Actions/Models 的行为 resolver 也由各自共享入口绑定。
- `ModelRequest.behavior_refs`、`behavior_purpose` 和 `data_generation`，以及 `JobSpec.data_generation`，须包含协调者/Models/Runtime 的正式公共字段。行为依赖取实际被选单元的完整闭包；`support_only` 合格，规则匹配按 `(rule_id, requested_revision, requested_semantic_revision)`。
- Sources 需要带 `connection`/`generation` 的 `record_event`、带 `generation` 的 `register_media`、`capture`、`register_media_supplement`，以及 `validate_media_supplement_on`。Memory 的 `supplement_episode` 使用协调者确认的新 `generation`、`connection` 参数，与 Conversation 的补充通知共享同一事务。Life `Contacts.received` 需支持可选 `connection` 并复用调用方事务，避免并发入站嵌套连接池互等；主协调已交 Life 补齐。
- 正式切换数据时需同时更新 `runtime.installation_generation.id` 和 `created_at`；普通升级保留这两个值。提供的时间戳粒度取决于上游消息协议，需集中验收切换边界。
- 共享 `runtime/migrations.py` 中在 `0032_conversation` 后登记 `0039_conversation_context_selection`、`0040_conversation_input_supplements`，在 `channels-0001` 后登记 `channels-0002-media-supplements`；fresh schema 内对应 `IF NOT EXISTS`。迁移新增 context selections、输入附件补充、渠道附件补充表，以及 turn 原始摘要/撤回时间/数据代际、expression 观察时间、channel event/call 相关字段。
- 新任务静态路由：`conversation.supplement_notify`、`conversation.example_usage`、`conversation.statistics_reconcile`、`channels.statistics_reconcile` 在 CONTROL；`conversation.model_submit`、`conversation.model_complete`、`conversation.model_consume_expire`、`conversation.model_observe_unknown`、`channels.media_supplement`、`channels.media_supplement_expire`、`channels.resume_delivery` 在 CONVERSATION。不要路由到不装配 Conversation 的独立 MAINTENANCE 进程。

## 未闭环与验证

- 映射配置修好后调用 Runtime 新的 `update_version_on(..., resume_due=True)`，在同一 lineage 和已用预算内重新安排 due，保留原错误分类和失败回执；需主协调纳入 Runtime 的 `rb_recovery_change.sql` 并做跨域集中验证。
- 本卡未合入其他工作包，`Actions` 实际外发与 `Growth` 延迟统计等跨域写入的数据代际围栏需主协调与各拥有者联合核实。现有旧同步模型夹具及测试替身也需在集中验证时对齐新公共接口。
- 本窗口只做静态 diff 与 Python AST 检查，按授权未运行测试、构建、烟测、真实模型或 QQ，未部署、清理或触碰生产。集成后必须验证真实消息、旧桥接 journal、晚到附件/撤回、原始回执、配置修复重试、实际模型/工具/部分发送，以及统计账本回填；不能把本地代码或健康探针当作真实业务通过。

## RB-19 输入与学习状态补充

- `DialogueResult.input_status` 按原输入 `input_id/event_id/event_revision` 列出 `context.selection` 与各次 `attempts`；某次输入实际提交须同时满足 `selected=true` 和 `dispatch=submitted`。没有选材或派发证据时为 `not_recorded`；`reply_compliance` 为 `not_recorded`，不推断模型遵守或用户认可。
- `learning` 通过 Growth 公开的 `input_learning_status(context, *, inputs=...)` 按原身份批量读取。`state=reviewed` 只表示输入已整理；`revision_state=applied` 需要 Growth 返回精确的 `revision_evidence`。未返回记录为 `not_recorded`，读取失败或合同不符为 `unavailable`；不直接访问 Growth 私表。
- 自然语言输入没有可信的逐条纠正分类，界面应按“输入进入请求”和“输入的学习/规则修订状态”呈现，不把所有输入标成纠正。对话已结束但学习状态仍待处理时，页面需继续刷新；具体结果以 Growth 已授权的实际证据为准。
- 本增量随本文件同提交；新增针对多轮/合并输入和学习证据的回归用例，但本窗口不执行测试或构建，由主协调集中验证并装配 Growth 正式接口。
