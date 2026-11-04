# RBW-memory_write 交接

状态：**代码已提交，待集成和集中验证；RB-08 的自动闭包重组、真实索引就绪展示尚未闭环，不能标整卡完成。**

- 基线：`e2529c95a92857f368111d9ae5b23ee66800b636`
- 代码及测试提交：`e50e842654242c5f569b13d96cc21cdbe2c8b4ae`
- 分支：`codex/rbw-memory-write-20261105`。本交接文件在后续独立提交中；协调者合入时取包含本文件的分支头。
- 未合主线、未推送、未操作生产。`.codex/config.toml` 是已有未提交配置修改，没有纳入提交。

## 实现

- Episode 的事件版本与语义版本分开，保存语义版本快照；相同语义内容的事件重放不推进语义版本。支持当前、指定发生时间及已知时间读取，当前权限、来源和遗忘约束也适用于历史。Episode 本身只有发生时间，`valid_at` 筛选该发生时间，不虚构编辑生效区间。
- 同一场景采用稳定分区；逐 Episode 保留原始权限上界，同一批只装入可按相同原主体和权限界安全处理的材料。附图后到通过 Sources 证明绑定原事件、原版本和原 part，幂等附着；原图撤回不直接删除独立原文来源。模型可见的全部材料及旧记录作为生成依赖，语义支持组另行记录。
- 共享提案两遍编译：按主体登记局部别名、显式解析跨主体端点，保存 canonical proposal、alias map、编译器/schema 版本、来源快照和摘要；同批对象、目标覆盖和提交回执共用事务。模型协议的 `no_change` 必须有真实非空 `reason`；changed/unresolved 可采用操作或未决项的原因。
- 异步提交固定 attempt，等待时释放 worker；完成通知和回退唤醒只观察原回执。编译修复可用已存结果新增可追溯编译尝试，不发送新模型请求。真实成功、未决、原件缺失及失败分开记录。来源、权限、版本、CAS、批次状态和安装代际在业务提交时复核。
- Runtime `RecoveryPolicy` 注入后，实际物理尝试、原 unknown 观察、语义纠正和闭包重组接入同一 lineage；统计以批次稳定身份、修订和 outbox 上报，来源撤回后修订旧统计贡献。依赖恢复及统计都需要主装配注入。
- 群词义使用既有 `group:<conversation_id>` 下的 `language.term_meaning` Claim，保留实际发言者、条件和不同解释；使用记录仅为曝光，不改变可信度。删除 Memory 自有 Reflection 读写及对应旧表，学习权威交由 Companion，Memory 仅使用 Retrieval 所有的 typed-reference 索引。

## 接口和装配

- `MemoryService.bind_observability(statistics=..., recovery_policy=...)`；`await memory.backfill_statistics(limit=128)` 按批次账本补齐并清空 outbox。`MemoryWorkers.recover()` 会调用补投和 `consolidation.claim_due_recoveries()`。
- `await memory.episode_at(context, episode_id, time_mode="current"|"valid_at"|"known_at", valid_at=..., known_at=...)`；HTTP `POST /api/v1/memory/episodes/get`。`await memory.lock_episodes(context, ((episode_id, revision), ...), connection=conn)` 返回带覆盖状态的 `EpisodePage`。
- `await memory.supplement_episode(context, episode_id=..., event_id=..., event_revision=..., part_index=..., supplement_revision=..., original_ref=..., supplement_ref=...)` 依赖 Sources 的 `validate_media_supplement_on`。`Episode.supplement_refs` 是单独字段；Retrieval 的证据和读取必须同时校验原来源及附加来源。
- `await memory.group_term_meanings(context, text, limit=8)` 返回 `GroupTermMeaning`；`await memory.record_group_term_usage(context, event_id, ((claim_id, revision), ...), outcome=...)` 的 outcome 为 `no_match/matched/explained/used`。Conversation 使用时需按实际结果调用，不能把使用次数当可信度反馈。
- `await memory.index_learning(context, rule_id=..., revision=..., semantic_revision=..., kind=..., source_refs=..., scope_ids=..., search_text=..., connection=conn)` 委托 Retrieval 的 `put_learning_reference_on`；失效传播需要其 `purge_learning_references_on(conn, refs, kind, actor_id=...)` 和 `memory.learning_index_refs`。记忆证据增加 `behavior_refs`，默认查询种类用 `learning` 而非 `reflection`。
- Sources 注册 `sources.register_dependant(memory.invalidate_actor, name="memory", partitions=memory.actors_for_source)`，并移除旧的同步 `memory.claims.source_changed` 注册；Sources 新接口负责逐 actor 派发。`tests/core/conftest.py` 同样须由协调者按此更新。
- Memory 的 index-object 任务在产生它的业务事务内，把同一 generation 固定到 JobSpec envelope 和 payload；`MemoryWorkers.index_object(..., generation=None)`、`index_retry(index_id, completed_attempt_id=None, generation=None)` 透传给 Retrieval 的 IndexJobs。旧任务不补绑定当前 generation。
- `await memory.consolidation.recompile_recorded(context, batch_id, expected_digest=...)` 允许按原结果及原读取集重新编译，失败前无选中摘要时传 `None`；仍需当前权限、来源、原版本和业务状态校验。这是程序接口，尚未接为 Operations `RecoveryAction`。
- Episode 状态 DTO 已返回 `commit_state`、`required_context_missing` 和 `indexing={state, objects}`；目前变更对象的索引状态为 `unknown`，无对象为 `not_required`，不把入队当索引完成。需要 Retrieval 的真实只读索引状态接口与前端展示接线。

## DDL 与顺序

`src/tianshu/memory/0025_memory_write.sql` 已在 Memory 模块紧随 `0024_memory_consolidation_retry` 注册；协调者还须在全局 product migrations 对同一文件按相同顺序注册。新增 Episode 快照/补充来源、编译尝试、重组、恢复 claim 和统计 outbox 表及相关字段；修改 batch 状态约束。迁移移除旧 Reflection 表、相关语义链接和索引扫描项，符合本次清空天枢业务数据、保留配置的授权；不得当作旧数据迁移方案。Runtime、Sources 和 Retrieval 的相应新迁移须先于使用其表/接口的运行装配完成。

集成前需确认 Growth 的 `growth_recovery.py` 不再查询 `memory.reflections`，Retrieval 不再导入 `ReflectionView` 或调用 `codec.reflection`；既有旧测试夹具也需转向 Companion 真正的学习权威。产品统一注入 Sources、Statistics 和 RecoveryPolicy、注册正确的运行角色/路由及新域迁移。

## 未闭环与集中验证

1. 重组实现只接受 `frozen.dependency_closure={complete: true, components: [[episode_id, ...], ...]}`，并再次检查来源、回复、事项及旧记录等已知边；未知依赖不拆分。当前没有可信生产者可证明自然语言材料的完整依赖闭包，因此真实截断/容量失败不会自动形成这些已证明子批，未证明时落 `configure_budget`；这项 RB-08 仍须完成，不能因为拆分 helper 或合成测试存在而标记完成。其他独立批次仍应继续，需集中验证。
2. 索引真实就绪尚无 Retrieval 只读状态接线，DTO 对变更对象明确返回 `unknown`。未实现带 Operations guard 的 Memory 单对象 `RecoveryAction`，不能用全量 recover 扫描冒充该窄口。
3. Models 提交端须在持久事务按 `completion.data_generation` 原子拒绝旧代际，并让已过期 completion 可收敛。Memory 已固定完成通知及最终业务事务代际，但不能代替 Models 入队端的原子校验。
4. `tests/memory/test_rb_write_compiler.py`、`tests/core/test_rb_consolidation_write.py` 已编写，覆盖局部/跨主体引用、回执原子性、异步和代际、来源撤回、历史、补图、统计及共同恢复等；这些是待执行用例，不是通过结果。集中验证需实际装配 Runtime/Sources/Models/Retrieval/Growth 后核对 PostgreSQL 迁移、真实队列/多主体事务、撤回与迟到回执、索引 freshness、统计补投和闭包分批；修正旧协议夹具中的 `no_change.reason`。

按本批分工只做静态审阅：`git diff --cached --check` 通过，19 个提交 Python 文件通过 AST 解析；没有执行测试、构建、烟测或部署。集中验收结果和真实使用效果未知。
