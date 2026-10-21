# PG召回与来源同步接续实测：2026-10-21

PG关键词读取进一步改善，但尚未达到所有场景都不慢于SQLite。Memory关键词p50从80.501降至70.911ms（−11.91%），比本轮SQLite的73.448ms快3.45%，也比原SQLite的71.989ms快1.50%。SourceSync关键词从497.388降至474.678ms（−4.57%），仍比原SQLite的449.336ms慢5.64%、比本轮SQLite的469.930ms慢1.01%；不能依靠新SQLite中位数上浮宣称全面领先。短field查询的PG仍明显更慢。

SQLite正式样本有上浮与长尾，全部保留。随后同物理库逐操作配对控制未复现约10%的稳定短读退步，但不能据此承诺所有SQLite负载零波动或零回退。本批状态为 **local_integrated_not_deployed**：Memory已本地集成，生产Q15未变，候选未部署。

## 固定版本与正式方法

比较基线 Memory `84c2f56ab28921c0008c6e4e0de48d3fe5bd40bd` 与冻结运行源码 `45d80619803a145bfa4c94108bb990d6868a17a3`，两边均为固定共享包 **0.1.2**；完整产品交付为 `c4a75d648635b2833c7bf71a74a1100e40688461`。这次original是84c，不是上批e1。两个离线镜像使用同一原e1镜像基底，分别装入固定产品wheel和同一共享wheel；每次运行核对revision、98个产品Git文件及共享包完整文件集合与SHA256。完整镜像ID、wheel和脚本/合同hash见[来源清单](pg-followup-performance-2026-10-21.sources.json)。

正式trials41/42/43，每backend/variant各三轮：`memory_sql`、`memory_source_sync`两suite × 四组合 × 三轮，共 **24次运行、4,560个正式采样**，全部正确性校验通过。每轮结果、p50/p95/p99、吞吐与原始latency完整保留于[机器可读结果](pg-followup-performance-2026-10-21.json)。汇总取三轮对应统计的中位数，不挑最快轮、不剔除异常值。下表耗时变化为`(新/旧−1)×100%`，负数更快；PG/SQ比值小于1更快。吞吐变化方向相反，完整值在JSON。

客户端为同一NAS/SSD、CPU affinity 0–7、128 shares、512MiB、单个串行客户端、pool上限4。shares不是硬CPU配额；既有PG服务资源及NAS背景负载未等量限制，不能称两数据库整体CPU/内存预算相同。正式记录的一分钟loadavg约5.14–18.43，调度波动持续存在。SQLite各次均为WAL/FULL（synchronous=2，busy_timeout=5000）；PG17.6各次fsync、synchronous_commit、full_page_writes均on。未清OS/PG缓存、降低持久性或更改生产全局参数。

初始化、原生显式迁移、种子、预热、签名/断言、SQL trace/profile均在计时外。正式Memory含300初始完整组，写后365组；30个Python命中未超过32条上限。SourceSync含300组/30来源，保留原生schema、snapshot/current/head、失效提交、revision、CAS和guard及最后业务事务；远端owner是公开合成夹具的进程内响应，不含真实Core/Platform HTTPS或模型延迟。两种负载均启用source lookup、automatic facts、memory context。Knowledge本批未变，Redis没有访问，未重测。

## 正式四组合结果

| 场景 | SQLite基线→候选p50 ms | SQ变化 | PG基线→候选p50 ms | PG变化 | 新PG/新SQ | 新PG/旧SQ |
|---|---:|---:|---:|---:|---:|---:|
| Memory关键词 | 71.989 → 73.448 | +2.03% | 80.501 → 70.911 | -11.91% | 0.965 | 0.985 |
| Memory精确field | 2.528 → 2.801 | +10.82% | 4.252 → 4.919 | +15.69% | 1.756 | 1.946 |
| Memory零预算 | 1.911 → 2.171 | +13.56% | 3.021 → 2.265 | -25.03% | 1.043 | 1.185 |
| Memory撤回后 | 34.815 → 34.825 | +0.03% | 39.790 → 34.918 | -12.25% | 1.003 | 1.003 |
| Memory完整组提交 | 36.036 → 43.547 | +20.84% | 33.893 → 37.794 | +11.51% | 0.868 | 1.049 |
| Memory来源→候选→提交 | 93.466 → 99.088 | +6.01% | 92.140 → 82.221 | -10.77% | 0.830 | 0.880 |
| SourceSync关键词 | 449.336 → 469.930 | +4.58% | 497.388 → 474.678 | -4.57% | 1.010 | 1.056 |
| SourceSync精确field | 386.565 → 390.474 | +1.01% | 409.351 → 400.675 | -2.12% | 1.026 | 1.037 |
| SourceSync零预算 | 393.911 → 392.868 | -0.26% | 581.749 → 406.610 | -30.11% | 1.035 | 1.032 |
| SourceSync撤回后 | 437.101 → 413.370 | -5.43% | 433.348 → 436.937 | +0.83% | 1.057 | 1.000 |

不能只看汇总中位数：SQLite field三轮p50原版为2.528/2.395/2.641ms，候选为2.801/2.644/12.394ms，范围虽严格分离，边缘差仅约0.003ms；候选第三轮field p95=60.965ms、zero p95=203.749ms。SQLite完整组提交中位数+20.84%、零预算+13.56%，三轮范围重叠也不能直接当作无退步。PG完整组提交+11.51%、field+15.69%、SourceSync撤回后+0.83%的中位数上浮同样保留；原版PG SourceSync zero有较长尾，−30.11%的改善含该背景，不泛化成固定收益。

## SQLite同库控制

为排除每轮随机ID、页布局和进程hashseed差异，固定读取已经完成的`memory-original-43-sqlite`同一365组物理库和guard；关键词为撤回后的10组，精确field为仍有效的`bench-10000`。两版本都使用原生公开Store构造器及select、相同moriginal43 namespace/pool4、空PG/Redis、冻结clock与PYTHONHASHSEED=0。构造器既有准备在计时外执行，构造前/后及测量后DB、guard SHA256完全相同，未手动checkpoint、迁移、恢复或改guard；原有BEGIN IMMEDIATE、权限、来源及临时FTS路径保持。hash值见JSON。

顺序控制trials61/62/63共 **6次运行、6,600采样**，与正式分母分开。field 2.710→2.845ms（+4.97%）、keyword 34.247→35.151ms（+2.64%）、zero 2.209→2.147ms（−2.79%），三轮范围全部重叠。第一轮原版有明显长尾：field p95=55.927ms、keyword p95=657.988ms，全部保留；该轮loadavg约18.40/10.40/9.24，不能将顺序差异直接归因代码。

随后逐操作控制为 **3个block、3,300对、另6,600采样**；每block exact/zero各500对、keyword100对，另有每case十对预热，不计入采样。两个已加载reader各512MiB/CPU0–7、network none，一次只有一个活跃调用；每case每block的old/new先后次数各半，逐操作交替。命令解析、签名核验和输出在计时外；墙钟包含原生请求构造/select，CPU计时包围该调用及相邻时钟开销。所有ready内容签名、DB/guard hash一致，关闭后仍一致。

| 配对case | 三block p50中位数：旧→新 ms | p50变化 | 成对wall比率变化 | 成对CPU比率变化 |
|---|---:|---:|---:|---:|
| exact | 2.771 → 2.759 | -0.44% | +0.97% | +0.68% |
| zero | 2.160 → 2.151 | -0.41% | +0.27% | +0.50% |
| keyword | 34.226 → 34.016 | -0.61% | -0.59% | -0.30% |

成对比率先对每对计算新/旧，再取每block中位数及三个block中位数，因此不等于两组p50的比值。exact/zero仍有约+0.97%/+0.27%的成对wall差、+0.68%/+0.50%的CPU差；这支持“同库控制未复现正式约10%级稳定短读退步”，不支持“全部负载零回退”。该控制只覆盖撤回后同库短读，不能替代完整30命中语料、来源同步或写入回归。

## 改善归因与未采用实验

本次产品改动限于PG路径：批读取128增至512，合并当前语句的source lookup/revision/recovery核验，目录查询按namespace过滤，减少词级查询重复计算和无用文本传递。计时外native adapter计数在三轮一致：Memory关键词 **20→14**条，SourceSync关键词 **71→45**条、精确field **61→43**条；Memory field仍12条。SQLite四项trace的command计数与去值shape均一致，但trace含FTS内部/trigger回调，不是物理I/O，也不能代替时延证据。PG计数不含驱动启动、隐式协议或SQL函数内部语句。

独立catalog探针的20次客户端中位数为0.9116→0.45325ms；对应EXPLAIN规划0.515→0.162ms，执行0.107→0.051ms。新计划先按目标namespace定位，再用`pg_class_relname_nsp_index`的relname+relnamespace条件；旧view计划仍连接pg_index与所属表。`pg_indexes.indexdef`虽为重建索引定义字段，COUNT可裁剪该投影，**不能称省掉了每次indexdef重建**。这些是隔离库单项诊断，不能直接分摊正式端到端耗时。[PG17 pg_class](https://www.postgresql.org/docs/17/catalog-pg-class.html)、[pg_indexes](https://www.postgresql.org/docs/17/view-pg-indexes.html)、[EXPLAIN](https://www.postgresql.org/docs/17/using-explain.html)说明字段和计划含义；planner成本单位不是毫秒。

数组评分SQL实验在七类合成查询中分数等价，但broad/OR分别约慢9.12%/9.41%，**未采用**。rank-lab的candidate指实验SQL，不能混称冻结产品45d；正式运行源码始终45d8061。trial40原生诊断、trial50 cProfile，以及catalog/rank实验和两类SQLite控制均不混入正式24次/4,560采样。

## 正确性、保护与交付边界

四组合核对完整语义字段和完整组成员签名，忽略随机ID及组顺序；不将该签名等价冒称跨后端逐次排序一致。启用真实临时PG环境的相关测试集合 **159/159通过，其中96条原生PG、63条本地或SQL double**，不是159条全原生PG。原生负例包含六个实时probe、513组批边界、BM25 oracle、同事务DML/DDL、guard/CAS等。SQLite完整集合 **1,540通过、97跳过、1个既有失败**（总1,638）；`test_confirmation_rejects_mismatched_authority[account-403]`为既有400/403差异，前批已在原e1独立复现，未在本卡隐去或扩大修改。精确用例和来源见JSON的verification。

不削弱权限、撤权或来源新鲜度，不缓存权限结论；锁后新语句快照、失效先提交→revision核对→业务事务及guard-before-commit仍由产品保留。普通完整组合成语料已启用事实/上下文功能，但不能代表所有自动事实历史、模型、并发规模或实际远端耗时；三轮范围重叠不是统计等价证明。

协调者已完成生产足迹与清理：10个core服务生命周期/镜像/完整mount内容不变，129个非目标容器已采集的id/image/mount/status/policy快照字段一致，5个TLS健康；未采集全部非目标容器重启计数，不据此宣称其所有生命周期均不变。settings hash、容量配置与guard代码不变，mount列表仅排序差异已排除。测试未挂生产业务数据或媒体、未访问Redis；临时PG库/角色、测试容器、stage及3个专属image tag均已清理，生产镜像引用未移除。生产Q15保持，未发布候选。报告写者仅读取协调者给出的安全结果文件并作数据/文稿核验，没有另连NAS/PG、读取私有配置或重复运行测试。

正式harness `a020fe552055dfb33afc71c07eaf50a5b8088f94`，同库控制 `f0018667528151d51f29974728be4195e4751fff`，配对worker `e284a12cfdaa0fe68837a6c9df7d36c517a5db82`，三份来源manifest独立保留。报告JSON与sources均为LF；sources记录原始安全证据文件的原bytes hash，嵌入内容重排为公开JSON，不冒称与原文件字节相同。脚本Git blob hash已重新核对，文档JSON的交叉SHA在提交暂存blob再次核验。方法见[基准说明](../../scripts/benchmarks/pg-read-performance/README.md)，交接见[TS-257](../handoffs/TS-257.md)；[上批报告](pg-read-performance-2026-10-21.md)单独保留，不混分母。
