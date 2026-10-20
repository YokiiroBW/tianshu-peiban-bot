# TS248 / TS249：按原理优化PG，并保持SQLite性能

用户最终目标是PG优于或至少持平SQLite，并要求SQLite自身性能不下降。Redis继续承担已规划的可丢弃缓存职责，本批不把Redis GET与持久数据库的完整业务读混为同一负载。媒体、原文件和FileStore继续留在文件库，数据库只承担既有结构化数据、配置与引用。

PostgreSQL是独立服务，连续的小SQL会支付协议往返与解析成本；SQLite在应用进程内执行，这类成本低得多。PostgreSQL官方说明pipeline可降低小操作的往返开销，但有依赖的操作不能任意并行。本批先消除逐组查询和逐行写入，用有界集合SQL完成已授权候选的来源、正文与投影读取。SQLite继续采用适合自身的惰性短路和原FTS路径，避免为了PG把所有正文提前读入。[PG pipeline](https://www.postgresql.org/docs/17/libpq-pipeline-mode.html)、[SQLite优化器](https://www.sqlite.org/optoverview.html)

全文检索的倒排索引用于定位词项；排序仍要计算文档长度、词频和语料统计。PG现有持久全文表已经有GIN，简单再建同类索引无法消除BM25对每个候选反复统计整个当次授权语料的成本。优化将授权语料的文档数、平均长度和词项文档频率一次计算，再集合评分，并用真实PG验证旧公式逐分等价。排序使用的候选仍先过主体、可见性、来源与完整组校验。[PG全文索引](https://www.postgresql.org/docs/17/textsearch-indexes.html)、[SQLite FTS5](https://www.sqlite.org/fts5.html)

PG共享缓存和操作系统缓存都可能让热查询从内存完成；它不必每次读物理磁盘。本批不改生产shared_buffers或持久性参数，重点减少重复工作与连接开销。Knowledge复用有受控生命周期的Store，HTTP每请求仍创建独立应用请求状态，共享连接owner按完整配置和有效存储配置失效；活跃请求结束前不关闭其资源。每次dispatch仍重新授权，并保留capture→文件读取→重读配置/授权→revision/seal复核的两阶段流程。[PG资源管理](https://www.postgresql.org/docs/17/runtime-config-resource.html)

MVCC快照保证数据库内可见性，无法替代本项目远端来源同步或文件现场校验。SourceAuthority的完整历史/墓碑覆盖、远端head稳定性、负面事实先提交、local revision复核，以及Store的schema/guard、writer锁和guard先于数据库提交顺序均保留。没有把请求结果缓存成可跳过撤权的结果，也没有以mtime或TTL代替文件hash。[PG MVCC](https://www.postgresql.org/docs/17/mvcc-intro.html)

验收采用“优化前/后 × SQLite/PG”四组，同NAS、同SSD、同合成数据、同权限与来源边界。保存每轮p50/p95/p99、吞吐、结果签名、CPU亲和与负载；PG额外记录未计时SQL调用数来解释收益。独立来源同步场景与原TS247的LocalFixture场景分开列示，文件库不挂入测试。若SQLite出现可复现退步，先定位修正；无法达到PG持平的场景须如实报告，不能靠省略校验或改变业务含义达成数字目标。PG EXPLAIN的计划成本不包含全部客户端和网络开销，因此最终以端到端业务读的实测作为结论。[PG EXPLAIN](https://www.postgresql.org/docs/17/using-explain.html)

Redis官方也强调客户端、往返和pipeline会显著影响基准。该原理用于解释当前缓存职责；用户已把本批性能比较对象更正为SQLite，因此不增加Redis生产访问、ACL修改或Redis对照。[Redis基准说明](https://redis.io/docs/latest/operate/oss_and_stack/management/optimization/benchmarks/)

客户端取行成本也计入端到端时延。本批锁定的psycopg 3.3.6在每次读取description时创建Column对象；共享适配器原先逐行重复读取、创建列名和位置映射。0.1.1按实际PGresult复用同一结果集的纯列元数据，旧Row保留自己的不可变映射，换结果或关闭即释放结果标识，公开description仍实时读取。None row_factory直接返回tuple；既有SQLite连接分支保持原样。42项安装版真实隔离PG及SQLite/Redis共享测试通过，计时对照继续验证收益，不把分配次数直接当作整体速度提升。

排名进一步仅聚合命中的查询词频，并复用本次Python已经持有的授权正文；完整文档长度、平均长度和DF的语料范围不缩小。同一PG业务事务的事实过滤、对象识别与排名可显式复用一次原始/解码快照，新事务重新读取。这些资源只在本次查询内使用，不保留权限或业务查询结果。

后续同机剖析仍看到协议等待，因此0.1.2用psycopg pipeline批量发送同一连接的三个会话SET，以及事务启动的SET、BEGIN和advisory lock。它们保持服务端执行顺序，退出pipeline并确认锁后，再用独立语句取得新的metadata快照，避免把等待锁之前的快照误认为锁后的状态。提交、guard持久化及业务写入仍按原顺序执行；pipeline不代替事务，也不减少权限校验。[psycopg pipeline](https://www.psycopg.org/psycopg3/docs/advanced/pipeline.html)、[PG MVCC](https://www.postgresql.org/docs/17/mvcc-intro.html)

SQLite的相同scope比较仍逐组进行，但相同请求范围只编码一次。PG Knowledge把同一阶段的两键metadata和项目状态合成一次查询；第二阶段仍重新查询并校验文件。纯查询文本的有界转换缓存只保存占位符转换结果，不保存参数、数据、权限或查询结果。

统一的CPU亲和、shares和内存限制作用于测试客户端。SQLite随客户端运行，PG服务沿用既有部署和背景负载；这不意味着两种数据库整体的CPU/内存预算相同，也不是独占服务器峰值压测。结果用于判断当前NAS部署中的业务延迟。

PG优化器还依赖表统计选择查询计划，默认自动维护会在装入或更新数据后异步更新统计。主研究刚导入即计时；运行后发现自动统计已更新，故追加既有数据的三轮热检索参考，保留原样本且不手动ANALYZE。两阶段的缓存、块数和背景负载也不同，没有前后同刻计划，不能把变化全部归因于统计。此处只读计划探针的临时设置退出事务即恢复，没有调整生产参数。[PG ANALYZE](https://www.postgresql.org/docs/17/sql-analyze.html)、[统计维护](https://www.postgresql.org/docs/17/routine-vacuuming.html#VACUUM-FOR-STATISTICS)
