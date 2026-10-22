# 天枢业务链路性能优化方案

规划日期：2026年10月22日。范围为记忆召回、来源同步与资料库检索；本版只规划，不启动实现、压力测试或生产操作。

优先减少一次业务操作内的重复序列化、派生计算和数据库逐条等待。完整权限、来源校验与持久性保持，SQLite不出现可重复的性能退步后才采用。PG逐场景与SQLite比较，保留仍慢的结果；不预先承诺提速比例。

## 现有证据与实现起点

当前Memory主检出为`e5e1b394a8f6d318dda4d9280587e599fd64c653`，共享存储为`0.1.3 / 019b954`。最近来源校验实验基于完整历史`4dbb8163524af55822d492cfdfda740090b569e9`，其最终候选`eb65763`未采用：SQLite普通提交配对耗时约增加8.03%，PG来源短查询也未改善。TS271连接层0.1.4候选同样未采用。正式实现由集成人对齐最新已集成产品历史后冻结输入，不从这些被否决的候选继续叠加。

已有诊断显示来源召回的重要CPU成本在完整合同校验，但这不意味着其它计算全部没有价值。新增优化必须说明少做了什么工作，以及端到端实际省了多少时间。上一轮额外写入诊断没有把普通写入不利定位到校验缓存或GC，本版不恢复该方向的缓存微调。

依据：[既有链路归因](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/pg-system-performance-2026-10-21.research.md)、[最近完整实测](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/validation-recall-performance-2026-10-22.md)。这些主要是300或365组合成数据、单个活动客户端的隔离结果，不能代表生产并发能力。

## 必须保持的业务边界

记忆来源链继续按以下顺序执行：

`本地起点及权限读取 → Core事实快照 → Platform当前访问 → Core最终head → 来源同步事务 → 业务事务与最终权限检查`

- 常见无重试路径的六次本地fresh revision读取保持；它们不是六次远端RPC。head变化、跨批次和重试分支原有的额外核验也保持。
- Core事实、Platform授权和最终head存在依赖，不直接并行。来源负向结果先持久化，再进入后续业务；不提交跨批次的成功前缀。
- 获取数据库权威锁后，再启动独立SELECT取得当前状态。PG的READ COMMITTED按语句取得快照，不能用同一条CTE替代锁后的新语句。[PG事务隔离说明](https://www.postgresql.org/docs/17/transaction-iso.html#XACT-READ-COMMITTED)
- 先核验scope、来源lineage、revision、epoch、admission和suppression，再读取允许的正文；完整组、projection和当前授权语料上的排序规则保持。来源撤回可影响其他actor，不按人物直接拆锁。
- 外部排序结束后的第二次operation仍重新同步、授权和核对版本，不复用第一次operation的通过结论。有效期和当前时钟也不因请求内计算复用而固定。
- 资料库保留项目及操作权限、文档版本、证据hash和最终阶段校验。`FileReader`已有的事务外双读hash及请求内去重保持，不改成仅看mtime或只读一次。
- guard仍先于数据库提交；PG同步持久提交、SQLite WAL/FULL保持。媒体、原文件和资料正文的现有文件库存放方式保持。

## 已有能力与本版新增方向

| 已有能力 | 本版处理 |
| --- | --- |
| Contracts顶层validator与静态引用准备 | 继续复用，不新增通用校验引擎，也不记住某份文档已经通过 |
| `SourceSnapshot`在PG同步事务内批读 | 复用其边界和更新规则，只处理重复派生数据与剩余逐条等待 |
| `FactSnapshot`在一次PG召回内复用事实读取和解码 | 继续传递已有对象，不再做一套事实缓存；SQLite保持现有惰性读取 |
| exact召回已有批读和512组完整回退 | 不恢复上一轮未通过的SQL改写，不减少候选或截断完整组 |
| `KnowledgeStorage`复用Store资源，资料读取已有capture和serve阶段 | 保留逐次授权及配置核对；不把资源复用包装成新的性能成果 |

## 第一阶段 减少结果预算的重复序列化

这是首个实现块，覆盖两种后端的纯计算，暂不改变来源协议和事务。

[MemoryService._fit_groups](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-memory/src/tianshu_memory/service.py:519)每加入一个候选组，都会复制并重新编码整个已选结果前缀。[KnowledgeApplication._query](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-memory/src/tianshu_memory/knowledge.py:1222)逐块计算预算也重复编码已选blocks。默认记忆最多32个单元、资料查询最多128个候选会限制规模，收益需按真实累计编码量判断。

改为在已有权限和完整性检查之后，对当前组或块的规范JSON只计算一次UTF8字节数，按原结构精确累计固定外壳、逗号及数组内容的大小；结果对象在原业务位置组装。空结果、单组和小预算场景保持轻量，避免为了大结果集给短查询增加准备负担。

业务结果必须逐字段相等：记录及组顺序、`budget_used`、omissions、max_units和完整组边界不变。记忆token预算仍按现有保守UTF8字节规则计费；资料库继续为原有的budget及stale_source提示预留空间。某组过大时继续检查后面的组，不改为提前结束，也不拆组。

以旧实现作为对照，覆盖中文、emoji、引号、转义、空数组、嵌套来源、多单元组、恰好预算及差1字节、前组超限而后组可容纳等情况。记录规范编码调用数、编码字节总量、CPU与端到端延迟。预算实现留在各自业务归属处，只提取确实相同的纯规则，不建立通用响应框架。

## 第二阶段 复用一次同步内的派生数据

沿[SourceAuthority._commit_observations与_apply](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-memory/src/tianshu_memory/source_authority.py:293)和[SourceSnapshot](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-memory/src/tianshu_memory/postgres_source_snapshot.py:18)，核对同一physical、selector、author的canonical文本、fingerprint和索引映射在哪些循环中重复生成。把已经生成的派生键传给需要它的既有步骤，减少重复遍历、JSON编码和hash。

复用范围限于当前输入与当前事务阶段。owner刷新、重新观察、CAS重试或输入改变后重建；数据库行派生值不能跨写入继续使用，现有`SourceSnapshot.withdraw`的同步更新保持。SQLite不构造PG专用行快照或批读包装。

本块不减少Contracts完整文档检查、关系规则、来源或授权判定次数。校验顺序、format、错误行为和对当前输入的遍历保持；不通过“已验证”标记跳过可信端口的校验。若纯计算复用需要改变这些语义，就停止该实现。

## 第三阶段 减少同一事务阶段的逐条等待

仅处理已有依赖层内、参数已经确定的独立读取，复用`read_batch`和既有完整组校验规则。批发仍由服务端按顺序执行，目标是减少客户端逐条等待；全部成功后才交出结果。[PG pipeline说明](https://www.postgresql.org/docs/17/libpq-pipeline-mode.html)

优先检查两个位置：

1. 非exact召回中剩余的projection与事实元数据分块读取；已批量化的exact路径不重复包装。正文读取仍在来源准入之后，坏projection对应的事实JSON仍不得提前解码。
2. [MemoryService._verify_retrieval](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-memory/src/tianshu_memory/service.py:490)在外部排序后的第二次operation中，逐组读取状态、成员、lineage、版本和projection。研究按依赖层批读这些当前元数据，仍在这次新的授权与版本边界内完成全部检查。

每条SQL保留独立语句语义；不把锁确认、CAS检查、后续DML和跨事务读取一起送入pipeline。保留256 selector及512 group分块和完整回退。SQLite继续使用已有便宜的索引点查及拒绝时短路；通用业务结果保持一致，PG批读策略不强加给SQLite。

若省下的等待被行适配、JSON转换或额外读取抵消，或者目标调用已无明显逐条等待，就停止这个块，不继续为减少SQL条数而合并。

## 第四阶段 验证来源HTTPS准备与连接成本

[SourceTransport._post](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-memory/src/tianshu_memory/source_transport.py:29)当前每个RPC重新读取配置、构造TLS上下文及Client，并在该RPC后关闭。此前合成owner实测没有包含这条真实HTTPS链路，不能据此预估连接复用的收益。

先用独立的Core及Platform合成HTTPS端点，分开记录配置与CA读取、TLS上下文构造、连接建立、owner处理、JSON解析及完整校验。复用现有归因工具补充缺失的观察，不另建常驻监控或把带hook样本计入正式成绩。

若准备成本明显，先评估同一次operation内按实际信任材料复用TLS准备对象、每个RPC仍建立新连接的方案。连接keepalive作为另一个独立候选：仅在端点、凭据、CA和验证策略逐次核对一致时，才研究同owner的短生命周期Client复用，operation结束关闭。HTTPX Client具备连接池，但这不自动证明适合当前动态配置语义。[HTTPX Client](https://www.python-httpx.org/advanced/clients/)、[TLS配置](https://www.python-httpx.org/advanced/ssl/)

必须保留每个RPC重新读取配置和当前凭据；端点、凭据、CA内容或策略变化时旧资源立即退出使用。不能只比较mtime；CA读取与实际使用必须一致。默认系统信任库或证书有效期无法可靠保持现有语义时沿用原新建路径。若keepalive会削弱逐次TLS验证，就不采用连接复用。

沿用并扩充既有动态端点、token与CA轮换测试，覆盖请求中途撤信、错误证书、无效CA不得发送凭据、超时、异常后的资源关闭、并发请求互不串用身份。owner调用顺序、响应文档、关联request_id和完整来源校验保持；不增加自动重试或请求合并。

## 复用与等待的限制

| 数据或工作 | 允许范围 | 失效边界 |
| --- | --- | --- |
| 已准入单元或资料块的编码长度、当前查询的纯派生值 | 当前业务阶段、未变化的输入 | 阶段结束、输入改变或重新读取 |
| SourceSnapshot与FactSnapshot的既有派生映射 | 原有事务与调用生命周期 | 原有写入更新规则、重试或事务结束 |
| 配置及TLS准备对象 | 仅第四阶段验证通过的短生命周期范围 | 逐次配置、凭据、信任材料或策略变化 |
| 当前授权、来源有效性、owner head、本地revision、时钟与有效期 | 继续按原边界获取和核验 | 不以请求缓存或Redis的上次结论替代 |

首批保留`SourceAuthority.operation`锁和Store的advisory lock。外部调用期间不持有数据库事务、资料文件I/O在事务外执行的现有能力继续使用。跨selector批次并发、放宽全局锁或改变revision粒度，不纳入首批；它们需要单独证明跨actor撤回、head对齐和guard一致性。增加连接池或线程数不能代替这项证明。

Redis在本版不新增权限、来源、正文或检索结果缓存。文档通过结论也不跨调用复用。

## 验收与SQLite性能保护

每个实现块单独比较和决定采用，已通过且输入未变的检查复用。先做受影响行为的等价检查和一次定位，稳定后集中执行项目要求的专项；改到来源事务或共享能力时再按现有约定完整验证，不在每次小改动后重复全套。

功能对照保留身份与项目权限、配置撤销、跨人物访问、物理撤回广播、receipt全域冲突、NULL alias、owner head变化、过期上下文、CAS重试、来源缺失、坏projection、完整组及中途失败不返回半批。PG原生节点和SQLite均需实际执行，替身不能代替原生PG证明。

性能比较分为三组，分别统计，避免混合协议：

| 比较 | 负载与数据 | 记录 |
| --- | --- | --- |
| 回归与端到端 | 复用300或365组既有原生业务夹具，覆盖短field、关键词、来源同步、零预算、撤回、普通及来源写入；增加资料检索多文档多块与预算边界 | p50、p95、全部wall与客户端CPU、内容及排序证明；旧新与PG和SQLite三轮交替 |
| SQLite读写配对 | 复用3300对读取和四类共240对真实提交协议，预热单列；每次写从封存的相同DB及guard字节开始，提交后关闭重开 | ledger、增长、内容、FK、quick_check与持久性；新旧版本跨block交换物理目录及worker位置，避免固定位置与版本绑定 |
| 规模及并发 | 在独立测试环境递增到1000及5000组合成数据、并发1和4，覆盖真实跨256 selector及512 group边界；另用隔离HTTPS端点测试传输 | 吞吐、p95、排队与锁等待、CAS重试、连接数、内存；PG原生写历史与导入历史明确区分 |

正式时间不带profiler；JSON、hash、RPC和SQL调用数等归因另列。保留所有轮次、异常、尾延迟和不利结果，准确记录后台负载。上一轮普通写入的wall与CPU不一致说明固定worker及存储位置也需要交叉控制；该设计用于区分影响，不预先断言旧不利结果来自文件系统。

SQLite不设允许退步比例。逐负载审查跨轮方向、配对耗时、CPU、尾延迟和冷态开销；出现可重复不利就不采用，原因不明也保持原实现。区间跨1不能单独证明不下降，某些大结果集变快不能抵消短读或写入退步。PG收益同样要求工作量减少与端到端改善相互支持，不能只报某个SQL或函数变快。

## 数据保护与实施顺序

实现及功能验证在本地独立任务检出中进行。同产品共享入口保持单写，稳定版本由协调者串行集成；当前规划不分派实现者或更新其它任务状态。

后续实测仅用新UUID低权限测试库、角色、目录和合成文件库，不挂载生产业务或媒体目录，不读写生产业务表，不使用真实owner、模型或QQ。资料附件、媒体和默认文件库保持原位置，不新增生产迁移、索引重建或PG参数修改。

规模及并发压力测试先在独立环境完成。NAS只做有界的最终实机复核，使用已有容量保护与就绪状态检查，一次仅一个本号测试客户端，避开其它维护；出现核心状态变化立即停止本号负载，保留证据并清理自有资源。保护器、生产容器与其它线程资源不为跑分修改或恢复。

建议执行顺序为：**结果预算编码 → 同步派生计算 → PG剩余批读 → 隔离HTTPS评估 → 联合对比与采用决定**。HTTPS成本观察可以在前面块评估期间独立完成，但每块源码冻结、性能证据及采用决定分别记录。首批不混入锁模型、revision协议、检索算法或合同校验引擎改造。
