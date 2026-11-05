# RBG-02 Conversation与Life：逐消费者结果恢复

先读../rbg-repair-2026-11-05.md和AGENTS最新覆盖。既有Conversation聊天、独立干净工作树、GPT-6.1 Terra/max普通速度；不新建聊天/子代理，不生产操作，不运行测试/构建/烟测。

白名单：src/tianshu/conversation/（generation.py/module.py/schema.sql除外）；src/tianshu/life/worker.py；新增life/rbg_result_*.py和SQL；tests/conversation/test_rbg_result*.py、test_rbg_persistence*.py、tests/life/test_rbg_result*.py及原对应定向用例；docs/handoffs/RBG-02.md。不得写Life规划、Models、Capabilities、life/recovery.py或共享装配；worker.plan保留，必须变动时先同步根。

完整实现：

1. 保留Identity→Sources→领域行锁。内部快照变化和用户expected_revision冲突区分；后台确认事务回滚后重读：已完成则幂等结束，已作废则明确退出，仍必要则有限本地重试/持久延后。不得泛化吞409或重做外部动作。
2. 扩展现有消费账本，原action/result_revision/receipt/data_generation和consumer身份明确。各分支可独立恢复；整体result_consumption仅在所有适用消费者已处理或明确不可应用终态时完成。
3. 业务写入与消费者完成证据同事务，或复用可查领域幂等回执；不能done后执行、持跨域大事务等待外部服务或靠内存锁。Life已拥有的Memory记录不再被兜底分支重复创建，所有权判定按原结果保持。
4. 重复/乱序不得回退状态，旧结果被覆盖须有明确disposition。源撤回/用户取消/代际失效保持终态；崩溃后只补缺项，不重新调用模型/发消息/重复记忆。
5. 提供既有未消费回执的正常恢复路径和只读清单。已有整体完成记录保持兼容，不能重放历史已完成副作用。consume(event)保持，任务/module/全局迁移注册给根。

现场作业18771，receipt=077504fd-9c2e-48ca-a37a-acc964705698，action=bf4f86e4-aff4-5447-a814-612b8e519f09；08:03 UTC仍未消费。活动冲突部分已有后续完成，不得批量重做失败作业。

必要用例代码：来源检查与行锁间更新；同结果并发；分支提交后另一分支失败/崩溃；新旧结果乱序；已完成活动、取消、源撤回；Memory归属；恢复不再外发。覆盖ResultConsumer→Life/Conversation真实装配，不只helper。

完整提交并交接SHA/SQL/注册点/用例及未运行项。用户已授权向主协调01a1a72e-d357-7ba2-9e01-6373ebb7741e回执。
