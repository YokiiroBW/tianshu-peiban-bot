# RBG-02 Conversation / Life 结果恢复交接

- 种子 `5cd309ae67c9896feb787ad03cc65272c4884173`，独立分支 `codex/rbg-conversation-20261105`。本文件与代码同一提交；准确 SHA 以本提交及回执为准。
- Action 结果以原 `action_id`、`receipt_id`、`result_revision` 和原生任务 `data_generation` 登记，分别记录 renderer、dialogue、Life activity、Life commitment、detached contact、Memory 六个消费者。完成、不可应用、被新结果取代及来源失效分别留存；仅所有分支到达明确终态后写原有整体完成表。Life 活动、承诺、独立联系人和 Memory 的业务写入与分支完成标记同事务；对话/渲染复用原领域回执、版本和已存在的表达发送幂等机制。旧的整体完成记录继续阻止历史副作用重放。
- 旧结果仍使用原事件；发现 Actions 真实 head 已前进时，同事务排入真实 head 的 `conversation.result` 并将旧结果未完成分支标记 superseded。Life 归属以原 Action 的 activity_id、当前关联或承诺的原 intent/确定性 Action ID 固定，承诺轮转后不会落入通用 Memory 兜底。完成/取消活动不被迟到结果回退。表达回执已提交而联系人投影遗漏时，重入只补联系人投影。
- `MaterialSnapshotChanged` 与用户 expected_revision 冲突分开，只在本地事务已确认回滚且远端回滚不为未确认时有限重读。活动步骤耗尽本地重试后延后原 `life.activity_step` 并重新判断当前状态；已有关联 Action 时转原 `life.activity_reconcile`，不重发 Action。对话 tool link 快照竞争和 Life 结果写入也按此规则重读。
- 新账本只存标识和有限状态证明，原事件正文仅在既有原生队列中。Conversation 既有 Sources 分区撤回处理现在会按来源版本和片段匹配所有原生 `conversation.result` 原件及恢复副本，清除命中事件的正文、附件、覆盖和来源定位，仅保留原 action/attempt/receipt/状态审计与来源失效标记；失效事件不再消费。恢复入队和新 head 入队在事务中持有原来源校验锁，防止撤回与重入交错。未绑定代次的旧任务也清除正文，但不猜测其代次或重放。
- 新视频 parse 和历史 `invalid_model_output` 必须先经 RBG-03 的 `Actions.inspect_video_parse_recovery(action_id, context, expected_attempt_id=...)` 只读分类，并核对 action/attempt/receipt/revision；随后仍走原 `Actions.resume` 及 Life guard。`LifeWorker.plan` 没有添加通用 TimeoutError 或 CancelledError 捕获。

## 根协调装配

1. 在 Action 结果发布和 bot 消费启动前注册 `src/tianshu/conversation/rbg_result_consumption.sql`。它新增 `conversation.result_deliveries` 与 `conversation.result_consumer_progress`，不复制原事件正文；原事件保留在原生 `procrastinate_jobs.args`。
2. 运维侧使用 `conversation.rbg_result_recovery.ResultRecovery(database, jobs, identity, guard_on)`；`registration()` 返回 `conversation.result.recover` 的 inspect/invoke/observe 注册信息，`candidates(limit=32, cursor=None)` 只读列出原生结果任务，`recover_result(job_id, *, data_generation, action_id, receipt_id, result_revision, attempt_id=None, owner_token=None, material_digest=None)` 仅按校验通过的原事件重入既有 `conversation.result`。候选保留原任务身份，并观察同回执/代次的有效排队任务、真实未来 scheduled_at、活跃原生 worker 和消费者租约，不把正常等待当作停滞。根将该 registration 接入 `attach_operations`，无需在 operations 构造 ConversationService、Renderer 或 ResultConsumer；真正消费者仍是 bot-foreground。`ResultConsumer.recovery_candidates/recover_result` 委托同一适配器。
3. 合入 RBG-03 的 `Actions.inspect_video_parse_recovery` 及其后续 Action 生产者修复后集中验证。根已交 RBG-03 保证不同内容使用独立后继回执、保留原回执；本卡对已知同一 receipt_id 的不同 revision 或正文报告 `result_receipt_revision_conflict`，未伪造新 ID。旧完成记录缺少足够原始证据时保持已完成，不据此重放。
4. 恢复清单基于原生队列留存的原始 result job；如原任务记录已被外部删除，不能凭缺失事件重建其原正文或推断成功。标为 blocked 的未确认分支不自动重放。

## 必要用例与限制

- 新增 `tests/conversation/test_rbg_persistence.py`、`tests/conversation/test_rbg_result_withdrawal.py`、`tests/life/test_rbg_result_consumers.py`、`tests/life/test_rbg_result_parse_recovery.py`，覆盖来源检查和领域锁之间的快照变化、同回执并发、对话已提交而 Life 失败、明确终态、承诺轮转归属、乱序 head、来源片段撤回及原生副本去敏、恢复前来源复检、未来重试及有效唤醒不误报、按原事件重入和视频分类身份。消费者用例组合实际 ConversationService、ExpressionRenderer 和 LifeWorker，外部端口使用隔离夹具。
- 扩展 `tests/conversation/test_expression_order.py` 检查表达提交后联系人投影故障的幂等恢复；适配 `tests/core/test_episode_semantics.py` 的真实 Action 事件和新账本夹具，保留业务断言。
- 按本批要求没有运行测试、构建、烟测、真实模型、QQ 或任何生产操作。仅完成静态 diff 审阅和 `git diff --check`；运行结果、集成、部署及业务收敛均须由根窗口集中验证，不视为已通过。

## 集中验证后的定点夹具同步

- 根的 `central-1` 使用集成源码 `e5d6ccb4b2d932b06f0aa106aa6d215b6f24880e`。本窗口负责的 5 个失败涉及天气整理假 Models 端口和 3 个活动场景（其中期限场景有两个参数）。静态对比确认种子 `5cd309ae67c9896feb787ad03cc65272c4884173` 的 `activity_step` 主体与该集成源码的 `_activity_step_once` 主体逐字相同，`eligibility.py` 与原活动测试文件也未变；首轮失败并非此次结果恢复改写活动窗口逻辑。
- 原活动夹具未指定 `execution_mode`，而种子中的 `background_work` 将带能力且未显式 `focus` 的活动视为后台工作；仅对上述要求角色活动窗口的场景显式选用 `focus`。图片场景使用现有 `Images.definition` 与产品相同的 model-tool 注册和合法图片参数，期限、窗口截止禁止执行和 `day_finished` 断言均保留。
- 天气整理夹具按 Models 当前公共接口提供同 attempt 的 `status(phase)`、`submit(completion)`、`result`、`reusable_result`，校验请求摘要和数据代次，并根据输入协议返回合法空变更提案；保留原天气数据到提取材料、完成状态和无媒体缺项断言，另核对一次提交及原 attempt 的观察/复用。
- 此返修没有运行测试、构建或烟测；仅静态源码对照与 diff 检查，待根对受影响用例集中补验。
