# RBJ-02：可靠唤醒身份、按对象执行与受限本地修复资格

状态：第一批，已派发并确认开始。这是新卡，不续写旧 RBI/RBH/RBG 已完成任务。

- 既有窗口：RBW 运行时与故障隔离，thread `01a1a772-e95a-7922-a72f-1beb061dd287`，host local。
- 唯一工作树：`C:\Users\Administrator\.codex\worktrees\rbj-runtime\tianshu-peiban-bot`；分支 `codex/rbj-runtime-20261106`。不要写旧 cwd 或协调检出。
- 代码起点 `39da2d283deb17de2739b3c24fc1eeedc96cfa69`；实际准备提交由根正式派发消息及 dispatch 记录给出，必须核对一致。
- gpt-6.1-terra / max，普通速度（service_tier=default、fast_mode=false），不自行开启 Fast。
- 依赖：无跨窗口实现前置。依赖窗口使用冻结合同与夹具，不读写其可变目录；接口不明确即报根并继续自身独立工作。
- 先读本卡、[完整方案](../source-result-repair-plan-2026-11-06.md)、[公共合同 1.7](../rbj-interfaces-2026-11-06.md)；CURRENT/AGENTS 顶部 RBJ 条款覆盖旧阶段。

## 完整交付范围

1. 复用 JobQueue/job_keys/原生 Procrastinate lock 与现有 fair_claim_job，修复 retry 清 queueing_lock 后失去逻辑身份的问题。只对明确登记的 life.activity_reconcile 和 conversation.result 持久态唤醒生效，不把所有 resource_recovery 或外部命令合并。
2. 冻结逻辑键/载荷摘要/代际不随 due 和 retry 改变。相同对象至多一个有效 doing 与一个有效 pending；允许 doing 有合法后继，保留最早合法 due 及依赖等待下界。历史无锁行由领域验证后 adopt，覆盖的纯唤醒在昂贵证明前廉价退出，保留 job/attempt/失败历史。
3. 按公共合同增加可信修复登记与精确目标资格。普通结果身份和失败预算不变，repair purpose/facts 可追溯，每个同缺陷目标 consumer 最多一次真实修复失败。随机 repair_ref/SHA/job 参数不能自授资格，同修复重入幂等；保留 (generation,job_id,native_attempt) 唯一事实。
4. inspect/begin/history import/finish/resolve 联合兼容普通和 repair 事实；历史扫描不能把 repair 重新算作普通或覆盖已有 purpose。旧 failed 不转 wait；started/unknown 先对账。真实服务器 statement_timeout 不一律免费重试，仅有已确认回滚的资源等待延续原资格。
5. 新迁移 rbj_*.sql，仅机械事实/预算/唤醒能力，不判断 Life/Memory/Growth 业务终态。不能把新 Runtime 领取行锁长时间带入 Sources/Goal 领域事务。返回最少必要诊断和公开接口，不扩展依赖/配置或业务控制器。
6. 必要用例代码覆盖 retry 锁解除与保存交错、退出/后继、无锁历史、旧代际/载荷冲突、doing 不强杀、一次修复资格、普通预算仍耗尽、伪造资格、未知提交、资源等待与真实失败区分。编写不运行。
7. 1.6 完成精确 ordinal 的 repair preflight-stop、covered 历史一跳压平与追加覆盖事实、单向 redact_wake_on 的 conn-only 单实现及 JobQueue 委托。begin/exit 双向互斥，原正文不备份/恢复；原 digest/身份/未知保留，去敏与实际是否应用区分，具体字段/锁序按合同，公共事件 schema 仍归根。

## 独占可写文件

- `src/tianshu/runtime/jobs.py`
- `src/tianshu/runtime/rbi_execution_budget.py`
- `src/tianshu/runtime/database.py`
- `src/tianshu/runtime/rbj_*.py`
- `src/tianshu/runtime/rbj_*.sql`
- `tests/runtime/test_rbj_*.py`
- `tests/runtime/test_rbi_execution_budget.py`
- `tests/runtime/test_rbf_database_jobs.py`
- `docs/handoffs/RBJ-02.md`

白名单是职责范围，不要求改动每个文件。不跨写其他窗口；新增非白名单文件、公共签名调整或事件 schema 需求报根，由根调整合同，不能自行平行实现。所有 module.py、runtime/product、全局 types、已有 schema.sql/旧迁移、注册、配置/依赖和共享记录归根。

## 验证与交接

根联合 Conversation 验证真实原生任务领取、历史导入及 repair purpose。新 SQL 和必要注册交 root，不在本窗口改 product/process/module/事件 schema。

只编写必要用例与静态审阅完整 diff 后提交，**不运行测试、构建、烟测、生产、真实模型或 QQ 命令**。根收齐集中执行必要检查；不设置自动化、不推送/部署。保留原 ID/请求/回执/unknown、来源/权限/版本/CAS/代际和总预算，不清数据或重放外部未知效果；403/429、备用路由不改。

交付 `docs/handoffs/RBJ-02.md`，包含精确提交、实际改动/静态审阅、公共接口/新 SQL 和根装配需求、用例代码及明确未运行、未完成/风险。整块提交后向唯一协调 chat `01a1ac6f-8494-7f82-8c4d-ec7d285f44ae` 回执（用户已明确授权本批既有窗口派发及回执），说明可复用检查与未覆盖；收到必要返修继续本卡，不主动扩大范围。
