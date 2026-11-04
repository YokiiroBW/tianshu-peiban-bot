# RBW-sources 交接

## 范围与提交

- 任务基线：`e2529c95a92857f368111d9ae5b23ee66800b636`。
- 本包代码及回归用例：`11c116e877114dfb95ab56f3aa3f471d40549a88`，初次交接 `86ade287b1aede5743c6a7558f85b4c0d53a8f3c`；两者已由主聊天集成。
- 本次追加代际修复及用例：`950699300b53f09579ea1062421558da46f0deca`，仍在 `codex/rbw-sources-20261105`；本文件以后续交接提交更新。
- 仅修改 `identity/`、`sources/` 和本卡指定的 `test_rb_*.py`；未改公共装配、全局迁移、其他域、配置或部署。
- 本回执表示本域代码已提交，**不表示跨域接线、整包 RB-02、集中验证或部署已完成**。

## 已实现的本域行为

1. Identity 用稳定场景 ID 标识业务场景：对话由 actor + conversation 确定；无对话角色场景由 actor + person 确定；无角色系统场景由 principal 确定。对话中的不同 principal、群成员、scope、权限和 epoch 不改变场景身份。`SceneAuthority` 单独携带 principal/actor/person/conversation、授权 scope、排序权限、principal epoch 和各 scope 的 access/intake epoch。
2. 定时上下文刷新保留原权限和 scope 上界，与当前授权取交集；丢失一个 scope 不再自动拒绝整个任务。缺失必要权限、角色失效及原场景不合法仍拒绝。真实系统主体可通过 `system_context` 从现有 principal 构造，不创建或授予权限。
3. 写入证明在同一事务锁定当前授权和来源及支持闭包；`check_many_on` 可证明同一事务刚创建的原件。来源状态可以批量查询，身份无权查看时只得到 unavailable。
4. 保留 `SourceRef` 单源直引；多源可使用规范化的 `EvidenceSpec` / `EvidenceSetRef`，分别表示 required、alternative_supports（组内 AND、组间 OR）和 generation_dependencies。证据创建先证明所有成员；后续证明按当下授权及撤回状态重新计算。证据成员带反向索引。
5. 附件补充是独立 SourceRef/修订，原消息及 revision 不变；固定键由原引用和 event/part 确定，同修订内容不同冲突。只允许原来源 owner 的当前授权上下文登记。原件为完整必需依赖，撤回后新补充立即不可读；外部域可在写事务内调用正式绑定证明，不查 Sources 私表。
6. 源变更事务只修改来源事实及关联原件并登记持久传播，不再同步调用 Memory/Life/Companion 等回调，也不直接依赖 Models 私有实现。后台传播后按消费者单独持久派发；分片消费者可按 actor 再创建独立持久任务。已有未分片回调以独立域任务兼容。
7. 自动档案回填读取 Runtime 唯一代际 `runtime.installation_generation(id, created_at)`；早于代际下界的未显式授权记录拒绝，显式授权且内容版本不变的旧引用仍可读取。迁移将旧版已创建的 archive_records 标记为显式授权（旧路径仅允许显式创建）。
8. `record_event` 和 `register_media_supplement` 在真正写入事务内检查 Runtime 的冻结代际；二者可加入调用方事务。当前同步新调用未提供代际时读取当前值，附件补充在文件验证发生异步等待之前捕获该值；跨异步任务必须显式传原有代际，过期任务不能通过重新读取当前代际续写。
9. 匿名/已认证 session inspect、setup、login 和 logout 返回的 `SessionView.data_generation` 取自实际 Runtime 单例；缺失单例时失败，不生成替代 ID。session 查询和 generation 读取在同一事务中，读取时锁定代际行。

## 公开接口和 DTO

```python
scene_id = identity.scene_id(context)
authority = await identity.scene_authority_on(conn, context)  # SceneAuthority
system_context = await identity.system_context(
    configured_principal_id, permission="config.publish", actor_id=None
)
refreshed = await identity.refresh_scheduled_context(
    saved_context, permission="projects.read", require_actor=False
)

evidence = await sources.register_evidence_on(
    conn, context, EvidenceSpec(
        required=(source_a,),
        alternative_supports=((source_b,), (source_c, source_d)),
        generation_dependencies=(source_e,),
    )
)  # SourceRef | EvidenceSetRef
allowed, = await sources.prove_many_on(conn, context, (evidence,), lock=True)
statuses = await sources.source_status_many_on(conn, context, (source_a, source_b))
```

`EvidenceSetRef` 序列化为 `{"evidence_id": "evidence_..."}`；`SourceRef` 为 `{"source_id": "source_...", "revision": 1, "locator": {}}`。证明返回逐项 bool；`SourceStatus` 含 reference、state、head_revision、usable。`lock=True` 仅用于可写事务；短暂读取可用 `prove_many` 或 `source_status_many`。

```python
original_refs = await sources.record_event(
    original_context, producer=producer, event_id=event_id,
    revision=event_revision, occurred_at=occurred_at, parts=parts,
    origin_kind=origin_kind, connection=conn, generation=frozen_generation,
)
supplement_ref = await sources.register_media_supplement(
    original_context, media, original_ref=original_ref,
    producer=producer, event_id=event_id, event_revision=event_revision,
    part_index=part_index, supplement_revision=supplement_revision,
    connection=conn, generation=frozen_generation,
)
bound = await sources.validate_media_supplement_on(
    conn, context, supplement_ref, original_ref=original_ref,
    producer=producer, event_id=event_id, event_revision=event_revision,
    part_index=part_index,
)
```

来源 read 返回的 locator 示例：`{"kind":"media_supplement","producer":"qq:connection","event_id":"message:group:123","event_revision":1,"part":0,"original_ref":{"source_id":"source_...","revision":1,"locator":{}}}`；补充版本为 `supplement_ref.revision`。校验持有来源和授权锁至调用事务结束。读取方需当前授权，登记方还需为原件 owner；后台应恢复原授权上下文。

`generation` 为 Runtime `jobs.current_generation_on(conn)` 返回的 UUID 字符串；调用方恢复队列或补取任务时必须使用其已持久化的原值。`connection=None` 才由 Sources 创建自己的事务，非空时完全参加调用方事务。主聊天负责共享 `SessionView.data_generation` 字段，字段名需保持一致。

角色分片接口：

```python
sources.register_dependant(owner.invalidate_actor, name="stable-owner-name",
                           partitions=owner.actors_for_source)
# async actors_for_source(conn, refs, kind) -> tuple[str | None, ...]
# async invalidate_actor(conn, refs, kind, *, actor_id) -> None
```

不提供 `partitions` 时使用原 `(conn, refs, kind)` 回调，仅做到按域隔离。消费者名称需跨进程/重启稳定；每个分片操作须幂等。

## DDL 与共享装配

1. Runtime 先创建并初始化唯一 `runtime.installation_generation` 行；新空库生成新 ID，配置导入不得带入旧代际。Sources 不再创建自己的代际表。
2. 身份 `0010_identity`、来源 `0011_sources` 后注册 `("0012_sources_rb", "tianshu.sources", ("0012_sources_rb.sql",))`；该文件增加 `archive_records.explicit_authorized`、`media_supplements`、`evidence_sets`、`evidence_members` 和反向索引。`sources.module` 已声明同号迁移，产品全局 `product_migrations()` 仍需协调者注册。
3. `sources.propagate`、`sources.dispatch`、`sources.notify` 均由 Sources module 注册，运行 lane 为 `SOURCE_LIFECYCLE`；协调者将其接到 bot-background，并在该进程注册各消费域、Models 清理适配器和稳定名称。来源传播发生在哪个前台进程无须先具备其他域回调。
4. Models 结果清理通过协调者装配 `(conn, refs, kind)` 适配器调用其公开清理能力；Sources 已不导入 `models.ledger`。Models 及所有读链仍须即时验证 source 权限，不能等待清理任务后才阻断读取。
5. Runtime 的 `rb_runtime.sql` 在 `runtime.schema.sql` 和 Procrastinate 后注册并初始化 generation；共用 `tests/conftest.py::database` 目前只运行 foundation + `0003_procrastinate`，协调者需将 Runtime RB 迁移加入隔离数据库 fixture，否则 Session 和 Sources 测试缺表。共享类型需带 `SessionView.data_generation: str | None`。

## 真正未完成与集中验收

- Memory 需把原 `partition_identity` 的 scope/epoch 哈希从业务身份中移除并使用 `scene_id`，但必须分别保存和校验不同 principal 的原授权上界；同 scene 不代表共享权限。新的 typed evidence 也需要相关写入/检索调用方真正接入。
- 各域需提供按自身私有反向索引定位 actor 的 `partitions` 与只清理该 actor 的回调，并覆盖新版 Episode 语义历史、学习历史、词义/样例和搜索投影。协调者已派发；本提交不冒称它们已经实现。未分片旧回调仍可能一个 actor 故障拖住同域其他 actor 的清理，但不会回滚原来源或其他域。
- 受保留配置中可能引用的身份、scope、来源和文件的闭包识别及空库导入由协调者的配置种子/清空装配处理；本包没有执行导出、清理或保留生产记录，也没有宣称相关配置引用已核实。
- 旧 `tests/core/test_archive_sources.py` 对旧自动历史拒绝原因 `explicit_history_request_required` 的断言需按代际新语义调整；其他旧用例如假设领域清理在撤回事务内同步完成，应改为先验立即不可读，再执行持久消费者任务。
- 所有产品测试、数据库迁移验证、构建、烟测、真实生产及部署均**未执行**。已完成静态完整 diff 审阅及 `git diff --cached --check`；回归用例已编写但尚无运行结果。需集中验证 Runtime 代际装配、session 在各路径返回真实代际、来源跨代拒绝与调用方事务回滚、文件校验期间代际变化、真实 PG 同事务可见性和授权锁、撤回竞态、分 actor 消费失败隔离、附件幂等与跨域接线、AND/OR 和生成依赖、archive 边界及 actorless 真实项目路径。
