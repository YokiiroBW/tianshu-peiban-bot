# RBJ-07：现有索引恢复 backfill 的独立有界覆盖疑点

状态：第二批已回执排队，根释放名额前不写产品/用例。这是新卡，不续写旧 RBI/RBH/RBG 已完成任务。

- 既有窗口：RBW Memory写入历史与共享整理，thread `01a1a772-e983-7ad0-b572-61409937e393`，host local。
- 唯一工作树：`C:\Users\Administrator\.codex\worktrees\rbj-memory\tianshu-peiban-bot`；分支 `codex/rbj-memory-20261106`。不要写旧 cwd 或协调检出。
- 代码起点 `39da2d283deb17de2739b3c24fc1eeedc96cfa69`；实际准备提交由根正式派发消息及 dispatch 记录给出，必须核对一致。
- gpt-6.1-terra / max，普通速度（service_tier=default、fast_mode=false），不自行开启 Fast。
- 依赖：无跨窗口实现前置。依赖窗口使用冻结合同与夹具，不读写其可变目录；接口不明确即报根并继续自身独立工作。
- 先读本卡、[完整方案](../source-result-repair-plan-2026-11-06.md)、[公共合同 1.3](../rbj-interfaces-2026-11-06.md)；CURRENT/AGENTS 顶部 RBJ 条款覆盖旧阶段。

## 完整交付范围

1. 排队卡，须待根明确释放实现名额。允许先读卡和确认理解，不启动产品/用例写入。
2. 已有 record_attempt_on/backfill，不新建统计体系。复核 index_jobs.recover 后部 backfill 被前方重扫描异常阻断的路径，结合现有 producer 水位和投影合同定义可证明覆盖，partial/unknown 保持真实。
3. 若成立，复用原有有界 backfill，接入 Memory 自身轻维护并独立隔离失败；只修改自身领域能力，module/process 装配交根。不要将两条对象向量正常或进程 ready 等同全覆盖。
4. 索引来源撤回/权限/版本/embedding profile 保持；不重新嵌入全库、不改配置、不改上游 HTTP。不在生产跑探针或数据库命令。
5. 编写扫描中断仍能有界补齐、重复维护幂等、水位/代际准确及失败隔离的必要用例代码，不运行。证据不足时明确待根执行，不虚构现场覆盖。

## 独占可写文件

- `src/tianshu/memory/workers.py`
- `src/tianshu/memory/index_recovery.py`
- `src/tianshu/memory/index_jobs.py`
- `src/tianshu/memory/rbi_maintenance.py`
- `src/tianshu/memory/rbj_*.py`
- `src/tianshu/memory/rbj_*.sql`
- `tests/memory/test_rbj_*.py`
- `docs/handoffs/RBJ-07.md`

白名单是职责范围，不要求改动每个文件。不跨写其他窗口；新增非白名单文件、公共签名调整或事件 schema 需求报根，由根调整合同，不能自行平行实现。所有 module.py、runtime/product、全局 types、已有 schema.sql/旧迁移、注册、配置/依赖和共享记录归根。

## 验证与交接

不阻塞核心批；根用实际投影与 producer 水位验证，不额外建立统计/监控并行系统。

只编写必要用例与静态审阅完整 diff 后提交，**不运行测试、构建、烟测、生产、真实模型或 QQ 命令**。根收齐集中执行必要检查；不设置自动化、不推送/部署。保留原 ID/请求/回执/unknown、来源/权限/版本/CAS/代际和总预算，不清数据或重放外部未知效果；403/429、备用路由不改。

交付 `docs/handoffs/RBJ-07.md`，包含精确提交、实际改动/静态审阅、公共接口/新 SQL 和根装配需求、用例代码及明确未运行、未完成/风险。整块提交后向唯一协调 chat `01a1ac6f-8494-7f82-8c4d-ec7d285f44ae` 回执（用户已明确授权本批既有窗口派发及回执），说明可复用检查与未覆盖；收到必要返修继续本卡，不主动扩大范围。
