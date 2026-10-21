# PG读取优化的四组对照

这些负载延续TS247，比较原版/候选版分别使用SQLite和PostgreSQL。代码导入固定镜像中的已安装产品包；首批以e1da946为基线并从其Git对象按AST导出公开合成夹具，本批TS257以84c2f56为基线、从84c导出夹具，两边使用相同夹具和合同，各阶段精确来源以对应manifest为准。协调者在NAS创建UUID独立数据库、受限角色和独立合成文件目录后，通过私有stdin传递连接配置。脚本拒绝非`ts_pgopt_`测试库，不使用Redis。

每个负载接受`--config /bench/private.json --backend sqlite|postgresql --variant original|optimized --trial N`，可加`--smoke`。私有配置包含`database`、`postgresql_url`、空`redis_url`，以及精确`original_revision`/`candidate_revision`。当前a020冻结脚本对original和optimized两种镜像都在计时前核对revision及产品/共享包完整安装文件的Git字节证明；不能沿用首批仅候选有proof的历史例外。实际backend必须符合请求，初始化、建表、迁移、种子数据和SQL诊断均在计时外。

`memory_sql.py`保留TS247的300组初始数据、完整组提交和来源→候选→提交边界，新增写入不命中Python，确保30个关键词匹配均在默认32条上限内。测关键词、精确field、零预算和原生来源撤回后的召回；返回签名核对完整组成员和语义字段。该组使用原生LocalFixtureSources，不能代表真实来源同步的完整耗时。

`memory_source_sync.py`增加真实SourceAuthority的生产调用链：同样300组、30个合成来源，执行完整snapshot/current/head、来源关系、修订、guard和业务事务。远端owner响应复用e1公开SyncHarness，传输由内存合成响应替代，仍验证公开请求/响应schema。它衡量本机权威同步和数据库成本，不包含实际Core/Platform的HTTPS或上游延迟。撤回后保留所有历史来源覆盖，断言已撤回field不再返回。

正式Memory两类负载均通过产品原生、显式备份的迁移启用来源索引、automatic facts与memory context；PG从同一合成SQLite权威初始化。四组都断言这三个功能标志为1，将现有功能的检索/连续性开销计入两种后端。较早调试样本使用最小schema 3，仅作定位，不混入正式三轮。种子仍为完整普通来源组，不把这些结果冒称为所有自动事实历史或语义模型负载。

`knowledge_sql.py`使用200个普通块、两个合成文件，测单块与20块检索、全新Application调用和5块导入。单独加入3段依赖闭包与256字节预算，必须完整返回或完整省略。签名保留引用、版本、全文hash、来源、spans、provenance与omissions，仅剔除各轮不同的导入时间；同时保存排序签名与无序内容签名。原版/优化版在同后端核对顺序，跨后端核对内容。每次调用仍执行原生权限、配置与文件新鲜度检查。

Knowledge另外计量原生受限HTTP/ASGI单块和20块请求，保留序列化、准入、逐请求应用态与真实后端操作；TestClient只在测试容器内部运行，不发布端口，不包含外部网络/TLS耗时。

三轮串行交错执行四个组合；所有原始样本保留。`p50/p95/p99`与`ops_s`都按操作本身计时，断言和签名计算的额外wall时间单独记录。单个客户端、同一NAS/SSD、相同CPU亲和范围0–7、128 CPU shares和512MiB内存；内核不支持CFS quota，报告不得称为硬CPU限额。早期首候选/CPU诊断固定CPU7；随后该核背景空闲降到7–9%，最终四组统一允许调度到0–7，避免单核背景抢占偏差。这些早期结果单独保留，不混入最终三轮。没有清OS/PG缓存、修改持久性或调全局数据库参数。`fresh_application`只指进程内新应用对象，不代表物理冷缓存。

额外的未计时PG调用记录native adapter SQL数，不记录参数和正文。该诊断不包括驱动启动、隐式BEGIN或SQL函数内部语句，也不是跨后端语句数比较。SQLite另做一次未计时trace，覆盖该操作新开的普通及来源只读连接，仅保存command计数和去值规范化后的语句shape哈希；保留trigger重复回调，不将它当作物理I/O。它辅助定位SQLite变化，不能替代三轮时延对照。

首批原版保留其原wheel；本批TS257两种镜像均离线安装固定共享适配器0.1.2，在相同原e1镜像基底分别装入84c/本批候选产品包，计时前分别核对Memory和tianshu-storage的完整已安装文件集合与hash。容器只挂测试目录，不挂生产配置、数据或媒体。完整镜像、夹具、合同、脚本hash及清理回执由各批交付记录保存。

`knowledge_mature.py`是主36次完成后的补充研究，三轮四组复用各自trial23的既有251块，仅测单块/20块和对应HTTP检索。它不重新导入、不手动ANALYZE或修改参数，保留完整原生授权、两阶段配置/文件校验；记录PG当时的自动统计时间。12次/1,440采样与主研究分开，不能混用两个阶段的分母或将差异全部归因于统计。原始结果和脚本Git字节分别保存，追加研究不替换任何原始样本。

## TS-257 接续诊断

本批的 `original` 指本地集成的 Memory `84c2f56` 与固定共享包 `0.1.2`，不是上批 `e1da946`；`optimized` 指本批另行指定的候选。两种镜像均须保留 `/usr/share/ts249-candidate.json`，分别匹配私有配置的 `original_revision` / `candidate_revision`，且产品及共享包完整文件集合与 SHA256 都必须符合 proof。输出 `verified_revision` 记录已核验版本，旧路径名仅为复用现有打包器。

仅 `memory_sql.py` 和 `memory_source_sync.py` 接受 `--profile-only`，需使用独立 trial 和独立合成目录，例如在原命令上加 `--trial 140 --profile-only`。它们建立相同种子、迁移和必要写入状态，保留结果签名、预算和撤回校验；不执行正式读取采样，也不输出任何正式 `results` 样本，`measurement_mode` 为 `profile_only`。Memory 写入路径仍执行原来完整提交及五次预热加正式次数的流程，以保持随后查询的语料相同，但这些写入不计入性能结果。

该模式分别调用一次关键词、精确 field、零预算及撤回后的关键词查询，输出 `untimed_python_profiles` 的前20个 self/cumulative 调用位置、次数、毫秒数，不导出参数、局部变量、正文或原始 `.prof`。完整语料的前三项在撤回前剖析。cProfile 只覆盖当前线程；这里两种负载都直接执行原生 `MemoryService` / `SourceAuthority`，不把它冒称为 HTTP worker 剖析。其默认时钟包含驱动等待和剖析开销，`process_cpu_ms` 是全进程 CPU 时间；不能将 self time 当成纯数据库执行耗时。

现有未计时 PG native / SQLite trace 继续独立运行，诊断模式另外记录零预算统计。它们与 cProfile 是不同调用，不能将不同调用的耗时相减来计算网络或 Python 净耗时。正式三轮不传 `--profile-only`，其 workload、样本数、计时边界及正确性校验保持；诊断结果单独保存，不混入正式三轮。本批仅复测两种 Memory suite，Knowledge 暂不重跑。

`sqlite_same_fixture.py`另做同物理库控制：两镜像仍经 `common.arguments` 核验安装 proof，固定 `PYTHONHASHSEED=0`，仅允许 `/bench/runs/memory-original-43-sqlite/config.json` 与其既有365组SQLite库/guard；不因控制trial或variant另建库。保留原 `moriginal43` namespace和pool4，显式空PG/Redis且拒绝环境变量覆盖，使用原生公开 `Store.from_config` 和 `MemoryService.select`，复用Harness请求构造及冻结clock。构造器既有准备在计时外执行，但必须在构造前、构造后、测量后核对DB和guard的SHA256完全不变；非空残留WAL或任一字节变化立即失败，不手动checkpoint、迁移、恢复或改guard。每次选择仍执行产品原有事务、权限和来源校验，关键词原有临时FTS不改。

控制查询在事务内从当前有效groups/lineage/sources选一条field，核验精确一组、零预算空、撤回后Python十组的语义签名；精确/零预算各500次、关键词100次（`--smoke`各3次，另有原有五次预热），输出3个独立case及schema/features/durability、同库三阶段hash。协调者以原版/候选各三轮串行运行，源manifest与结果单列，不能混入正式24次/4,560采样；这用于排除随机ID、页布局和进程hashseed差异，不替代正式四组合，也不代表物理冷缓存。示例参数仍为 `--config /bench/private.json --backend sqlite --variant original --trial 61`，候选改variant并使用独立输出文件；控制固定读原trial43的同一个库。
