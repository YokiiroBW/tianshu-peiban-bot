# NR-09C 自主日程滚动安排

基线 `c8ff95113b90cabf5805f9b633f0ff829cc132a2`，分支 `codex/nr09-life`。本交接是本地实现与静态审阅，未运行测试、构建、真实模型或生产操作；提交 SHA 随回收消息提供。根负责集成、集中验证和串行发布。

## 实现

- `day_plan.py` 移除每日累计 4 项和剩余清醒时间 65% 限制。冲突查询只取仍 planned/running 的安排，完成历史不占未来容量；取消睡眠边界前强制 20 分钟留白，活动可恰好在 sleep_at 结束。既有跨活动重叠挪位、承诺前后保护、睡醒窗口、来源校验、续读原 checkpoint 和动作去重保留。
- `planning_engine.py` 移除每日计数短路。`decision_review_at(mark)` 优先使用现有 `review_after`；仅对无错误、无未消费原 generation 的有效决策，回退到保存的 `next_due_at`。普通时钟/成长通知保留这个绝对期限并重新挂到当前 CAS revision；到期允许同一材料再次决定，不会被 unchanged 永久冻结。正在当前时间窗内的 scheduled/running 活动优先继续，普通成长通知也等待活动完成；明确作者/承诺等重要变化沿用原行为。
- 完成链路复用原实现：想象活动完成写原 checkpoint → `activity-finished`；真实工具回执写实际结果 → `activity-result`；两者均走原 `life.signal`、revision 和来源/CAS，再决定下一步。没有新增完成扫描器或循环调用模型。留白沿用原 `review_time` 和有限到期任务。
- `recovery.py` 在已有恢复查询中包含缺 `review_after`、但已有有效指纹与 `next_due_at` 的日程。正常 `recover → PlanningEngine.schedule → run` 恢复旧 `day_arrangements_complete` / `unchanged_decision_material` 卡住的有效安排，无迁移、无批量状态改写。原 pending generation 和 last_error 的观察/有界纠正路径不变；未知动作仍仅 reconcile 原动作。
- `planner.py` 原 `life.plan:v2` TASK 完整锚点与单次 1+3 输出 schema 保留；只给新请求追加滚动说明，并用既有 counter 预留、测量完整实际 prompt。原冻结请求可继续按其原 TASK 解析，不改写旧请求/回执，不换请求身份。

## 根建议定向验证

新增 `tests/life/test_rolling_day_plan.py`：新场景使用生产 `o200k_base` 计数器。覆盖第 5、6 项实际接续、重复完成幂等、长活动超过旧 65%、刚好结束于 sleep_at、承诺/正在活动冲突、睡眠窗口、留白绝对期限和重复 tick、旧两种 reason 的重启到期与幂等、当前 running 和旧 unknown 保持、睡眠期间恢复只待原醒来边界。

`tests/life/test_planner_contract.py` 新增旧 TASK 单独冻结请求的解析断言。建议集中运行上述两份文件，并复用/运行必要既有用例：

- `test_planning_stream_repair.py::test_free_time_has_a_review_window_and_ordinary_notifications_only_rebind_the_due_job`
- `test_planner_execution.py::test_lost_response_and_refreshed_clock_only_observe_original_attempt`
- `test_planner_execution.py::test_successful_original_plan_is_consumed_after_legal_epoch_refresh`
- `test_activity_continuation.py::test_recovery_rebinds_known_continuation_and_original_unknown_without_replaying_them`
- `test_activity_continuation.py::test_old_checkpoint_goes_to_normal_planning_and_future_activity_does_not_suppress_it`

本代理只执行 `git diff --check` 与调用链/完整差异静态审阅，不宣称用例已通过。

## 上线日程唤醒

1. 正常启动沿用现有 LifeClock/worker 的恢复入口，授权仍从原 routine.context 刷新 `life.manage`，不扩场景。已保存的旧有效 next_due_at 会重挂队列；到期只按现有作息决定，休息时记为 actor_resting 并等待原 wake_at。
2. 若根需要即时触发一次正常复查，使用角色原场景、当前权限与当天 day-plan revision 调用现有 `DayPlans.reconsider(context, body)`，或 `POST /api/v1/life/day-plan.reconsider`。body 为 `client_request_id`（独立且可重复调用去重）、`expected_revision`（当天 plan revision）、`reason`；HTTP 另带 actor_id 及原场景身份字段。该命令仅发原 `life.signal` 排队，不绕作息、不直接调用模型、不重放任何动作。
3. 核对 planning.reason/next_due_at/review_after、当天 activity slots 和原 generation receipts。当前两角色若已睡觉，如实记录待醒；不修改生产作息制造第 5 项验收。大于 4 项的确定性行为由隔离场景证明，生产推进另记真实结果。

无共享接线、模型绑定、Memory、QQ 策略或 DDL 需求。保持旧分支与本工作树供根回收。
