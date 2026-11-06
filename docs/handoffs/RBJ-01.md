# RBJ-01：来源图有界证明与锁闭包交接

## 状态和边界

- 工作树：`C:/Users/Administrator/.codex/worktrees/rbj-sources/tianshu-peiban-bot`；分支：`codex/rbj-sources-20261106`。
- 工作基线：`ec4b26a652d53d3dc40ab42a9f61734ff12e320d`；执行合同 1.4：`d888b8dcef0db2bb5e07842bd3a5284b5baa69fe`。根随后单独授权 `evidence.py` 的同调用私有预算传递。本交接与代码同一提交，完整 SHA 以完成回执为准。
- 本卡只完成代码、必要用例代码与静态审阅；没有执行测试、构建、PG、烟测、真实模型、QQ 或生产操作。代码提交不代表集成、性能或业务验收通过。

## 实现与语义

- `sources.check_groups(jsonb,text[])` 仍是数据库侧公开入口，Python 侧 `check_many_on`、`validate_persistence`、`prepare_event_writes` 参数与返回保持。取图按新发现节点逐层展开，逐条计数支持边、片段、输入与字节；不将大图完整物化后再截断。
- 通过逐叶消除识别无环子图，按需迭代求值并在同次同权限模式中复用支持结果。仅无环依赖闭包可复用，带环部分维护当前路径；保留 AND/OR 顺序和短路、有出口的环、disclosure、各引用 locator/片段与原本地权威谓词。无 64 层技术回退；不跨调用或跨事务缓存授权结果。
- `archives.lock_selected` 的 selected/writable/alias_keys 已改为受限展开；共用当前调用预算进行锁前发现、alias→root→有序共享/独占行锁、锁后新鲜复核和后续证明。集合发生变化继续抛 `source_lock_set_changed`；不截断锁集后继续。独立 `lock_selected` 自建有限预算。
- `prepare_event_writes` 将不同真实 context 分别校验、共享预算；只有同一完整 context 和精确 locator 的重复准备合并。主事件的必需来源失败仍抛 `source_support_unavailable`，可选组不可见仍由领域决定是否跳过。
- 同一私有预算传到 `check_many`、`validate_persistence`、`prepare_event_writes`、`record_event`、归档、披露/普通投影、媒体补充/绑定验证及媒体写入相关的锁和证明；Evidence 的 manifest读取、展开、锁和证明也共享预算。保留 Evidence 的 1024/2048 业务限制与 required/generation/alternative、缺失为 false 的语义。
- 归档不再预先无界读取 alias bound；按完整 `extra_aliases`、新根和 `write_ids` 锁定并复核，再有界读取权威状态。技术预算不足直接抛错并回滚整批。`record_event` 和 `publish_projection` 在调用方提供事务时用内部 savepoint 防止捕获预算错误后提交局部来源写入。

预算初值为节点访问 8192、边/片段 65536、工作 1000000、输入项 32768、图字节 32 MiB；单 SQL 请求输入另限 16 MiB。**节点和边是遍历访问预算**：同一节点在锁前、锁后和证明中重新读取会再次计数，因此可处理的不同节点数可能低于 8192。范围/片段本地检查按潜在扫描量保守计费。这些阈值需要根用现场规模和增长夹具验证，不是已测容量。SQL 预算耗尽使用 `54000` 和 `source_dependency_budget_exceeded`；Sources Python 公共边界映射为同名 503 `AppError`。Memory 等直接在 SQL 调用 `any_support_usable` 的路径仍获得准确 PG 错误，不转换成业务 false；根已知悉该边界。

## 迁移与装配

根在 `0012_sources_rb` 和 `rbf_source_proof_reuse` 之后依次注册：

1. `src/tianshu/sources/rbj_lock_graph.sql`：Sources 内部有界锁闭包、alias 目标/键、归档 bound、旧版本支持行和 Evidence manifest 辅助函数。
2. `src/tianshu/sources/rbj_source_proof.sql`：迭代证明和共享预算辅助函数；`CREATE OR REPLACE` 覆盖既有 `sources.check_groups(jsonb,text[])`，保留签名、函数身份和局部 `jit=off`。

`valid_reference` 仍用于独立节点的原始权限/范围谓词；`any_support_usable` 沿用现有 SQL 入口。旧 `_proof_acyclic_plain` 和已发布 SQL 不修改、不作为新证明回退。两份 SQL 不要求旧数据迁移、删边或来源压缩；新增函数和具体迁移顺序由根在 `module.py`/产品装配统一注册。

## 用例代码和待验证项

新增 `tests/sources/test_rbj_graph_proofs.py`：195 节点/8252 边和新增回执、64→65→更深、同事务可见、真实公共读取和 Memory 检索/撤回、权限 epoch、片段、disclosure、范围、OR 短路、有/无出口循环、缺失、节点/边/工作预算、多 context 共预算与去重、alias 高扇出、锁等待期间支持/alias 闭包变化、直接锁定的预算、预算耗尽无部分回执，以及外层事务捕获错误时多段事件回滚。`test_rb_authority.py` 增加多条归档后段耗尽预算时先前 source/archive/alias 原子回滚；旧 proof/lock 用例仅调整与深度、预算及新内部入口直接冲突的断言。

根需先注册两项迁移，再集中运行上述真实 PG 用例与受影响的既有 Sources、归档、撤回/更正、Evidence、Memory 检索及 Life 结果原子并发用例；明确测试同一调用预算边界和 `source_lock_set_changed`。测量现场拓扑的取图、求值、持锁与等待时间，确认新锁闭包和多次预算计费在实际负载下可接受。当前没有 SQL 执行、PG 行为、性能及线上业务恢复证据；若集中测试发现差异，应先按当前合同返修，不把静态检查当通过。
