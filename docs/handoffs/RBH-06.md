# RBH-06：结构化记忆投影接入

- 基线 `ec245f9e30f935f7bcadbc441373a45c9f9d07a4`，分支 `codex/rbh-conversation-20261105`；准确交付 SHA 以本提交与回执为准。
- `ContextAssembler` 的个人和群投影调用移除真实 `MemoryService.projection` 不支持的 `reflection_limit`，保留原 `claim_limit`、`matter_limit`、关系背景、完整 evidence unit、当前时间语义和覆盖缺项处理。无 `person_id` 时仍按真实接口执行授权范围内的非指定主体投影，且不查询个人关系；群投影继续单独读取并按原记录去重。
- 不重新把历史反思当当前行为：沿用 Conversation 原有来自 Companion 的 `learned_habit`、来源检查和按规则版本的最终复检。没有改变 Memory/Growth 实现、人格或检索策略。
- 新增 `tests/core/test_rbh_context_projection.py`，通过隔离 PG 上的真实 `MemoryService.projection`、真实 Sources/Identity 和真实 Memory 检索最终复检联合 `ContextAssembler`；仅把可选搜索设为空以隔离投影入口，不替换投影方法。覆盖个人和群记录、无人物群上下文、关系、当前/过期有效时间、来源撤回后个人与群 coverage 缺项、现行习惯的获取与撤销后旧规则最终复检，并确认历史反思不因结构化投影成为当前规则。该测试使用现有显式一次性 PG/pgvector 夹具和隔离 Companion 账本。
- 无新增迁移、公共接口、根装配或生产数据修正；按正常调用读取最新投影即可。未改共享 `module.py`、已有 schema 或其他工作树。
- 按批次约定，本窗口未运行测试、构建、烟测、真实模型/QQ或生产命令。已静态审阅代码和测试，Python AST 语法解析通过；根需集中运行上述定向用例及与结构化上下文相关的必要用例，结果不能预先当作通过。
