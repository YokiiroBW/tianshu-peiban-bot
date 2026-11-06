# RBJ-05：观察与结果应用分离、head 推进交接及原子业务写入

状态：第一批，已派发并确认开始。这是新卡，不续写旧 RBI/RBH/RBG 已完成任务。

- 既有窗口：RBW Life日程与后台执行，thread `01a1a772-e80b-7fa0-95d3-d32d50649745`，host local。
- 唯一工作树：`C:\Users\Administrator\.codex\worktrees\rbj-life\tianshu-peiban-bot`；分支 `codex/rbj-life-20261106`。不要写旧 cwd 或协调检出。
- 代码起点 `39da2d283deb17de2739b3c24fc1eeedc96cfa69`；实际准备提交由根正式派发消息及 dispatch 记录给出，必须核对一致。
- gpt-6.1-terra / max，普通速度（service_tier=default、fast_mode=false），不自行开启 Fast。
- 依赖：RBJ-01, RBJ-03, RBJ-04。依赖窗口使用冻结合同与夹具，不读写其可变目录；接口不明确即报根并继续自身独立工作。
- 先读本卡、[完整方案](../source-result-repair-plan-2026-11-06.md)、[公共合同 1.2](../rbj-interfaces-2026-11-06.md)；CURRENT/AGENTS 顶部 RBJ 条款覆盖旧阶段。

## 完整交付范围

1. 追踪 activity_step/activity_reconcile/commitment 的终态应用入口。观察者遇已有 terminal 原回执仅调用 Actions.ensure_result_consumption，不再用 DirectResultFence 绕过已停止的共同消费者直接 apply。
2. 保留 pending/running 控制写、404 unknown 观察、原 Action 的合法恢复和已消费失败回执后期限到达的新领域决定。这些是新的控制/续接决定，不能当作同一旧事件再次应用；不因去掉旁路而删掉业务能力。
3. 对本次新增确认竞态：原 activity_step 读到 pending/running，在 fence 前同 Action 已变成更高版本 terminal。保持旧事务回滚，核对同 Action/代际且真正更高 head 后有界重读并交接消费者，不重跑外部 invoke。头回退、同版本冲突、来源变化等错误不能被 blanket 409 分支吞掉。消费预算耗尽须准确保留 blocked。
4. ResultConsumer 调用的 _activity_result/_commitment_result 仍拥有领域决定，Life+Goal+Memory+proof 原子性不拆。复用 Sources 同 context 准备，完整锁读集不明确时不缓存授权布尔值；不直接改 Sources 实现。
5. reconcile 唤醒使用原 queueing_lock 语义，历史无锁纯唤醒在核对原 context/载荷/代际后走 Runtime.adopt_wake_on；不直接删旧 job，不强杀 doing，不用新业务身份刷新预算。
6. 必要用例代码覆盖观察/通知并发最多 apply 一次、旧预算耗尽、pending/running/404 与期限能力、头部推进交接、取消/换 Action/换代、Goal/Memory/proof 故障回滚。编写不运行。

## 独占可写文件

- `src/tianshu/life/worker.py`
- `src/tianshu/life/reconciliation.py`
- `src/tianshu/life/rbi_result_fence.py`
- `src/tianshu/life/rbi_activity_reconciliation.py`
- `src/tianshu/life/rbg_result_transaction.py`
- `src/tianshu/life/rbj_*.py`
- `src/tianshu/life/rbj_*.sql`
- `tests/life/test_rbj_*.py`
- `tests/life/test_rbi_result_atomicity.py`
- `tests/life/test_rbi_activity_reconciliation.py`
- `docs/handoffs/RBJ-05.md`

白名单是职责范围，不要求改动每个文件。不跨写其他窗口；新增非白名单文件、公共签名调整或事件 schema 需求报根，由根调整合同，不能自行平行实现。所有 module.py、runtime/product、全局 types、已有 schema.sql/旧迁移、注册、配置/依赖和共享记录归根。

## 验证与交接

19:07 原 activity_step/result_head_advanced 是明确回归案例；不得通过删除 head fence 修复。编写端口夹具，根集成后验证真实 Sources/Action/消费者事务。

只编写必要用例与静态审阅完整 diff 后提交，**不运行测试、构建、烟测、生产、真实模型或 QQ 命令**。根收齐集中执行必要检查；不设置自动化、不推送/部署。保留原 ID/请求/回执/unknown、来源/权限/版本/CAS/代际和总预算，不清数据或重放外部未知效果；403/429、备用路由不改。

交付 `docs/handoffs/RBJ-05.md`，包含精确提交、实际改动/静态审阅、公共接口/新 SQL 和根装配需求、用例代码及明确未运行、未完成/风险。整块提交后向唯一协调 chat `01a1ac6f-8494-7f82-8c4d-ec7d285f44ae` 回执（用户已明确授权本批既有窗口派发及回执），说明可复用检查与未覆盖；收到必要返修继续本卡，不主动扩大范围。
