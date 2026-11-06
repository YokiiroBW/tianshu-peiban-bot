# RBJ-03：原 Action 终态结果的公开补唤醒端口与 head fence

状态：第一批，已派发并确认开始。这是新卡，不续写旧 RBI/RBH/RBG 已完成任务。

- 既有窗口：RBW 独立能力与数据边界，thread `01a1a772-e975-77c3-a3e9-849d1b71eb20`，host local。
- 唯一工作树：`C:\Users\Administrator\.codex\worktrees\rbj-capabilities\tianshu-peiban-bot`；分支 `codex/rbj-capabilities-20261106`。不要写旧 cwd 或协调检出。
- 代码起点 `39da2d283deb17de2739b3c24fc1eeedc96cfa69`；实际准备提交由根正式派发消息及 dispatch 记录给出，必须核对一致。
- gpt-6.1-terra / max，普通速度（service_tier=default、fast_mode=false），不自行开启 Fast。
- 依赖：RBJ-04。依赖窗口使用冻结合同与夹具，不读写其可变目录；接口不明确即报根并继续自身独立工作。
- 先读本卡、[完整方案](../source-result-repair-plan-2026-11-06.md)、[公共合同 1.1](../rbj-interfaces-2026-11-06.md)；CURRENT/AGENTS 顶部 RBJ 条款覆盖旧阶段。

## 完整交付范围

1. 在 Actions 增加 ensure_result_consumption 公共窄口，复用现有持久化 head、来源权限验证、assert_result_head_on 和 self.events。该端口只交接已有回执，绝不能 invoke/resume/重新提交外部操作。
2. terminal 才构造既有原事件信封；pending/running 返回准确 waiting。核对 context/actor/scope/generation；调用者快照已落后时以同一 Action 的新权威 head 交接，同版本冲突/头回退必须拒绝。
3. Sources→Action 的已有锁序及 event publish 在同事务，保证被交接的 receipt/revision 与头部一致。跨事务读取后 fence 期间再次推进时有界重读/交接，不无界循环，不吞 403/404/409。
4. self.events.ensure_on 返回真实 blocked/waiting/accepted/verified，不假报受理。旧 publish 调用兼容，现有 save()/失败/取消/unknown 结果通知保留。
5. 本批 actions.py 唯一写者为你；RBI 旧卡的 Conversation 归属不适用于本批。必要用例代码覆盖 head 推进、同版本冲突、generation/权限/来源变化、pending 与 unknown、重复补唤醒、不新增模型/外部调用。编写不运行。

## 独占可写文件

- `src/tianshu/capabilities/actions.py`
- `src/tianshu/capabilities/rbj_*.py`
- `tests/capabilities/test_rbj_*.py`
- `docs/handoffs/RBJ-03.md`

白名单是职责范围，不要求改动每个文件。不跨写其他窗口；新增非白名单文件、公共签名调整或事件 schema 需求报根，由根调整合同，不能自行平行实现。所有 module.py、runtime/product、全局 types、已有 schema.sql/旧迁移、注册、配置/依赖和共享记录归根。

## 验证与交接

按冻结的 ActionResultEvents.ensure_on 编写，不等待 Conversation 合并。根负责真事件生产/消费联合检查。

只编写必要用例与静态审阅完整 diff 后提交，**不运行测试、构建、烟测、生产、真实模型或 QQ 命令**。根收齐集中执行必要检查；不设置自动化、不推送/部署。保留原 ID/请求/回执/unknown、来源/权限/版本/CAS/代际和总预算，不清数据或重放外部未知效果；403/429、备用路由不改。

交付 `docs/handoffs/RBJ-03.md`，包含精确提交、实际改动/静态审阅、公共接口/新 SQL 和根装配需求、用例代码及明确未运行、未完成/风险。整块提交后向唯一协调 chat `01a1ac6f-8494-7f82-8c4d-ec7d285f44ae` 回执（用户已明确授权本批既有窗口派发及回执），说明可复用检查与未覆盖；收到必要返修继续本卡，不主动扩大范围。
