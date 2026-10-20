# PG读取优化的四组对照

这些负载延续TS247，比较原版/候选版分别使用SQLite和PostgreSQL。代码导入固定镜像中的已安装产品包；公开合成夹具从原版e1da946的Git对象按AST导出，所有版本用相同夹具和合同。协调者在NAS创建UUID独立数据库、受限角色和独立合成文件目录后，通过私有stdin传递连接配置。脚本拒绝非`ts_pgopt_`测试库，不使用Redis。

每个负载接受`--config /bench/private.json --backend sqlite|postgresql --variant original|optimized --trial N`，可加`--smoke`。私有配置包含`database`、`postgresql_url`、空`redis_url`，以及精确`original_revision`/`candidate_revision`。优化版在计时前核对安装文件与候选Git字节证明。实际backend必须符合请求，初始化、建表、迁移、种子数据和SQL诊断均在计时外。

`memory_sql.py`保留TS247的300组初始数据、完整组提交和来源→候选→提交边界，新增写入不命中Python，确保30个关键词匹配均在默认32条上限内。测关键词、精确field、零预算和原生来源撤回后的召回；返回签名核对完整组成员和语义字段。该组使用原生LocalFixtureSources，不能代表真实来源同步的完整耗时。

`memory_source_sync.py`增加真实SourceAuthority的生产调用链：同样300组、30个合成来源，执行完整snapshot/current/head、来源关系、修订、guard和业务事务。远端owner响应复用e1公开SyncHarness，传输由内存合成响应替代，仍验证公开请求/响应schema。它衡量本机权威同步和数据库成本，不包含实际Core/Platform的HTTPS或上游延迟。撤回后保留所有历史来源覆盖，断言已撤回field不再返回。

正式Memory两类负载均通过产品原生、显式备份的迁移启用来源索引、automatic facts与memory context；PG从同一合成SQLite权威初始化。四组都断言这三个功能标志为1，将现有功能的检索/连续性开销计入两种后端。较早调试样本使用最小schema 3，仅作定位，不混入正式三轮。种子仍为完整普通来源组，不把这些结果冒称为所有自动事实历史或语义模型负载。

`knowledge_sql.py`使用200个普通块、两个合成文件，测单块与20块检索、全新Application调用和5块导入。单独加入3段依赖闭包与256字节预算，必须完整返回或完整省略。签名保留引用、版本、全文hash、来源、spans、provenance与omissions，仅剔除各轮不同的导入时间；同时保存排序签名与无序内容签名。原版/优化版在同后端核对顺序，跨后端核对内容。每次调用仍执行原生权限、配置与文件新鲜度检查。

Knowledge另外计量原生受限HTTP/ASGI单块和20块请求，保留序列化、准入、逐请求应用态与真实后端操作；TestClient只在测试容器内部运行，不发布端口，不包含外部网络/TLS耗时。

三轮串行交错执行四个组合；所有原始样本保留。`p50/p95/p99`与`ops_s`都按操作本身计时，断言和签名计算的额外wall时间单独记录。单个客户端、同一NAS/SSD、相同CPU亲和范围0–7、128 CPU shares和512MiB内存；内核不支持CFS quota，报告不得称为硬CPU限额。早期首候选/CPU诊断固定CPU7；随后该核背景空闲降到7–9%，最终四组统一允许调度到0–7，避免单核背景抢占偏差。这些早期结果单独保留，不混入最终三轮。没有清OS/PG缓存、修改持久性或调全局数据库参数。`fresh_application`只指进程内新应用对象，不代表物理冷缓存。

额外的未计时PG调用记录native adapter SQL数，不记录参数和正文。该诊断不包括驱动启动、隐式BEGIN或SQL函数内部语句，也不是跨后端语句数比较。SQLite另做一次未计时trace，覆盖该操作新开的普通及来源只读连接，仅保存command计数和去值规范化后的语句shape哈希；保留trigger重复回调，不将它当作物理I/O。它辅助定位SQLite变化，不能替代三轮时延对照。

候选装入固定版本的共享适配器时，计时前同时核对Memory和tianshu-storage的已安装文件hash；原版保持其原wheel不变。容器只挂测试目录，不挂生产配置、数据或媒体。完整镜像、夹具、合同、脚本hash及清理回执由本批交付记录保存。

`knowledge_mature.py`是主36次完成后的补充研究，三轮四组复用各自trial23的既有251块，仅测单块/20块和对应HTTP检索。它不重新导入、不手动ANALYZE或修改参数，保留完整原生授权、两阶段配置/文件校验；记录PG当时的自动统计时间。12次/1,440采样与主研究分开，不能混用两个阶段的分母或将差异全部归因于统计。原始结果和脚本Git字节分别保存，追加研究不替换任何原始样本。
