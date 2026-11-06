# RBJ-03 原 Action 终态结果的公开补唤醒

工作树 `C:\Users\Administrator\.codex\worktrees\rbj-capabilities\tianshu-peiban-bot`，分支 `codex/rbj-capabilities-20261106`，准备基线 `ec4b26a652d53d3dc40ab42a9f61734ff12e320d`。已阅读不可变公共合同 1.1 提交 `315fc10bef2b4a5629341b7aea84bdf4e17ca98f` 及当前 1.3 提交 `bf408fa5af0daabace0059dd501d22166dd02bcd`；1.2/1.3 的 Life owner 解析/绑定及 repair grant 关闭不改变本卡 Actions 接口。未合并或覆盖协调工作树。本文件与实现属于同一交付提交，精确 SHA 由提交回执给出。

## 实现与边界

- 新增 `Actions.ensure_result_consumption(context, action_id, *, data_generation, expected_result_revision=None, expected_receipt_id=None)`。期望版本和回执 ID 必须同时给出或同时省略；同版本 ID 冲突、头回退、跨版本复用同一回执 ID 均拒绝。更高版本的同一 Action 重新按权威 head 交接。
- 复用 `get`、原身份/来源校验、`assert_result_head_on` 和既有 Action 结果信封。在 Sources/身份→Action head 的同一事务中构造原信封并调用 `self.events.ensure_on(connection, event)`，只交接原 Action/receipt，不调用 invoke、resume、模型或外部副作用；`save` 继续调用原 `publish`，事件字段由同一构造函数生成。
- 仅捕获 `assert_result_head_on` 自身的 `result_head_advanced`，旧事务先回滚，再最多读取三次；在锁内观察到的版本及 Action/receipt 摘要只用于进程内核对后续回退和同版本变异。持续竞争保留真实 409，权限、来源、代际、head 之外的 404/409 及事件端错误原样抛出。
- pending/running 返回 `waiting`、`action_pending`/`action_running`，`receipt_id` 和 `result_revision` 为 `None`，`consumer_progress` 为空。终态透传 Conversation 的真实 `state`、`reason`、`consumer_progress` 与可选 job/due/budget 字段，再补权威 Action/receipt/revision；`verified` 不推断 Life 已应用。事件端未装配明确返回 `blocked/result_events_unavailable`；事件端返回不符合 1.3 的状态、原因或进度映射时回滚并报 `result_wake_contract_invalid`。
- expected pair 只含 revision/receipt ID，本端比较这两个身份、本次快照与锁内完整结果，并由 Conversation 核对已有原事件；不能证明调用者未提供的历史正文摘要，不增加新参数。原 unknown、attempt、预算及所有副作用身份不变。

## 依赖、用例与验证

- 无新增 SQL、配置或全局类型。RBJ-04 需将现有 `ActionResultEvents.ensure_on` 实现按合同 1.3 返回真实状态、原因与 `consumer_progress`；root 负责依赖装配及原事件消费者联合验证。当前准备基线尚未包含该端口，单独装配时只能返回明确 blocked，不应宣称已唤醒。
- 新增 `tests/capabilities/test_rbj_result_wake.py`：以真实 Action 表、`get`、来源端口栅栏和锁事务配合冻结的事件端口夹具，覆盖 pending/running 与 unknown、failed/cancelled、原事件幂等和进度透传、head 前进后事务回滚、版本/正文/来源/context 冲突、代际/权限变化、三次竞争上限、事件端原错误传播、未装配/不符合合同的端口，以及不 invoke/resume 原 Action。该夹具不替代 root 的真实 Sources、Conversation/Runtime 和 Life 联合验收。
- 按本批规定，只做完整 diff 静态审阅和 Python AST 语法解析，不运行测试、构建、烟测、PG、生产、真实模型或 QQ；未推送/部署。实际测试结果、消费 proof 与业务收敛均待根集中验证，不以本地静态检查冒称通过。
