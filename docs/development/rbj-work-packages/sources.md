# RBJ-01：来源图有界求值、深度断崖及同调用准备去重

状态：第一批，已派发并确认开始。这是新卡，不续写旧 RBI/RBH/RBG 已完成任务。

- 既有窗口：RBW 身份来源与失效传播，thread `01a1a772-e813-76e3-9869-be43c2a4834c`，host local。
- 唯一工作树：`C:\Users\Administrator\.codex\worktrees\rbj-sources\tianshu-peiban-bot`；分支 `codex/rbj-sources-20261106`。不要写旧 cwd 或协调检出。
- 代码起点 `39da2d283deb17de2739b3c24fc1eeedc96cfa69`；实际准备提交由根正式派发消息及 dispatch 记录给出，必须核对一致。
- gpt-6.1-terra / max，普通速度（service_tier=default、fast_mode=false），不自行开启 Fast。
- 依赖：无跨窗口实现前置。依赖窗口使用冻结合同与夹具，不读写其可变目录；接口不明确即报根并继续自身独立工作。
- 先读本卡、[完整方案](../source-result-repair-plan-2026-11-06.md)、[公共合同 1.4](../rbj-interfaces-2026-11-06.md)；CURRENT/AGENTS 顶部 RBJ 条款覆盖旧阶段。

## 完整交付范围

1. 阅读 Sources 的快速路径、旧递归及全部公共调用链。以迭代共享 DAG 求值消除技术深度 64 的性能断崖；保留权限、范围/片段、disclosure、撤回、AND/OR 与循环语义。不能仅扩大深度或关闭来源证明。
2. 取图、子批、求值共用明确节点/边/工作量预算；预算耗尽用独立错误表达，不能当作来源 false，也不能无界退回旧递归。循环中有合法 OR 出口的分支仍能成立，不能将整张有环图简单拒绝。
3. 复用现有同调用缓存/JIT 抑制；合并同 context、精确引用/locator 相同的准备。跨调用事务证明复用仅在完整权威读集保护到提交可证明时采用；否则保留新鲜校验，不引入权限缓存竞态。
4. 新 SQL 使用 rbj_*.sql 增量迁移；不改历史 schema.sql/已发布 SQL。列出 root 的注册顺序及函数覆盖关系。性能主修不依赖旧数据迁移或来源压缩；生产者的祖先边收敛只在能证明语义等价时提出精确后续，不越界改 Life。
5. 添加无正文现场拓扑夹具的必要用例代码：195 节点/8252 边规模、64→65→更深、新回执同事务可见、范围/权限 epoch/撤回、替代组短路、有/无出口循环、缺来源和预算。覆盖公共读取及 Memory 撤回/检索的关联调用行为。编写而不运行。
6. 1.4 补充 archives.lock_selected 的锁闭包：selected/writable/alias_keys 的 supports/alias 扩展须有界，并和本次公共调用的锁后新鲜复核、证明求值共享预算；不得先完整展开再 LIMIT。保留有序共享/独占锁和集合变化回滚，不截断锁集继续提交。添加深图、高扇出 alias、预算耗尽、锁等待期间集合变化，以及受影响归档/撤回/更正的必要用例代码。实际运行仍由根集中执行。

## 独占可写文件

- `src/tianshu/sources/proofs.py`
- `src/tianshu/sources/service.py`
- `src/tianshu/sources/ports.py`
- `src/tianshu/sources/archives.py`
- `src/tianshu/sources/rbj_*.py`
- `src/tianshu/sources/rbj_*.sql`
- `tests/sources/test_rbj_*.py`
- `tests/sources/test_rbf_proof_reuse.py`
- `tests/sources/test_rbi_scoped_preparation.py`
- `tests/sources/test_rb_proof_batches.py`
- `tests/sources/test_rbf_lock_order.py`
- `tests/sources/test_rb_authority.py`
- `docs/handoffs/RBJ-01.md`

白名单是职责范围，不要求改动每个文件。不跨写其他窗口；新增非白名单文件、公共签名调整或事件 schema 需求报根，由根调整合同，不能自行平行实现。所有 module.py、runtime/product、全局 types、已有 schema.sql/旧迁移、注册、配置/依赖和共享记录归根。

## 验证与交接

集中验证使用真实 PG 与真实 Sources；亚秒级是待验证目标，需分清取图、求值和持锁/等待时间。不得用 stub 通过代替来源语义与性能验收。

只编写必要用例与静态审阅完整 diff 后提交，**不运行测试、构建、烟测、生产、真实模型或 QQ 命令**。根收齐集中执行必要检查；不设置自动化、不推送/部署。保留原 ID/请求/回执/unknown、来源/权限/版本/CAS/代际和总预算，不清数据或重放外部未知效果；403/429、备用路由不改。

交付 `docs/handoffs/RBJ-01.md`，包含精确提交、实际改动/静态审阅、公共接口/新 SQL 和根装配需求、用例代码及明确未运行、未完成/风险。整块提交后向唯一协调 chat `01a1ac6f-8494-7f82-8c4d-ec7d285f44ae` 回执（用户已明确授权本批既有窗口派发及回执），说明可复用检查与未覆盖；收到必要返修继续本卡，不主动扩大范围。
