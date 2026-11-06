# RBJ-06：Growth 同材料并发准备的权威批次

## 交付与边界

- 独立 worktree：`C:\Users\Administrator\.codex\worktrees\rbj-growth\tianshu-peiban-bot`；分支：`codex/rbj-growth-20261106`。
- 实际派发基线：`ec4b26a652d53d3dc40ab42a9f61734ff12e320d`；代码与用例提交：`2371ed32660e1133a4c88e88dab1a2127730731d`。本文件另由后续提交交付。
- 跨域接口依据不可变合同 1.5：`a5e6155c1d6ad5a680e4b2f4a46aa7abf98adcd1` 中 `docs/development/rbj-interfaces-2026-11-06.md` 第 10 节。

## 静态路径与修复

静态追踪确认可达交错：两个原生 `organize` 均先读到空 `active_batch`，独立得到同一材料键和不带谱系的候选批次；A 在 `GenerationRequests.prepare` 的 guard 内分配原 Runtime lineage/attempt 并与冻结请求同事务提交，B 随后进入相同材料的 prepare。原 guard 仅比较材料键并返回 true，B 后续成功或失败记账仍可能使用自己的无谱系候选。历史 `historical_only` 已完成事实不代表这是当前活跃卡死，也不能据静态追踪称已运行复现。

`growth.py` 在原 guard 的 Growth 状态行锁内，检查当前批次、代际、材料、输入、选择版本及阻断状态；命中时重载权威 `active_batch` 和 state。通过既有 `GenerationRequests.request` 的 1.5 同事务接口读取原 attempt、context 和 material 的冻结请求，核对请求身份、代际与反思材料闭包，再沿用原 lineage、recovery_version、请求和原结果记账。初始无批次的竞态执行者还重检输入行版本；当前选择或版本变化时退出。原批缺谱系/版本、冻结请求缺失或身份/上下文/材料冲突时明确停止，避免落入宽泛 RPC 兜底继续准备候选请求。没有新增 reserve、换批、刷新预算或改写原回执；已有来源授权、CAS、unknown 和 Runtime 处理仍由原路径负责。

## 测试代码与静态检查

`tests/companion/test_rbj_growth_authority.py` 编写了两套实际 GrowthService、真实 GenerationRequests 和 Runtime/Models 持久账本的受控交错用例；只在 prepare 外侧设置事件屏障，无真实 provider 调用。参数化覆盖成功、失败、unknown，以及材料版本变化、缺谱系/恢复版本、缺冻结请求、冻结请求代际或上下文冲突。断言原冻结请求、attempt、谱系和总预算未被替换，必要时原 unknown 继续作为 unknown。缺失/冲突场景只在隔离测试数据库中构造损坏事实。

实际执行：完整代码 diff 静态审阅；`git diff --cached --check` 通过；使用 Python `ast.parse` 对改动的两个 Python 文件做静态语法解析通过。按派发要求，**未运行任何测试、构建、烟测、PostgreSQL、真实模型、QQ 或生产操作**；测试断言及上述交错运行结果均待根集中验证。

## 装配与限制

- 必须先装配 RBJ-04 的 `GenerationRequests.request(self, attempt_id, context, *, connection=None, material_key=None)` 合同实现，再集中运行 Growth 与 Conversation 的相关 PostgreSQL 用例。本分支保留原始基线，不包含对方实现；直接运行本分支的上述新代码会因旧接口不接受这两个关键字参数而失败。
- 接口在传入 connection 时须复用该事务、不重取 Sources 锁、无插入/prepare/claim/submit，保留上下文及 invalidated 检查和 expired 原观察语义；原 attempt 的 material 不匹配须报 `generation_identity_conflict`。Growth 不直接访问 Conversation 私表；用例中的数据库直读/受控修改仅用于隔离夹具和断言。
- 无新增 SQL、迁移、注册、配置或其他公共装配；根负责按合同核对接口、集中验证与后续集成。未推送、未部署，尚无运行验收或真实业务效果结论。

## 集中验证反馈及夹具返修

根回报在集成提交 `a3c4ea31` 的隔离 PostgreSQL 集中验证中，本卡新增的九个参数化场景均先在 `concurrent_preparation` 的调用次数断言处失败，尚未进入各自的业务断言；这个结果不能判定产品修复已经通过或失败。原因是 `GenerationRequests.prepare` 在未传 connection 时打开事务，再通过 `self.prepare(..., connection=transaction)` 重入；原测试包装器将 A 的事务内重入误计为第三个 Growth 执行者。

返修提交 `1076a021c18a132c392ba39be617f23b34ca2c60` 只修改测试夹具：带 connection 的重入直接委托真实方法；只统计两个外层执行者，严格断言分别为 first/second、材料键及 attempt ID 相同。两者均到达外层 prepare 后、A 获准冻结前，增加明确的持久 `active_batch is None` 断言；再放行 A 完成原批冻结并提交到 Models 账本，B 仍由各场景控制释放。原请求、谱系、unknown、预算和具体异常断言未放宽。

本窗口静态审阅返修 diff、`git diff --cached --check` 和测试文件 AST 解析通过；按派发要求未运行测试或 PostgreSQL。请根在现有集成基线上装配上述返修提交，复验受影响九例，并据实际结果判断是否存在后续产品或夹具问题。根的私有运行日志不纳入本提交。
