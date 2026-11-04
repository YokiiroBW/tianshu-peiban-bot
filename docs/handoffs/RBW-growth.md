# RBW-growth 交接

## 基线和交付

- 基线：`e2529c95a92857f368111d9ae5b23ee66800b636`。
- 分支：`codex/rbw-growth-20261105`；实现及回归用例提交：`8ea2335246fe68bd950f6fd3bd08e8f30eee316b`。本交接在其后的独立提交中，最终集成请使用包含本文件的分支 HEAD。
- 范围：Companion 自有代码及 `tests/companion/test_rb_growth_*.py`。未改共享装配、其他域、部署或生产；工作树原有 `.codex/config.toml` 改动未纳入提交。

## 已实现的功能块

- Companion `style_rules` 作为规则正文和当前状态的唯一权威，新增修订快照、独立支持事件和待辨识提案；明确修订、撤回、恢复使用版本检查。支持次数变化只增加普通 revision，正文、适用条件或状态变化增加 semantic revision。重复或相似但无法确定同一规则的提案保留为待辨识，不自动另建规则复活已撤回项。
- 运行材料先按 actor、person、conversation、scope、有效期、用途和来源过滤，再对全部合法候选排序；持续明确偏好不要求与当前话题相似。模型输出提供受限规则别名、修订动作和有效期；今日有效期基于 Life 提供的时区。完整生成依赖与模型选择的支持事件分开保存和校验。
- 反思正文落在 Companion `learning_reflections`，Memory 接口只接收索引引用和临时 `search_text`。原始回复样例通过 Conversation 的公开接口关联 turn、输入和多条输出，只保留定位和依赖，使用时重读原件、来源、规则状态及语义版本。样例选择、表达和实际送达分别计数；明确负反馈可停用样例。
- Growth 输入记录低价值日常休息的跳过原因，仅对明确标为低信息的想象经历做汇总，并保留未评估明细的覆盖缺口。独立失败材料让后续输入继续；异步 Models 请求持久化原始意图，单独观察权威回执。实际重试、一次 JSON/schema 修正及 quota 变更接入共享 RecoveryPolicy 原 lineage 与预算；unknown 不当作失败重发。
- 来源失效按 actor 分区枚举并清理本域内容；统计由原材料/重试账本与 outbox 游标补投。Operations 提供针对单个 Growth 重试项的 inspect/invoke/observe，原事务执行注入 guard。

## 公开接口及装配

1. 保留现有 `GrowthService` 构造及 `on_projection_changed` 回调，构造后注入 `growth.set_observability(statistics=statistics, recovery_policy=recovery_policy)`、`growth.set_conversation(conversation)`、`growth.set_life(life)`、`growth.set_operations_guard(guard_on)`。这些依赖在本工作树尚未由产品根装配。
2. `await growth.material(context, *, purpose='conversation', query=None, budget=None)` 返回逐条 `ContextContribution`，行为引用含 `rule_id`、`revision`、`semantic_revision`、`semantic_role`。目的统一为 `conversation`、`life.plan`、`writing`、`diary`，兼容传入 `chat`/`planning`。
3. `await growth.resolve_behaviors(context, refs, *, purpose)` 逐引用返回当前规则、请求版本、语义版本、资格和兼容性；`purpose='history'` 可读授权的指定历史 revision，并同时返回当前状态。Broker 无需创建 Growth worker：`BehaviorResolver(database, identity, sources).resolve_behaviors(...)` 复用同一规则实现。
4. 检索的 owner 回读绑定 `await growth.learning_records(context, refs, *, purpose='history')`。输入引用为 `{'kind': 'reflection'|'style'|'interest'|'habit', 'id': ..., 'revision': ...}`；反思记录包含 `kind/id/revision/semantic_revision/requested_revision/compatible/state/content/conditions/counterexamples/episode_ids/support_ids/attempt_id/source_refs/scope_ids/occurred_at/recorded_at`。规则正文从 Companion 回读，不能把索引当权威。
5. Memory 需实现 `lock_episodes(context, refs, *, connection)`（返回带 `items`、`coverage` 的页面，原事务锁定版本）和 `index_learning(context, *, rule_id, revision, semantic_revision, kind, source_refs, scope_ids, search_text, connection)`；`search_text` 仅是本次索引输入，不成为新的正文副本。Memory 在原始 episode 事务中投递 `companion.episode_changed`，payload 为 `context, episode_id, revision, generation`，可带 `watermark`；`generation` 必须是该原事务冻结的代次。
6. Conversation 需提供 `learning_reply(context, *, turn_id=None, expression_id, message_ids=())`，返回原始 `turn_id/expression_id/conversation_id/person_id/scope_ids/source_refs/generated/delivery_state/input_messages/output_messages`；每条消息须有 `message_id`。按真实 turn 和多消息关联提供原件，不能按时间相邻拼接。消费端使用 `growth.record_example_usage(context, example_ids, *, usage_id, outcome, turn_id=None, expression_id=None)`，outcome 为 `selected`、`expressed`、`delivered`；送达会再次核对 Conversation 回执。
7. Models `generation_profile(request)` 需给出实际 `route_id`、`route_revision`、`quota_group`；`submit(request, completion=JobSpec(...))` 支持按原 attempt 幂等提交及完成通知。**Models 接纳事务必须对 completion payload 中冻结的 `generation` 进行原子校验**，否则 Growth 冻结意图后与业务清空之间仍有跨服务接纳竞态；同 attempt 已有记录必须读取账本、不得另开物理请求。此跨域保证待协调者集成确认。
8. 来源服务可用 `CompanionDependencies.actors_for_source(connection, refs, kind)` 枚举分片，再对每个 actor 调 `invalidate_actor(connection, refs, kind, actor_id=...)`；旧 `invalidate` 仍委托相同实现。变更未传播前，材料读取仍独立核验来源。
9. Operations 如注册 `growth.operations.inspect/invoke/observe`，目标为 `{'actor_id', 'scope_key', 'retry_key'}`，使用 `observation_only/domain_managed`，并向 `set_operations_guard` 注入原事务 guard；窄口唤醒已有领域恢复，不由 Operations 额外预留物理 attempt。
10. 统计注入后 `growth.observations.replay(generation, cursor=None)` 按材料和重试账本逐页补投再清空 outbox，只有补投完成才标 producer complete。恢复服务接口、续接原因和额度依赖按协调者发布的 `rb-observation-contract-2026-11-05.md` 装配。

## DDL 与任务注册

- `src/tianshu/companion/habit_revisions.sql` 为 `0039_companion_learning`，必须在本域 `0030_companion`、`0034_companion_growth_batches`、`0037_companion_growth_retry` 之后运行；`companion.module` 已声明，协调者还需在全局 product migrations 中注册并与其他包的编号、顺序对齐。
- 本域 `companion.module` 注册 `companion.organize`、`companion.growth.retry`、`companion.growth.input_retry`、`companion.episode_changed`、`companion.growth.statistics`；请协调者将任务放入相应后台 lane。`growth.recover()` 只由 `bot-background` 启动。所有异步 payload 使用冻结 generation，业务事务提交前校验。
- 新增管理路由：`growth/revise`、`growth/unresolved/dismiss`、`growth/history`、`growth/reflections`、`growth/examples/view`、`growth/examples/reject`；原 `growth/view`、`growth/revoke`、`growth/restore`、`growth/review` 继续使用本域账本。`valid_at` 的反思查询明确返回 `reflection_valid_time_unknown` 覆盖缺口，不推断未知生效期。

## 未验证和集成限制

- 按本批授权，未运行测试、构建、烟测、真实模型或生产操作。已静态审阅差异并运行 `git diff --cached --check`，无空白错误；四个 `test_rb_growth_*` 文件为待协调者统一执行的用例，并非通过记录。
- Memory、Conversation、Models、RecoveryPolicy、Statistics、Operations、Runtime 的跨域接口和注册须协调者合入后集中核对；尤其是上述 Models 接纳代次原子性，以及原回执 unknown、quota 变更、输出纠正、来源撤回、并发纠正、索引回读与真实群/回复场景。此提交本身不能证明全链路业务收敛。
- 当前授权为清空全部天枢业务数据后保留配置；旧业务反思双账本和旧在途材料不提供迁移保证。既有非 `test_rb_*` 测试夹具可能仍调用旧的同步 Models/Memory 接口或缺少 generation，应由协调者在集成验证时按最终合同调整，不得用夹具通过替代真实接入。
