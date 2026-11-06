# RBJ-04：原结果消费者收敛、准确预算诊断和定向本地对账

状态：第一批，已派发并确认开始。这是新卡，不续写旧 RBI/RBH/RBG 已完成任务。

- 既有窗口：RBW 对话接入与上下文表达，thread `01a1a772-e842-7d80-864f-3edb1c0443ae`，host local。
- 唯一工作树：`C:\Users\Administrator\.codex\worktrees\rbj-conversation\tianshu-peiban-bot`；分支 `codex/rbj-conversation-20261106`。不要写旧 cwd 或协调检出。
- 代码起点 `39da2d283deb17de2739b3c24fc1eeedc96cfa69`；实际准备提交由根正式派发消息及 dispatch 记录给出，必须核对一致。
- gpt-6.1-terra / max，普通速度（service_tier=default、fast_mode=false），不自行开启 Fast。
- 依赖：RBJ-02, RBJ-03。依赖窗口使用冻结合同与夹具，不读写其可变目录；接口不明确即报根并继续自身独立工作。
- 先读本卡、[完整方案](../source-result-repair-plan-2026-11-06.md)、[公共合同 1.2](../rbj-interfaces-2026-11-06.md)；CURRENT/AGENTS 顶部 RBJ 条款覆盖旧阶段。

## 完整交付范围

1. 拥有 ActionResultEvents.ensure_on；publish 保留兼容并委托此口。公开补唤醒与原通知共用原事件/身份/消费者账本，准确表达真正 blocked/waiting/accepted/verified，不能只看到 pending job 就忽略执行资格。
2. 扩展 ResultRecovery 与 ResultConsumer 的 repair_ref/repair_consumer。先读原业务证据/proof，已提交则对账，未提交且可信资格有效才执行同一原事件的目标原子本地领域回调。首次只支持 life_activity 与已有等价原子合同的 life_commitment；绝不在 repair 分支运行 renderer/dialogue/模型/QQ/外部 Action。
3. 保留 execution_identity(generation,action,revision,receipt)，不加 consumer 生成新普通身份绕预算。接 Runtime 的可信登记、失败历史摘要 CAS 与 begin/finish purpose，普通耗尽保持耗尽，一次目标修复资格幂等。消费者参数不能自授资格；candidates/inspect/invoke/observe/verify 统一表达普通资格、repair 资格和 unknown。
4. Life/Goal/Memory/consumer proof 同事务完成；未知提交先读原 proof 和相关业务引用，不凭 activity.completed 推断完整成功。新 head/撤回/换代/取消按真实 superseded/invalidated 等收口，不重放旧结果。保留已有 head fence 和同版本冲突拒绝。
5. 复用 Runtime 的 wake 合同；对旧无锁 result jobs 仅在核对完整信封/代际后 adopt。不直接写 Runtime 内部事实，不碰 Actions 文件。
6. 添加四次失败耗尽、一次修复资格重入、repair 无外部调用、proof 丢应答对账、来源/Goal/Memory/proof 任一点回滚、head 推进与旧任务覆盖的必要用例代码。编写不运行。

## 独占可写文件

- `src/tianshu/conversation/results.py`
- `src/tianshu/conversation/rbi_result_wake.py`
- `src/tianshu/conversation/rbg_result_recovery.py`
- `src/tianshu/conversation/rbg_result_ledger.py`
- `src/tianshu/conversation/rbj_*.py`
- `src/tianshu/conversation/rbj_*.sql`
- `tests/conversation/test_rbj_*.py`
- `tests/conversation/rbj_*.py`
- `tests/conversation/test_rbi_result_consumption.py`
- `docs/handoffs/RBJ-04.md`

白名单是职责范围，不要求改动每个文件。不跨写其他窗口；新增非白名单文件、公共签名调整或事件 schema 需求报根，由根调整合同，不能自行平行实现。所有 module.py、runtime/product、全局 types、已有 schema.sql/旧迁移、注册、配置/依赖和共享记录归根。

## 验证与交接

依赖用冻结 API 与必要夹具实现，不从其他窗口可变目录 import。报告新的队列 payload 参数供 root 装配；真实 PG 跨域测试由根集中执行。

只编写必要用例与静态审阅完整 diff 后提交，**不运行测试、构建、烟测、生产、真实模型或 QQ 命令**。根收齐集中执行必要检查；不设置自动化、不推送/部署。保留原 ID/请求/回执/unknown、来源/权限/版本/CAS/代际和总预算，不清数据或重放外部未知效果；403/429、备用路由不改。

交付 `docs/handoffs/RBJ-04.md`，包含精确提交、实际改动/静态审阅、公共接口/新 SQL 和根装配需求、用例代码及明确未运行、未完成/风险。整块提交后向唯一协调 chat `01a1ac6f-8494-7f82-8c4d-ec7d285f44ae` 回执（用户已明确授权本批既有窗口派发及回执），说明可复用检查与未覆盖；收到必要返修继续本卡，不主动扩大范围。
