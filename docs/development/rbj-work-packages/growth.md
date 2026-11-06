# RBJ-06：同 material 并发准备时权威 batch 谱系重载疑点

状态：第二批已回执排队，根释放名额前不写产品/用例。这是新卡，不续写旧 RBI/RBH/RBG 已完成任务。

- 既有窗口：RBW Companion学习与表达样例，thread `01a1a772-e96f-7600-a841-94ca8dd26ab6`，host local。
- 唯一工作树：`C:\Users\Administrator\.codex\worktrees\rbj-growth\tianshu-peiban-bot`；分支 `codex/rbj-growth-20261106`。不要写旧 cwd 或协调检出。
- 代码起点 `39da2d283deb17de2739b3c24fc1eeedc96cfa69`；实际准备提交由根正式派发消息及 dispatch 记录给出，必须核对一致。
- gpt-6.1-terra / max，普通速度（service_tier=default、fast_mode=false），不自行开启 Fast。
- 依赖：无跨窗口实现前置。依赖窗口使用冻结合同与夹具，不读写其可变目录；接口不明确即报根并继续自身独立工作。
- 先读本卡、[完整方案](../source-result-repair-plan-2026-11-06.md)、[公共合同 1.3](../rbj-interfaces-2026-11-06.md)；CURRENT/AGENTS 顶部 RBJ 条款覆盖旧阶段。

## 完整交付范围

1. 排队卡，须待根明确释放实现名额。允许先读卡和确认理解，不启动产品/用例写入。
2. 静态疑点：current_material 同 material_key 已有 active_batch 时只返回 true，未将持久 batch 的 lineage/recovery_version 等重载入本次局部 batch；后续成功/失败记账使用局部 batch。现场 historical_only 已完成，不将它说成当前活跃卡死。
3. 追踪两个原生执行者交错准备路径，编写准确复现用例代码。成立时在 Growth 原事务内重载并校验权威冻结批次，沿用请求/谱系继续观察；版本/当前选择变化正常退出。禁止补造谱系、重复 reserve、换批刷新预算或吞 lineage_missing。
4. 不改 HTTP/备用路由/Models。如果静态追踪排除可达性，交付准确路径证据并保持产品代码不改；如果需要执行才能确认，标记待根集中运行，不能冒称已复现。
5. 必要用例和最小领域修复整块交付，编写不运行。

## 独占可写文件

- `src/tianshu/companion/growth.py`
- `src/tianshu/companion/growth_retry.py`
- `src/tianshu/companion/rbj_*.py`
- `src/tianshu/companion/rbj_*.sql`
- `tests/companion/test_rbj_*.py`
- `docs/handoffs/RBJ-06.md`

白名单是职责范围，不要求改动每个文件。不跨写其他窗口；新增非白名单文件、公共签名调整或事件 schema 需求报根，由根调整合同，不能自行平行实现。所有 module.py、runtime/product、全局 types、已有 schema.sql/旧迁移、注册、配置/依赖和共享记录归根。

## 验证与交接

仅确认成立的当前缺陷进入实际修复；不阻塞 Sources/结果消费核心批。

只编写必要用例与静态审阅完整 diff 后提交，**不运行测试、构建、烟测、生产、真实模型或 QQ 命令**。根收齐集中执行必要检查；不设置自动化、不推送/部署。保留原 ID/请求/回执/unknown、来源/权限/版本/CAS/代际和总预算，不清数据或重放外部未知效果；403/429、备用路由不改。

交付 `docs/handoffs/RBJ-06.md`，包含精确提交、实际改动/静态审阅、公共接口/新 SQL 和根装配需求、用例代码及明确未运行、未完成/风险。整块提交后向唯一协调 chat `01a1ac6f-8494-7f82-8c4d-ec7d285f44ae` 回执（用户已明确授权本批既有窗口派发及回执），说明可复用检查与未覆盖；收到必要返修继续本卡，不主动扩大范围。
