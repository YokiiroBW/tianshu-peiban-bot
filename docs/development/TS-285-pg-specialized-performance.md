# TS-285 / TS-286 PostgreSQL 专用链路优化

2026-10-22 用户已授权实施：集中优化 PG 记忆召回、资料库检索和来源同步；优先 PG 专用路径，SQLite 仅保证功能兼容。沿用同聊天 gpt-6.1-sol / max 并行授权，产品代码单写者，协调者独占共享记录与 NAS 实测。不推送、部署或迁移生产。

## 冻结输入与归属

- TS-285：`worktrees/TS-285/tianshu-memory`，`codex/ts-285-pg-specialized`；基线为最新已验收完整历史 `c696fd0de073dc375b161fb8192dc2713dc26b63`，包含 TS267/272/280/282/284。Memory main 仍为 e5e1b3，本任务不接管 Q18 联合装配。
- TS-286：`worktrees/TS-286/workspace`，`codex/ts-286-pg-specialized`；基线 `413e707286dc8a038edd47c6ef24caa8e2d8e02d`。复用既有隔离负载、归因、安装证明及清理机制。
- 共享存储固定 0.1.3 / 019b954，合同沿用上轮精确冻结夹具。TS275/278/271 未采用候选不直接混入；若某项有独立价值，需在本号 PG 路径重新实现、验证并说明。

## 实施范围

先定位并处理 PG 内仍然逐条等待或重复执行的工作，按独立功能块提交。优先检查召回正文准入后的 projection / fact 批读、外部排序后第二次 operation 的完整结果复核、资料检索当前文档与文件核验所需的重复元数据读取、来源同步同一校验阶段内的独立读写。

复用 `is_postgres`、`read_batch`、`SourceSnapshot`、`FactSnapshot` 及已有业务规则，不另建通用存储框架。不凭 SQL 数量推定收益，不替换 PG 当前排序公式，不缓存文档已通过或授权/来源有效结论。范围内确需共享入口薄装配时，SQLite 继续执行原语义；不以 SQLite 耗时作为性能采用条件。

TS-285 可写：`service.py`、`knowledge.py`、`source_authority.py`、`postgres*.py`、`recall_index.py`、`fact_queries.py`、必要的 `contracts.py`、相关现有测试及 `docs/handoffs/TS-285.md`。其余入口、依赖、schema/迁移与锁模型保持，若发现具体需要再由协调者按当前授权明确范围。

## 必须保持

- 当前完整输入合同和关系校验、作用域/身份/项目权限、来源 lineage/revision/epoch/admission/suppression、完整组和 projection 校验、原错误及排序语义。
- 常见路径六次 fresh revision 与三个依赖 owner RPC；重试及 head 变化另行重新核验。外部排序后第二次 operation 重新同步并验证当前授权与版本。
- 权威锁确认后独立 SELECT 取得当前状态；CAS、跨 actor 撤回广播、负向同步先持久化、整批失败原子性、guard 先于 commit、PG 同步提交。
- 资料原文件仍在文件库，双读 hash、版本、证据与最终校验完整。媒体与默认文件库不迁移。

## 验证与采用

受影响测试先集中运行，来源事务或共享能力变更后按产品约定完整验证；PG 节点必须原生运行。SQLite 做功能兼容，不再追加 SQLite 性能对照。独立审阅确认完整边界，再冻结运行提交与离线 wheel。

PG 前后同一依赖/合成夹具、三轮交替、一次一个活动客户端，覆盖记忆 field / keyword / 零预算 / 撤回、完整来源同步、资料 exact / cohort / fresh / HTTP / 导入及真实提交。正式时间无 profiler，保留全部样本、p50/p95/p99、CPU、签名、不利结果及后台负载；归因另列。现有来源基准仍使用合成 owner，不冒称真实跨服务 TLS 性能。

仅使用本号 UUID 低权限 PG 库、角色及专属合成文件目录，不访问生产业务表、不挂载生产数据或媒体、不调用 Redis/真实 owner/模型/QQ，不改生产 PG 参数或索引。复用容量保护和服务就绪核查；状态变化停止本号负载并保留证据，只清理本号资源。已有其它任务和运行资源不接管。

PG 有实测收益且语义等价才采用；无收益块停止并保留反证。交接区分本地实现、隔离验证、产品集成、部署，不把候选完成称生产生效。
