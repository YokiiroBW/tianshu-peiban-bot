# PostgreSQL 运行链路与后续优化研究（2026-10-21，TS271）

本研究的结论是：本号 300 组合成来源同步的主要已见成本是 Python 文档校验，短查询更敏感于连接握手和多次协议等待，写入还受独立 guard 与持久提交的串行成本影响。三者需要分别优化，不能用一个 SQL 的服务器执行时间解释整条业务链。

本文的调研作者只做代码、隔离诊断和主要来源分析，不操作NAS、凭据或生产；实测由协调者独占执行。原型代码冻结为协调仓库 `16700d966e88fd9b2b80473105713e5b21f5764d`；共享存储原型为0.1.4，Memory为完整 `e5e1b394a8f6d318dda4d9280587e599fd64c653` 历史、运行源码 `4447bb4`。最终36次比较、SQLite真实写入配对与追加原生PG写后对照均已完成，连接原型不采用，根保留0.1.3；具体数字见[主报告](pg-system-performance-2026-10-21.md)。Memory业务仍归TS267，本文没有实施其后续建议。以下文件链接用于定位，精确语义以所列冻结Git对象为准；0.1.4实现仅保留在实验分支。

驱动运行版本锁定为 psycopg 3.3.6。在线官方文档当前显示开发版，本文只使用固定依赖中已有且本号已核对的 pipeline、prepare、pool 和 pgresult 接口，不依赖新版本功能。

## 1. 从一次查询到返回业务结果

PostgreSQL 要经过客户端发送、解析/重写、规划、执行和返回结果；连接池主要复用已建立的会话，不会自动消除每条命令的处理和消息交换。本项目 SQLite 分支直接调用进程内 `sqlite3`，不经过 libpq 与独立 PG 后端的协议交换。对于少量已命中缓存页的简单 SQL，PG 省下的执行工作未必抵得过这些固定成本；PG 的事务、并发和集中持久化能力也不等于单线程单短 SQL 必定快于 SQLite。[PG17 查询路径](https://www.postgresql.org/docs/17/query-path.html)

可以把端到端时间拆成以下几部分；这是本项目的成本模型，不是可直接相加的 EXPLAIN 字段：

`业务时间 = 排队/锁等待 + 借用与绑定 + Python校验/转换 + 协议与服务器工作 + guard持久化 + 提交确认`

### 借用连接与当前绑定

[Database.connect / StorageRuntime._checkout](../../packages/tianshu-storage/src/tianshu_storage/runtime.py) 从 pool 借出 raw connection，每次重设 `search_path`、`lock_timeout` 和 `default_transaction_read_only`，再读取当前安装、逻辑库与 ready 状态。一个 pool 会服务多个逻辑 schema，也会交替使用只读/可写模式，所以这些动态值不能只在物理连接新建时设置。psycopg 的 `configure` 发生在新连接准备阶段；改放那里不能替代本项目的逐次重设。[psycopg pool 生命周期](https://www.psycopg.org/psycopg3/docs/advanced/pool.html#connections-life-cycle)

本轮候选在私有 raw pipeline 中依次发送设置和绑定两条语句，全部确认后才 fetch、核对绑定并返回业务连接。绑定表包含完整 schema，函数和等号操作符限定到 `pg_catalog`。只有这两个内部模板使用原本的自动准备策略 `prepare=None`；初始化无绑定路径和业务 `native/read_batch` 仍是 `prepare=False`。自动准备复用计划，不缓存设置参数、绑定行或权限；DDL/search_path 变化需要重新分析计划。[PG17 PREPARE](https://www.postgresql.org/docs/17/sql-prepare.html)、[psycopg prepared 策略](https://www.psycopg.org/psycopg3/docs/advanced/prepare.html)

`_checkout_schema_sql` 的 256 项有界缓存只保留 schema 派生的不可变 SQL/quoted path，减少重复 Identifier/Composed 构造。timeout、read_only、当前安装元组和实际 SELECT 结果都不在缓存中。设置错误仍属于 `postgresql_unavailable`，绑定 SQL 错误沿 `postgres_error`，当前元组不符仍为 `postgresql_binding_mismatch`；公开设置 cursor 的成功 `pgresult` 用于识别延迟错误阶段。没有把业务事务、权限查询或锁后 metadata 移进这个握手。[公开 Cursor.pgresult](https://www.psycopg.org/psycopg3/docs/api/cursors.html#psycopg.Cursor.pgresult)

### SourceAuthority 的三段事务

[MemoryService.operation](../../projects/tianshu-memory/src/tianshu_memory/service.py) 进入 [SourceAuthority.operation / barrier / _commit_observations](../../projects/tianshu-memory/src/tianshu_memory/source_authority.py)。常见无重试路径具有三个本地事务；多批次、owner head 变化或 CAS 重试可能增加工作，不能把“三个”当成所有情况的上限。

| 阶段 | 实际工作 | 必须保留的边界 |
| --- | --- | --- |
| T1：本地起点 | 读取本地 m0、coverage；按需核验当前账号与 scope | 读取发生在本次锁确认后，不拿上次操作的结果代替 |
| T1 结束后：owner 核验 | Core snapshot → Platform current access → Core head；多个 selector batch 要对齐 owner heads，必要时重新读取 | 网络调用没有持有 T1 数据库事务；owner 事实和访问仍逐次核验 |
| T2：同步落账 | 在锁后重新核对 m0、coverage、viewer；CAS 后构造 SourceSnapshot，检查并应用当前观察，持久化失效与撤回 | 所有批次对齐后才应用，来源负向结果先落账，不能只提交通过的一部分 |
| T3：业务操作 | 重新读取 local revision 并比对同步结果，再核验 viewer，执行查询/消费/候选提交等 | 不能把 T2 之前的授权或来源判定当成 T3 的当前状态 |

常见路径中的“六次 fresh revision”是本地 `SourceAuthority.revision` 的六次新鲜读取：T1 的 m0 与 coverage 各一次，T2 的 CAS、coverage 与 `_apply` 返回结果各一次，T3 再一次。它们不是六次远端 RPC。单个 selector batch 的基础远端序列是上表的三次；多批次、head 刷新和业务 prepare/check 会另增调用。

[Store.transaction](../../projects/tianshu-memory/src/tianshu_memory/store.py) 对 PG 排队确认 `SET synchronous_commit=on`、`BEGIN` 和 schema 级事务 advisory lock，然后另发 metadata SELECT，验证当前 guard。[Memory dialect](../../projects/tianshu-memory/src/tianshu_memory/postgres.py) 的锁用于串行化数据库权威与外部文件检查点。READ COMMITTED 每条普通 SELECT 使用命令开始时的快照，因此锁获取返回后再启动的 SELECT 才能看到前一写者提交；把两者塞成同一个 SQL/CTE 不能据此证明获得锁后的新快照。[PG17 事务隔离](https://www.postgresql.org/docs/17/transaction-iso.html#XACT-READ-COMMITTED)

### 授权语料、BM25 与完整组

[recall_index](../../projects/tianshu-memory/src/tianshu_memory/recall_index.py) 先检查 lineage、revision/epoch、admission 和 suppression，来源准入后才批读正文，再检查完整成员与 projection；只有整体有效的组进入当前授权语料。现有 PG 路径按 512 组分块；超过边界继续分块/回既有路径，不能截掉后续组或提前读取失效正文。

[postgres_recall.rank](../../projects/tianshu-memory/src/tianshu_memory/postgres_recall.py) 一次发送该次调用的完整授权 corpus，在 MATERIALIZED CTE 中计算 vector、词频、长度和 corpus 统计，返回 ID/score，正文由本次输入映射恢复。这里复用的是同一调用内的计算结果，不是跨请求来源缓存。`MATERIALIZED` 能控制重复求值，也可能增加内存和妨碍条件下推，是否更快仍要结合实际计划。[PG17 CTE 物化](https://www.postgresql.org/docs/17/queries-with.html#QUERIES-WITH-CTE-MATERIALIZATION)

本产品的 BM25 依赖当前授权 corpus 的文档总数、平均长度和文档频率。不能先只保留命中文档，再把缩小后的集合当完整 corpus；也不能直接用包含隐藏、撤回或旧版本内容的持久搜索表计算全局统计。PG 提供的 `ts_rank/ts_rank_cd` 有自己的相关性规则，直接替换并非保持现有公式的纯性能优化。[PG17 文本搜索排序](https://www.postgresql.org/docs/17/textsearch-controls.html#TEXTSEARCH-RANKING)

### guard 与数据库提交

在业务代码返回后，Store 再读 metadata。检查点确有变化时，[source_recovery.persist](../../projects/tianshu-memory/src/tianshu_memory/source_recovery.py) 写临时文件、flush/fsync、替换外部 guard，然后才提交数据库。若 guard 已前进而提交被中断，后续验证会失败关闭；不能把文件写到 commit 后面换速度。没有权威变更的读事务不需要每次重写 guard，但仍验证它。

PG 同步提交等待事务 WAL 持久化后才确认；它不要求每次事务把所有 heap/index 页面都刷完，后者还受 checkpoint 管理影响。因此 `driver_commit`、guard fsync 和 checkpoint 相关 I/O 是不同来源，不能合称一次磁盘写。[PG17 同步/异步提交](https://www.postgresql.org/docs/17/wal-async-commit.html)、[PG17 WAL 与 checkpoint](https://www.postgresql.org/docs/17/wal-configuration.html)

## 2. 现有证据支持什么

### 300 组诊断显示的成本归属

以下取自协调者私有结果 `results/memory_flow_profile-original-402-postgresql.json`，Memory revision 为 `e5e1b39`，公开合成 owner 响应，没有真实远端 TLS、模型或 QQ 延迟。它是带 hooks 的一次归因诊断，`diagnostic_only=true`，不是正式中位数样本；通用外层 `measurement_mode=timed` 不能覆盖这一标记。

| 合成业务流 | 本地 checkout 次数 | Contracts.validate 次数 | 校验独占 wall / 整段归因 wall（ms） |
| --- | ---: | ---: | ---: |
| source_exact | 3 | 8 | 187.632 / 236.772 |
| source_keyword | 3 | 8 | 175.368 / 284.692 |
| source_zero_budget | 3 | 8 | 188.266 / 241.324 |
| source_commit | 4 | 12 | 215.381 / 345.551 |
| source_full_flow | 10 | 28 | 654.666 / 965.552 |

8/28 是该 fixture 中的合同文档校验次数，不是 SQL 次数或远端 RPC 数。零预算仍进行来源与权限核验，不能用预算为零绕过这些步骤。source_exact 的 `driver_execute` 独占 wall 为 13.704 ms；即便把该计时桶理想化地全部消除，也无法解释约 188 ms 的校验成本。这只用于排序研究方向，不能算成可实现的提速上限或正式收益。

另一次独立 cProfile 调用中，source_exact 观察到 16,204 次 validator `evolve` 和 11,281 次 Resource 创建；热点含 `descend`、类型检查、`properties` 与 `anyOf`。cProfile 自带开销，且累计时间含子调用，不能把这些函数累计时间再相加。相同目录的 401/404 是 20 组 smoke，不能拿它们与 402 的 300 组数字做前后性能比较。

[memory_flow_profile.py](../../scripts/benchmarks/pg-read-performance/memory_flow_profile.py) 采用互斥 exclusive 桶解释总耗时，并在新版补充 inclusive 桶观察 checkout 等完整嵌套段。inclusive 互相重叠，不能相加；较早 402 没有 inclusive 字段，不能把 checkout 的约 0.243 ms exclusive 值冒称完整借出耗时。driver execute/fetch 也同时含驱动工作与等待，不能把其全部标为服务器 CPU 或网络延迟。

### 本轮候选的反证与修正

配对与正式负载还有一项需要单列的历史差异：`checkout_paired` 在 SQLite 写好300组再导入PG，`memory_sql` 则先导入空库，再在PG原生写300组、30次提交与35次全流程，达到365组后读取。Memory `postgres.prepare` 在复制数据完成后才安装 `source_revision` 行级触发器，因此前者不具有后者反复更新 metadata 行的 MVCC/HOT 与统计历史。零预算同样读 metadata/accounts/scopes，不能只用多65条正文解释其退化。这个历史差异是可检验的假设，尚不是已证实的回退原因。

两种配置都只向 Store 传 database_path 与 storage_backends，未配置 role_grants_database_path；实际都直接走 MemoryService.select、LocalFixtureSources 和 Store.transaction。正常 COMMIT 后在 IDLE 状态调用 rollback 会直接返回，不能笼统说成每次回池都会清 prepared 缓存。双方读前都有大量借用，业务 SQL 仍 prepare=False、binding 无参数，也不能未经证明归因于冷准备或业务 generic/custom 计划。

追加的 `checkout_sequence_compare` 先导入空 schema，再经PG原生写入365组，以同一pool/guard旋转旧版、当前pipeline与仅去除raw.pipeline上下文的顺序方法。100次关键词预热使它属于稳态因果诊断，不能冒充正式约46次关键词读取后的全部表现。顺序方法保留完整限定、计划策略、逐次会话设置和绑定核对；binding失败时设置已独立提交，与旧顺序实现一致，未验证连接仍不会交给业务。若精确与零预算的wall/CPU不能跨block稳定改善，就停止继续扩展这个方向，保留正式反证。

第一候选 `4bcf336` / wheel 前缀 `aaf9d1b0` 保持正确性，但把两个握手模板也设为 `prepare=False`。同进程、同 pool/schema、300 组的交替配对得到 checkout+SELECT p50 比 1.2767；两个顺序应用原型中短 field 约 4.32→5.22 ms。原始镜像、样本和失败结论保留。省去一次等待不足以抵消该 Python/驱动路径和准备策略变化，不能凭 pipeline 原理直接宣布加速。

返修 `0b9fdfa` 只恢复两个完全限定内部模板的自动准备，并限定等号操作符；冻结 `16700d9` 再缓存 256 项不可变 schema SQL/path。26 个原生节点已由协调者通过，覆盖 dirty session、跨 schema、只读切换、prepared 阈值、临时表遮蔽、绑定更新/删除/重建和错误阶段。协调者提供的第二原型同 pool 配对中，授权 exact 比 1.0048、zero 比 0.9997，接近持平；这不是最终冻结的 36 次正式比较，不能据此宣称应用提速或 SQLite 零退步。

### EXPLAIN 能回答和不能回答的问题

PG17 的 `EXPLAIN (ANALYZE, BUFFERS, WAL, SERIALIZE TEXT, FORMAT JSON)` 可以分别观察真实执行、页访问、WAL 产生和服务端输出序列化。默认不测输出转换，即使启用 SERIALIZE，也不向客户端发送原查询结果，所以它不包含真实结果传输、客户端 Python 行适配、合同校验、pool 排队、外部 RPC 或 guard 文件 fsync。[PG17 EXPLAIN 的 SERIALIZE/WAL 说明](https://www.postgresql.org/docs/17/sql-explain.html)

EXPLAIN 适合解释一个服务器计划，不能代替 native API 的端到端 wall/CPU。DML 的 ANALYZE 会实际执行；用于诊断的隔离回滚副本应与正式真实提交样本分开。回滚诊断不证明 commit、guard 或关闭重开后的持久内容正确。

## 3. 已实施、本轮候选与四个后续完整块

已实施的 TS248/256/264 等轮次包括：有界完整组批读、事务内 SourceSnapshot、独立 SELECT 的 read_batch、一次调用内事实准备、集合式 BM25、PG bump 的 UPSERT RETURNING 和来源无变化写入跳过；Contracts 也已按不可变 registry 缓存顶层 validator 与静态引用目标。不能把这些再列成 TS271 新完成事项。历史实测边界见 [TS264/265/269 报告](pg-write-performance-2026-10-21.md)。本轮候选只改变共享 checkout 的准备和等待成本，没有实现下列业务优化。

### 优先 1：继续消除 Contracts 的不可变准备成本

**证据与归属。** [contracts.py 的 _prepare_validation / static_ref / validate](../../projects/tianshu-memory/src/tianshu_memory/contracts.py) 已有 registry 身份失效、1024 项静态 ref 解析缓存及按 reference 复用 validator；仅建议“缓存 validator”会重复已有能力。剩余热点发生在对当前输入逐层 `descend/evolve`、构造资源/上下文和组合分支时。

**可验证假设。** 在既有 Contracts 模块内，研究能否通过已有公开扩展方式，为确定的静态本地 ref 进一步准备可复用的 schema/解析上下文，减少与输入无关的对象构造，同时每次仍遍历完整当前文档、执行 format、分支与关系规则。registry 更换即重建；动态、歧义、外部 ref 和其他方言沿原官方路径。不得缓存某文档已通过、某 owner head 已授权或某 receipt 仍有效的结论。

**验证与停止。** 用现有 source exact/zero/full-flow 的大小文档、registry 切换和合法/非法文档对照错误顺序、schema/instance 路径、formats、不可解析引用及降级行为，再用无 profiler 的相同业务样本比较 CPU/wall。若热点仍是必须执行的实例检查，或减少构造必须依赖私有 validator 状态、改变错误/方言行为，就停止该方案，保留当前实现；不引入另一套校验引擎。该块可能同时改善 PG 与 SQLite，不能全部归因于数据库。

### 优先 2：沿依赖层继续 batch，而非把整条操作装进 pipeline

**证据与归属。** [postgres_source_snapshot.py](../../projects/tianshu-memory/src/tianshu_memory/postgres_source_snapshot.py) 已在 CAS 后、首次 DML 前批发独立 SELECT；[共享 read_batch](../../packages/tianshu-storage/src/tianshu_storage/postgres.py) 已确认全批成功才返回有序 cursors。召回则有 lineage/admission → 有效组正文 → projection/fact → 授权 corpus 的依赖。

**可验证假设。** 在上述已有边界内，把同一层、参数已知且不依赖彼此结果的剩余读取送到现有 read_batch；必须拿到上一层结果才能选出下一层 ID 时，保留同步点。一条 SQL 能准确表达读改写时才采用服务器集合操作；本轮以前的 bump/UPSERT RETURNING 已是这种模式。PG 官方给出的 `SELECT x` 后客户端加一可改为 `UPDATE x=x+1` 的例子与此相符，但不能据此把本产品跨 owner、权限和 guard 的整个流程搬成一个 SQL。[PG17 pipeline 使用场景与读改写例子](https://www.postgresql.org/docs/17/libpq-pipeline-mode.html#LIBPQ-PIPELINE-WHEN)

**验证与停止。** 除少量组，还用跨 256 selector、512 group 边界和大结果集，核对撤源、全域 receipt 冲突、NULL alias、完整组、projection 失效和中途 SQL 失败不返回半批。保留六次本地 fresh revision 和锁后独立语句。测协议等待与服务端 JSON/数组解码、内存、Python 适配的总变化；若少了等待却增加更大 CPU/尾延迟，或只有很小的 SQL 等待桶，就停止扩张，不重复把已批量化部分再包装。

Denis Laxalde 的原作者工程案例将大量参数化写入排入 Psycopg pipeline，并显式启用 prepared，以展示减少往返的模式；这是本任务最贴近的外部案例。案例使用 UNLOGGED 演示表，其时间不是本产品基准，不能照搬其持久性设置或把结果比例外推到这里。[Pipeline mode in Psycopg，2024-05-08](https://www.psycopg.org/articles/2024/05/08/psycopg3-pipeline-mode/)

### 优先 3：把 DML、revision trigger、WAL 与 guard 分开测

**证据与归属。** SourceAuthority `_apply` 已比较当前 payload/revision/state，跳过不变的 observations、physical、admission 和 owner head 更新；真正变化仍多次 DML。[postgres.py](../../projects/tianshu-memory/src/tianshu_memory/postgres.py) 为权威表安装逐行 revision trigger，每行又更新 metadata 中的 revision。PG 的 UPDATE 产生新行版本，部分变更还涉及索引；HOT 有明确的索引列和页空间条件，不能凭“已有索引”推断没有写放大。[PG17 HOT](https://www.postgresql.org/docs/17/storage-hot.html)

**可验证假设。** 对已经完成当前授权、冲突与来源规则验证的变更，在原 SourceAuthority 所属位置按表研究集合 DML；先测 SQL/trigger 次数、WAL bytes/fpi、metadata 更新和 guard/commit 时间。集合执行可以减少客户端等待与反复规划，但仍可能触发同样数量的行级 trigger，不能把批量化自动等同于消除 WAL 或全局 revision 更新。[PG17 行级/语句级 trigger](https://www.postgresql.org/docs/17/sql-createtrigger.html)

**验证与停止。** 覆盖同批重复 key、约束冲突、receipt 全域冲突、旧 revision、失效广播、完整组和异常后整体回滚，并进行实际提交、关闭重开和内容/guard 核对。共享 executemany 当前逐条经过 dialect，具有 total_changes、rowcount、lastrowid、savepoint 和错误前缀语义，不能直接替换为 Psycopg 原生批量 API。改变 trigger 粒度还会改变 revision 演进，必须先追踪消费者，不能默认按事务只加一次。若主要时间在不可省略的 guard/WAL 刷盘而不是多次 DML，就停止以 SQL 批量化解决全部写入延迟；保持 fsync、synchronous_commit、full_page_writes 和 SQLite WAL/FULL。

### 优先 4：并发优化先追调用链，不能直接解锁

**证据与归属。** SourceAuthority 的 `_operation_lock` 是实例内 RLock，覆盖同步、远端核验和后续业务，目的是避免操作互相推进全局 revision 后反复使 CAS 失效。Store 的 schema 级事务 advisory lock 则跨连接/进程串行化独立 guard 与权威变更，两者职责不同。普通 PG MVCC 不会替应用维护这份文件协议；增加 pool 连接数不能自动越过 RLock 或 advisory lock。[PG17 advisory lock](https://www.postgresql.org/docs/17/explicit-locking.html#ADVISORY-LOCKS)

**可验证假设。** 在已有隔离诊断里先分别观察请求排队、pool 等待、RLock 等待、数据库锁持有、owner RPC 与 CAS 重试，追踪所有 `MemoryService.operation` / `Store.transaction` 调用方实际是否写入。读 API 名称不证明没有消费、receipt、审计或其他权威变化。只有发现可证明的只读边界和实质等待，才研究既有 Store/SourceAuthority 内更细的共享读或操作范围；不能直接按 person 分锁，因为物理撤回会影响其他 actor，owner heads 和 revision 仍有全局关系。

**验证与停止。** 用两个 actor、同一 physical 的撤回广播、授权变化、owner head 前进、跨进程 guard、提交中断与 CAS 重试限度做并发对照，保留调用数量、失败率及尾延迟。psycopg 的一个 connection 上多个 cursor 仍串行执行，也共享会话/事务，给同一连接多加线程不能代替这项分析。[psycopg 并发说明](https://www.psycopg.org/psycopg3/docs/advanced/async.html#concurrent-operations)

若当前证据无法证明 reader 与 guard 的一致性，或放开操作锁只增加 CAS 重试和不可用，就停止并发改造，保留锁。该块不是本号已实现功能，不新增并行队列、Redis 权限缓存或用户确认流程。

## 4. 如何作出本轮与下一轮决策

先完成冻结候选的既定正式比较：相同 Memory/依赖闭包、相同合成初态、交替 PG/SQ、真实写入及重开；独立记录短读、来源同步、撤回、零预算、纯提交和完整写入。诊断 hooks、cProfile 和 EXPLAIN 只解释原因，不进入正式延迟样本，也不以回滚或幂等命中代替写入。

之后按上面四块逐项使用现有调用链做原型，不恢复旧卡或同时改 TS267。采用依据是相同受影响行为的内容、权限、来源和持久性保持，且目标场景出现可解释、可重复的实际改善；这是本任务的判断方法，不是新增覆盖率、百分比或 CI 门槛。相关验证已通过且代码未变时复用结果，发现新失败或实质疑点才补查。若没有稳定收益、只转移瓶颈或改变语义，就保留反证并停止该方向；不为“PG 必须处处快于 SQLite”继续无限扩张改动。
