# RBJ-05 Life：结果观察、原业务证据与唤醒

- 基线：`ec4b26a652d53d3dc40ab42a9f61734ff12e320d`；接口依据不可变合同 `bf408fa5af0daabace0059dd501d22166dd02bcd`（1.3）。本卡仅含 Life 实现与定向用例，尚未集成或部署。
- Life 观察到 terminal Action 头时，以原 generation、Action、receipt、result revision 调用 `Actions.ensure_result_consumption`，原样传播其 accepted/waiting/blocked/verified 结果。pending/running 仍使用原有来源及 Action-head fence 更新控制状态。真实 head 推进时旧事务退出后重读同一 Action；严格递增的 terminal 头交给共同消费者，持续递增的控制头留下原对象后继。没有重调原外部操作。
- 既有结果消费者继续在同一事务提交 Life、Goal、Memory 与 consumer proof。`LifeWorker.inspect_result_repair_on(...)` 在调用方完成 Sources/Action fence 后检查原 owner；owner 未提供时只接受同 actor/Action 的唯一行。已绑定 owner 的 repair 使用新增的 `expected_owner_id` 重新锁定同一行，不能切换到新 owner。同回执已落账但没有完整事务证明、相同版本不同回执、回退或 owner 缺失保持 unknown；明确取消/换绑为 superseded。实际 callback 遇到同回执但缺完整证明时抛 `local_result_application_unconfirmed`，交由 Conversation 作为执行 unknown 对账。
- 原失败的 retry 或期限续接必须读取当前原事件的 `consumer_progress.life_activity`，核对 generation、Action、receipt、revision、done 和完整 owner proof；随后仍在 Action/Sources/活动行的事务及 CAS 下决定。期限到达后的新 skip 是独立 Life 决定，不重新消费旧回执；持久期限判断使用数据库时钟。
- 活动唤醒复用 Runtime `adopt_wake_on`/`cover_wake_on`。Life 核对 native task、generation、actor、完整 payload、原 Action、冻结上下文及活动行；旧 sequence 键需精确包含原 Action 和 sequence，旧启动恢复键需精确匹配活动行 revision。不同 payload 不用同一 adopt 身份。其他正在执行的旧任务按 Runtime 的 waiting 结果处理；当前 job 确认 covered 时原样返回完整覆盖结构，供 Runtime 留存该 ordinal 的机械退出证据。资源等待及原恢复 due 保留下界，历史候选使用持久游标分批轮转。无法证明身份或旧启动 revision 已变化的历史 job 不被猜测覆盖；当前 job 返回 blocked，活动维护继续按当前权威行安排标准唤醒。
- 承诺的 Action 404 可能与 admission 竞态：保留原 frozen Action，标记 unknown 并安排同一承诺的观察，不再次 invoke。承诺 pending、unknown 和无回执也保留有界间隔的原对象后继；版本参与队列锁，避免不同载荷合并。

## 集成要求

1. 先集成 RBJ-02 的 wake 注册、adopt/cover、`covered_by` 与 covered native ordinal 退出证据，再集成 RBJ-03 Actions 窄口及 RBJ-04 Conversation 事件/进度/repair 接线；本卡没有新 SQL 或共享注册修改。`wake_key` 传未附 generation 后缀的原始逻辑键，Runtime 负责归一化。
2. Conversation repair 在原事件来源和 Action-head fence 后调用 `inspect_result_repair_on`，持久绑定返回的 `owner_id`，actual callback 传入该 `expected_owner_id`；`local_result_application_unconfirmed` 属于 unknown，不能当作已确认失败或自动免费重放。根协调者负责构造/注册 `ResultRecovery` 的 Life worker 引用及公共迁移。
3. 旧 Life 测试夹具的 Action snapshot 必须包含真实 Action ID，假 Actions 需要接入共同 `ensure_result_consumption`；旧断言中把 `capability_result` 的 terminal 观察当作同步业务落库，或把历史同回执/单个 episode 当作完整证明，需要按新合同更新。`test_rbi_result_atomicity.py` 的本卡相关断言已调整；其他跨域共享夹具与用例由根统一适配。
4. 有些历史启动任务在活动 revision 已改变或缺少原 frozen Action/context 时不能证明覆盖，保留 unknown/blocked，不能凭 payload 补造 Action 归属。实际生产历史分布、覆盖收敛和运行延迟均待协调者检查。

## 本窗口验证状态

已编写 terminal 交接、head 推进/回退/冲突、持续推进后继、proof 事务回滚、原 owner repair、历史唤醒身份与覆盖、承诺 404 等定向用例。本窗口只进行 Python AST 静态解析、完整 diff 静态审阅和 `git diff --check`；按本批约定未运行测试、构建、PostgreSQL、真实模型、QQ 或生产操作。集中集成和必要的真实数据库联合验证由协调者执行。

## 集中验收后的定向夹具返修

根协调者回报：RBJ-05 新增的四个测试文件共 24 例在集中真实 PostgreSQL 检查中通过；另有相关旧用例 6 例失败，本次仅调整其测试归属。`source_life` 通过 `source_module(...).migrations` 安装当前权威 Sources 迁移，覆盖使用同一夹具的 Goal fence 测试；没有复制新版 SQL 清单或修改产品装配。

结果消费者的两个旧场景改用 native started→failed 和真实 attempts 计数构造完整历史，分别核对未确认远端回滚返回 `blocked/result_consumer_blocked`、活跃消费者返回 `waiting/consumer_running` 且 due 等于原 lease。测试继续核对原事件、消费者证据、失败事实、无额外唤醒和 Operations 不误报。此返修未在本窗口运行测试或 PostgreSQL；六例及其与根当前集成状态的装配仍由协调者定向复验，前述 24 例复用根已取得的结果。
