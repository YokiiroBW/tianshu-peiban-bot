# RBW-memory_write 交接

状态：**本包实现与回归用例已提交，待主协调者装配和集中验证。** 索引就绪读取和 Operations 恢复窄口均需跨模块接线；不能把静态审阅称为实际业务验收。

- 基线：`e2529c95a92857f368111d9ae5b23ee66800b636`
- 初始代码及测试提交：`e50e842654242c5f569b13d96cc21cdbe2c8b4ae`；本交接所在后续增量补齐索引状态、补图事务和模型请求代际。
- 分支：`codex/rbw-memory-write-20261105`。协调者合入时取包含本文件的分支头。
- 未合主线、未推送、未操作生产。`.codex/config.toml` 是已有未提交配置修改，没有纳入提交。

## 实现

- Episode 的事件版本与语义版本分开，保存语义版本快照；相同语义内容的事件重放不推进语义版本。支持当前、指定发生时间及已知时间读取，当前权限、来源和遗忘约束也适用于历史。Episode 本身只有发生时间，`valid_at` 筛选该发生时间，不虚构编辑生效区间。
- 同一场景采用稳定分区；逐 Episode 保留原始权限上界，同一批只装入可按相同原主体和权限界安全处理的材料。附图后到通过 Sources 证明绑定原事件、原版本和原 part，幂等附着；原图撤回不直接删除独立原文来源。模型可见的全部材料及旧记录作为生成依赖，语义支持组另行记录。
- 共享提案两遍编译：按主体登记局部别名、显式解析跨主体端点，保存 canonical proposal、alias map、编译器/schema 版本、来源快照和摘要；同批对象、目标覆盖和提交回执共用事务。模型协议的 `no_change` 必须有真实非空 `reason`；changed/unresolved 可采用操作或未决项的原因。
- 异步提交固定 attempt，等待时释放 worker；完成通知和回退唤醒只观察原回执。编译修复可用已存结果新增可追溯编译尝试，不发送新模型请求。真实成功、未决、原件缺失及失败分开记录。来源、权限、版本、CAS、批次状态和安装代际在业务提交时复核。
- Runtime `RecoveryPolicy` 注入后，实际物理尝试、原 unknown 观察、语义纠正和闭包重组接入同一 lineage；统计以批次稳定身份、修订和 outbox 上报，来源撤回后修订旧统计贡献。依赖恢复及统计都需要主装配注入。
- `MaterialBuilder` 将未知语义关联的整批材料冻结为一个不可拆组件；只有程序明确给出隔离分组，且每个模型可见材料、旧记录及已知来源/回复/事项依赖均有完整归属并通过校验，才允许拆批。无法拆分而超过容量时记 `closed_component_exceeds_budget`：模型之前保留来源版本和冻结批，截断之后保留原模型尝试、回执和原错误。调整预算后按完整原组件恢复；已有物理尝试时以原 lineage 新增明确替代批，不会只恢复一条材料或重置预算。不会用缺少关联边推定自然语言独立。
- 群词义使用既有 `group:<conversation_id>` 下的 `language.term_meaning` Claim，保留实际发言者、条件和不同解释；使用记录仅为曝光，不改变可信度。删除 Memory 自有 Reflection 读写及对应旧表，学习权威交由 Companion，Memory 仅使用 Retrieval 所有的 typed-reference 索引。
- 新库不再注册旧队列 `memory.extract`、`memory.observe_legacy`、`memory.index`，移除了只转换/扫描旧单 Episode 请求的工作者及其旧 schema。`memory.extract` 作为新版共享整理的 Models 功能名仍在使用；新版 unknown 原尝试观察、人工恢复和预算修复均保留。

## 接口和装配

- `MemoryService.bind_observability(statistics=..., recovery_policy=...)`；`await memory.backfill_statistics(limit=128, generation=...)` 按批次账本补齐并清空 outbox。`MemoryWorkers.recover(generation=...)` 将相同代际传给 Retrieval `IndexJobs.recover(generation)`、共同恢复领取及补投；周期任务 envelope 和 payload 均冻结该代际。
- `await memory.episode_at(context, episode_id, time_mode="current"|"valid_at"|"known_at", valid_at=..., known_at=...)`；HTTP `POST /api/v1/memory/episodes/get`。`await memory.lock_episodes(context, ((episode_id, revision), ...), connection=conn)` 返回带覆盖状态的 `EpisodePage`。
- `await memory.supplement_episode(context, episode_id=..., event_id=..., event_revision=..., part_index=..., supplement_revision=..., original_ref=..., supplement_ref=..., generation=..., connection=None)` 依赖 Sources 的 `validate_media_supplement_on`；`generation` 必须为原媒体任务冻结的代际，传入 `connection` 时在同一事务验证并保持共享锁。Conversation 可与 `Sources.register_media_supplement` 共用事务和代际，不可对迟到任务重取当前代际。`Episode.supplement_refs` 是单独字段；Retrieval 的证据和读取必须同时校验原来源及附加来源。
- `await memory.group_term_meanings(context, text, limit=8)` 返回 `GroupTermMeaning`；`await memory.record_group_term_usage(context, event_id, ((claim_id, revision), ...), outcome=...)` 的 outcome 为 `no_match/matched/explained/used`。Conversation 使用时需按实际结果调用，不能把使用次数当可信度反馈。
- `await memory.index_learning(context, rule_id=..., revision=..., semantic_revision=..., kind=..., source_refs=..., scope_ids=..., search_text=..., connection=conn)` 委托 Retrieval 的 `put_learning_reference_on`；失效传播需要其 `purge_learning_references_on(conn, refs, kind, actor_id=...)` 和 `memory.learning_index_refs`。记忆证据增加 `behavior_refs`，默认查询种类用 `learning` 而非 `reflection`。
- Sources 注册 `sources.register_dependant(memory.invalidate_actor, name="memory", partitions=memory.actors_for_source)`，并移除旧的同步 `memory.claims.source_changed` 注册；Sources 新接口负责逐 actor 派发。`tests/core/conftest.py` 同样须由协调者按此更新。
- Memory 的 index-object 任务在产生它的业务事务内，把同一 generation 固定到 JobSpec envelope 和 payload；`MemoryWorkers.index_object(..., generation=None)`、`index_retry(index_id, completed_attempt_id=None, generation=None)` 透传给 Retrieval 的 IndexJobs。旧任务不补绑定当前 generation。
- `await memory.consolidation.recompile_recorded(context, batch_id, expected_digest=...)` 允许按原结果及原读取集重新编译，失败前无选中摘要时传 `None`；仍需当前权限、来源、原版本和业务状态校验。这是程序接口，尚未接为 Operations `RecoveryAction`。
- Operations 装配 `memory.set_operations_guard(RecoveryExecutor.guard_on)`，注册 `RecoveryAction("memory.consolidation.resume", frozenset({"memory_consolidation_failed"}), memory.operations.inspect, memory.operations.invoke, memory.operations.observe, observation_only=True, domain_managed=True)`；机械候选为 `await memory.recovery_candidates(limit=32)`，目标 `{kind: "memory_batch", id: batch_id}`。该口只唤醒指定批次已有恢复或观察，guard 与版本/来源重验和域内入队同事务；共享原 lineage 和预算，unknown/已取消/撤回不派发，verified 需要实际提交覆盖回执。机械编译错误优先使用上面的原结果重编译，不经此口盲发模型。
- 主装配调用 `memory.bind_index_readiness(retrieval.index_readiness)`；`extraction/status` 在 Memory 事务外按最多 128 条调用该只读端口，接收同序的 `kind/id/revision/profile_key/generation/state`，再重新验证 Episode、来源和批次。DTO 中 `indexing.state` 为 `ready/pending/failed/unavailable/not_required`，每个对象保留端口返回的状态和 profile/generation；依赖不可用、响应不合法或并发状态反复变化时如实返回 `unavailable`。没有绑定时不会冒称就绪。其他事务内 Episode 批量投影继续显示 `unknown`，不会在调用方事务中执行网络查询。

## DDL 与顺序

`src/tianshu/memory/0025_memory_write.sql` 已在 Memory 模块紧随 `0024_memory_consolidation_retry` 注册；协调者还须在全局 product migrations 对同一文件按相同顺序注册。新增 Episode 快照/补充来源、编译尝试、重组、恢复 claim 和统计 outbox 表及相关字段；修改 batch 状态约束。迁移移除旧 Reflection 表、相关语义链接和索引扫描项，符合本次清空天枢业务数据、保留配置的授权；不得当作旧数据迁移方案。Runtime、Sources 和 Retrieval 的相应新迁移须先于使用其表/接口的运行装配完成。

集成前需确认 Growth 的 `growth_recovery.py` 不再查询 `memory.reflections`，Retrieval 不再导入 `ReflectionView` 或调用 `codec.reflection`；既有旧测试夹具也需转向 Companion 真正的学习权威。产品统一注入 Sources、Statistics 和 RecoveryPolicy、注册正确的运行角色/路由及新域迁移。

旧测试 `tests/memory/test_extraction_contract.py` 仅覆盖已删除的单 Episode schema；`tests/core/test_consolidation_pipeline.py` 中 legacy 测试段仅覆盖已删除的队列转换。`test_generation_policy.py:208` 和 `test_consolidation_contract.py:227` 的 `ConsolidationRecovery(memory,None)` 须改为单参数构造。上述旧测试位于本卡测试边界之外，由协调者在集中验证时清理；原历史迁移 `0021_memory_consolidation.sql` 的 `change_kind='legacy'` 默认/约束和回填可随新空库 schema 压平处理。本次未发现只供这些旧队列使用的独立新域表，不建议对当前语义记录或来源表做额外删除。

## 边界与集中验证

1. 实际 `MaterialBuilder` 冻结 `dependency_closure`；当前自然语言输入没有程序给出跨组件隔离证明时始终是一整个组件，不能自动把它拆成多个子批。明确隔离证明需以 `basis=program_isolated` 给出逐材料 `material_episodes` 和逐旧记录 `record_episodes`，验证所有声明与已知依赖后才能切分。未能拆开时 `closed_component_exceeds_budget`，其他独立批次继续。此保守行为不声称能判定任意自然语言的隐含关联。
2. 索引状态仅在 `extraction/status` 已按 Retrieval 公开接口接入，真实端口与前端还需主装配和联合验收；事务内批量投影仍为 `unknown`。Operations 窄口必须按上述方式注册；不是全量 recover 扫描，也不代替统一恢复账本。
3. Memory 已将原批次代际传入 ModelRequest 和 completion，纠正及重试继承原值，业务提交事务也复核代际。Models 分支 `d3c2ff50` 已提交 `ModelRequest.data_generation` 和接收/持久化代际围栏及过期 completion 处理；须集成该增量并联合验证，Memory 侧不能代替入队端校验。
4. `tests/memory/test_rb_write_compiler.py`、`tests/core/test_rb_consolidation_write.py` 已编写，覆盖局部/跨主体引用、回执原子性、异步和代际、来源撤回、历史、补图、统计、单批 guard/unknown 及保守闭包等；这些是待执行用例，不是通过结果。集中验证需实际装配 Runtime/Sources/Models/Retrieval/Growth/Operations 后核对 PostgreSQL 迁移、真实队列/多主体事务、撤回与迟到回执、索引 freshness、统计补投和闭包分批；修正旧协议夹具中的 `no_change.reason`。

按本批分工只做静态审阅和 Python AST 解析；没有执行测试、构建、烟测或部署。集中验收结果和真实使用效果未知。
