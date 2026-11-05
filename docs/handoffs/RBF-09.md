# RBF-09 前台对话公共恢复合同接线

基线 `33e5282f60fe4c803c3b16d498d1d924621ed1b9`；本卡独立分支 `codex/rbf-conversation-20261105`。仅修改 Conversation、直接相关测试和本交接，未修改 RBF-05 独占的 `conversation/persistence.py`。

## 实现与接口

- 输入事件在调用 Sources 写入前准备完整事件及附加依赖，观察输入时先锁来源再写联系人和 Memory 投影。Sources 的 `RecoveryWait` 和有明确证据的数据库资源错误贯穿受影响的对话、上下文、群参与、模型结果和表达异常边界；具体能否自动重排由 `recovery_wait_retryable` 和数据库分类共同决定。
- `conversation.run` 在认领 turn、生成 attempt 的同一事务内创建原身份的 `conversation.prepare` 作业；恢复准备沿用 turn/attempt/代际，启动恢复对未冻结模型请求的历史 running turn 使用同一作业键。Root 需在共享 Runtime `TASK_LANES` 将其归到 CONVERSATION，并加入 `DYNAMIC_TASK_LANES`，供 producer-only 路由及根据 turn 区分人工/自主 lane。
- `GenerationRequests.claim/confirm` 为群参与和前台提交使用：本地 claimed 与已确认登记分开，尚未确认时可用原完整冻结请求、原 attempt 和原 completion 身份向 Models 做幂等准入；已确认后只观察，缺失回执不倒推未发送。来源、原授权、业务期限、代际和领域 guard 均保留。Growth 和 Life 的直接 submit 路径不调用该 claim/run，继续使用各自领域的确认状态；它们对 `prepare/read` 的现有用法仍保留。
- 前台成功结果通过 Models `reusable_result` 核对原请求 `submission_digest`、代际及当前授权后，再进行来源与 turn CAS；资源等待若确认可恢复则消费同一结果，绝不生成新的模型 attempt。回滚不确定时保留未确认状态，仅按原 ID 查询回执，不自动重启动作或表达。
- 工具恢复先查询原 Action 账本，再在明确无登记时注册同一 action ID；表达恢复区分发送前的可恢复预检与发送后结果不确定，并沿用原 expression/receipt。渲染结果已提交而后续本地处理回滚时可继续处理同一回执。

## 迁移和集成

Conversation module 注册 `0041_conversation_submission_confirmation`，文件 `src/tianshu/conversation/submission_confirmation.sql`。它新增 `generation_requests.submission_confirmed/recovery_unconfirmed`、`context_selections.consumption_state` 和 `expression_plans.continuation_unconfirmed`，仅依据已存在的 Models attempt 回填登记证据；已完成的消费标为 done，升级时已有结果且仍 running 的未完成消费保守标为 unconfirmed。旧 schema 和共享迁移未改写。确认值单调从 false 到 true；false 不是未发出证据。内部状态不是面向用户的错误码。

依赖 RBF-05 Sources `prepare_event_write` 与锁集变化等待、RBF-06 Models `status(phase=...)`/`require_receipt`/`reusable_result`，以及 RBF-01/共享 RPC 的 `RecoveryWait`、`recovery_wait_retryable` 和数据库失败分类。Root 负责这些提交、Runtime lane 与迁移顺序的统一集成；Models 是唯一执行账本。若确认标记已存在但原 Models 回执缺失，保持未解决并只观察，不重建 attempt 或清除记录。

## 验证交接

遵照分工，本卡未运行测试、构建、烟测、生产、真实模型或 QQ。已静态解析改动 Python 文件并检查 diff 空白。新增 `tests/conversation/test_rbf_recovery.py` 覆盖同一输入/turn 的锁集等待、claimed-before-RPC、丢 ACK、并发与迟到同 ID 的本地模拟供应商单次外发、原结果本地提交恢复、远端回滚否定证据、明确取消/来源撤回、表达发送前后等待和渲染回执恢复；同时调整直接相关既有测试的模拟接口与迁移。

集中验证需显式配置隔离 PostgreSQL+pgvector 的 `TIANSHU_TEST_POSTGRES_ADMIN_DSN`，集成完整 Sources/Runtime/Models 后可执行：

```text
python -m pytest tests/conversation/test_rbf_recovery.py tests/conversation/test_generation_recovery.py tests/conversation/test_group_participation.py tests/conversation/test_rb_model_handoff.py tests/conversation/test_expression_order.py
```

还需由主协调者验证 Runtime 新任务路由、迁移从现有数据升级、Growth/Life 使用 `GenerationRequests.prepare/read` 的集成，以及 Channels 原 Action/QQ 结果对账；模拟供应商和静态检查不代表真实模型或业务验收。
