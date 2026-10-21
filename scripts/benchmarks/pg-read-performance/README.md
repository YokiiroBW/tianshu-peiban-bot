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

## TS-265 写入接续

本批original改为本地集成Memory `c4a75d648635b2833c7bf71a74a1100e40688461`，共享包固定0.1.2；候选精确版本仍由私有配置给出，双方安装proof继续核验。TS257已经完成，其报告和方法事实不改。原`memory_sql.py`/`memory_source_sync.py`/`common.py`保持a020字节，仍可做两suite三轮四组合24次/4,560采样，涵盖短field/关键词/零预算/撤回、LocalFixture完整组commit与来源→候选→commit，以及完整SourceAuthority读取。

`memory_source_write.py`是独立补充suite，复用同样300组/30来源、原生Store/migration/Contracts/SourceAuthority/SyntheticOwners/TrustedWorkflow，测`source_sync_complete_group_commit`和`source_sync_source_candidate_commit`。纯commit的源和pending job在计时外准备；full-flow包含原生scope probe、consume和commit，不跳过来源同步。每case30次，full-flow另有原有五次真实预热，实际写后365组；三轮四组合另12次/720正式采样，不能改写原读suite语料或混用24次分母。提交后关闭并重开Store，核对ledger及原生field选择签名，均在计时外；`--smoke`为20组、每case3次。调用参数与原suite相同。

SQLite写控制入口为`sqlite_write_control.py`，四case为`local_commit`、`local_full_flow`、`source_commit`、`source_full_flow`。LocalFixture和SourceAuthority分开，后者保留真实barrier/合同/guard/CAS，仅远端owner响应为进程内公开合成夹具。默认每模板300初始完整组（smoke20）；纯commit模板另有一个已原生准备的pending job，full-flow模板尚无目标source/event/job。输出seed_groups、seed_source_count、实际source_count及复制bytes/hash，不能把小smoke泛称全部写负载。

四个mode都先经过现有`common.arguments`的安装proof和测试库名称校验；以下命令均还需通用参数`--config /bench/private.json --backend sqlite --variant original|optimized --trial N`，并固定`PYTHONHASHSEED=0`。所有控制均显式空PG/Redis、同writecontrol namespace/pool4，拒绝环境URL或namespace覆盖；容器中的专属工作挂载固定为`/bench/write-active`。

1. `--mode seed --case local_commit`：协调者把一个**新的空模板目录**绑定到`/bench/write-active`，由原生API种子/准备一次，关闭并重开验证guard与构造器no-op，封存DB/guard/合成harness状态和`snapshot.json`。对四case分别做一次；模板随后只读，不覆盖。模板有显式原生迁移备份，但克隆只复制封存清单；若出现未纳入的FileStore或storage状态文件则拒绝，不静默遗漏。
2. `--mode prepare --case local_commit --source /bench/write-control/templates/local_commit --destination /bench/write-control/pairs/b1/local_commit/p0001`：目的地必须全新。普通复制到original/optimized两目录，每文件及目录fsync，核对同一DB/guard及状态文件bytes/hash。不是hardlink、事务rollback或共享可变库。原生准备、复制和验证全在计时外；复制可影响OS缓存，因此不称物理冷缓存。
3. `--mode run --case local_commit`：将一侧专属克隆绑定到`/bench/write-active`，只执行一次原生真实写入。计时前核对封存SHA、ctor字节不变、schema/features/FULL/WAL，确认pending job尚无ledger；计时结束后要求group/record/ledger增长、guard revision增长，DB/guard字节变化，并关闭/原生重开核验ledger、完整组field签名、quick_check/FK。stdout一行`event=sample`含wall/cpu纳秒、source owner_reads、前后hash/计数及重开证据；synthetic packet/result保存在专属`result.json`供verify使用，不输出正文。
4. `--mode verify --case local_commit`：只核对该侧已提交结果，**不重放写命令**；stdout`event=verified`。SourceAuthority原生核验可能在计时外持久同步，单列核验前后证据，不禁止或隐去。准备/重开或核验失败均不产生成功写样本。

`--mode run --stream`可保持两个只读rootfs的容器/热Python与Contracts。先输出`event=ready`；stdin每行`{"case":"local_commit"}`（或其余case）对当前新克隆执行一次并完成所有close/验证后输出sample，`{"case":"stop"}`输出closed。每条命令新开/关闭原生Store，不持SQLite连接等下一条。私有driver负责在**两个worker均无打开连接**时，将下一对全新克隆放到各自专属工作挂载；绑定目录inode应保留，不能靠重命名宿主绑定目录改变容器所见目录。模板和历史结果保持，上一对不能再次run；初态SHA将拒绝已用副本，不能把幂等ledger命中或空commit当预热。

建议正式控制3个block×每case20对，四case合计240对/480真实提交，先后次数按case/block各半；预热也使用独立克隆并单列。始终一次一个活跃调用，旧/新内部绝对DB与guard路径相同、冻结clock/hashseed相同，资源保持CPU0–7/shares128/512MiB。每对**初态**物理bytes/hash必须一致；原生UUID可令提交后bytes不同，比较持久化业务增量及语义签名，不伪造ID。所有异常值和未达标结果保留。该控制覆盖固定普通完整组写入，不代表一切自动事实、并发或真实远端网络负载。NAS驱动、采样、生产足迹与清理由协调者单写，不将私有驱动入库。

`--mode run --validation-profile`（也可加 `--stream`）仅用于独立诊断。默认不启用，原计时分支、sample 字段和 stream 协议消息保持。启用时仍走同一个 `run_one` 的封存初态、WAL/FULL、真实提交、关闭重开和语义证明；同一个热 Contracts 继续由 stream 复用，预热与四 case 交错仍由既有 driver 安排。示例为在上述通用参数后添加 `--mode run --stream --validation-profile`，stdin 命令不变；不允许用于 seed / prepare / verify，也不混入正式性能统计。

诊断只在实际 commit / full_flow 计时窗口内启用 validate 观察和 GC 计数，安装 / 还原 hook 在计时外，异常也恢复原实例属性及本工具的 callback。每个 sample 额外含 `validation_profile`：`diagnostic_only=true`、`clock=process_time_ns`、`validate_calls`、不重复累计嵌套调用的 `validate_cpu_ns`、`by_contract[合同名]={calls,inclusive_cpu_ns}`，以及 `gc={collections,cpu_ns,by_generation,unfinished_collections}`。合同分项为包含式 CPU，嵌套时不可相加；总 validation CPU 和 GC CPU 都属于整段 process CPU，GC 可能与 validation 重叠，不可把这些数相加。GC CPU 是本 callback 观察到的 start→stop process CPU，未完整结束的 collection 单列；不强制 collect、不调阈值、不禁用 GC。没有缓存文档、通过结论或来源 / 权限判定，也不导出参数或正文。计数器本身有开销，诊断耗时不能替代无 hook 的正式结果。

## TS-271 共享连接层与运行链路研究

本批固定 Memory 完整历史 `e5e1b394a8f6d318dda4d9280587e599fd64c653`、运行源码 `4447bb4`，不改 Memory 业务代码。original 使用共享存储 0.1.3；optimized 使用本批冻结的 0.1.4。两种镜像的 98 个 Memory 包文件一致，分别核对 8 个共享包文件与依赖闭包；候选测试轮只调整 Memory wheel 的依赖元数据和 RECORD，以装配新共享包，未修改产品仓库的依赖锁定。精确 wheel、源码、镜像和脚本 manifest 见同号报告。

正式研究保持现有三个 Memory suite 的计时与校验边界：三轮、四组合共 36 次，读取/普通写入两 suite 共 4,560 个正式采样，独立 SourceAuthority 写入另 720 个。SQLite 同物理初态控制采用三 block、每 case 每 block 10 对，共 120 对/240 次正式真实提交；另 24 对/48 次预热提交。规模与上批不同，不能合并分母或宣称重用了上批结果。

`memory_flow_profile.py` 另建相同规模的普通来源及 SourceAuthority 夹具，对短 field、关键词、零预算、纯提交、完整消费提交分别归因。hook 仅覆盖当前调用线程；exclusive 桶互斥，inclusive 桶嵌套重叠，不可相加。driver execute/fetch 包含等待；SQLite 的内部执行计入应用桶，不能直接把两种后端的 driver 桶相比。另一次 cProfile 调用只输出函数位置、调用数和时间，不导出参数、正文或原始 profile 文件。该 suite 强制 `profile_only`，返回 `diagnostic_only=true`、空正式 `results`，不计入三轮收益。

`pg_execution_profile.py` 只读取相应 `memory_sql` 已完成夹具，在原生只读事务内核对 schema 并选择当前仍有效且确有记录的 field。对绑定、metadata、候选、records 与 lineage 的固定 SELECT 使用 `EXPLAIN (ANALYZE, BUFFERS, WAL, SETTINGS, SERIALIZE TEXT, TIMING OFF, FORMAT JSON)`，输出去值后的计划结构与统计，不执行 DML、手动 ANALYZE 或参数修改。它同样强制诊断模式；服务器计划与执行耗时不包含完整应用、连接池和客户端网络成本。

`checkout_paired.py` 将旧版本 Git 字节导出的两个 checkout 方法和候选方法交替绑定到同一进程、pool、schema、guard 及产品夹具，每个 case 三 block×100 对。方法切换在计时外，测 checkout+SELECT、原生授权 field 和零预算；每次仍读取当前绑定和原生来源权限，不缓存业务结果。保留全部 wall/thread CPU 样本、每 block 中位数和前后 guard hash。这项因果控制与正式四组合分开；原生 SourceAuthority 链仍由正式 suite 覆盖，不能用本地来源夹具替代远端同步结论。第一候选变慢的样本及修订候选的 CPU 代价均须保留。

`checkout_sequence_compare.py` 用于解释因果控制与正式短查询差异：先导入空 schema，再在 PG 原生写入 300 组、30 次纯提交与35次全流程，得到365组并做100次未计时关键词预热。此前 `checkout_paired` 是 SQLite 写满300组后导入PG，二者不具有相同物理/MVCC历史。新诊断在同 pool/guard 上旋转旧版、当前pipeline、仅去除当前 `_checkout` AST 中 `raw.pipeline` 上下文的顺序模板三种方法；其余设置、完整限定、计划策略和新鲜绑定相同。每 case 三 block×60个trio（另每block6个预热trio），保留全部wall/thread CPU及AST hash。它明确是临时方法替换的独立诊断，未修改已安装候选，不冒称新的产品版本或混入正式样本。

## TS-276 来源校验配对诊断

本批沿用现有suite，对照Memory完整Q18基线`4dbb816`，共享包两侧均固定0.1.3。通用`5b50579`版停止于15次/2660采样并保留反证；来源限定`226d9c8`版36次/5280采样独立封存。最终分配修订`eb65763`使用独立24次/4560个短召回和来源同步采样，再独立执行SQLite持久写入/读取与下列CPU诊断；226版12次来源变化写入不能冒称最终eb版结果。SQL结构的有界消融没有稳定业务收益，原exact SQL保持。正式样本、顺序pilot、SQL诊断与以下同库配对分开保存，不合并分母。

`source_validation_paired.py`复用`source_full_flow`的独立300组合成封存模板，验证hash后复制到全新目录；PG从副本导入新UUID namespace，属于导入历史，不是来源正式套件原生300组的同一物理初态；普通`memory_sql`正式读取则为365组。CLI必须显式提供config/backend/template/directory/contract-directory/baseline-contracts/baseline-sha256/output，并调用既有`verify_installation`核对完整已安装候选与共享文件。旧Contracts从指定Git blob导出并逐字节核对。没有生产路径或数据库默认值。

单进程中保持同一Store、SourceAuthority和合成owner，只分派唯一Contracts对象的`validate`：新旧delegate经正常构造，各有独立锁和准备缓存，每次从当前已验证的不可变schema/registry完整校验当前输入。其他业务、关系规则、权限、六次fresh revision、三个owner调用、CAS和guard均走原路径。默认每后端三block×每case20对，field、零预算和关键词轮换并平衡先后；首block各case三对预热单列。关键词要求封存数据中全部预期匹配组完整返回；300组模板为30组。前版只有field/零预算的120对结果独立保留，最终增加关键词以核对正式轮次观察到的SQLite来源关键词上浮，总计每后端180对/360诊断样本及9对预热，不混为前版协议。每次核对完整返回、schema调用计数及前后行数/revision/guard，不允许选成功子集。

该脚本强制`diagnostic_only=true`，以JSONL逐条flush开始、样本、配对结果及异常。只导出hash、计数和固定错误代码，不导出文档正文；全部wall、process CPU与validate CPU保留。validate CPU属于整段process CPU的一部分，不能相加。模板复制、开库、delegate构造和状态核对在计时外；这些额外读取会影响缓存状态，故不能用它的绝对延迟代替正式镜像比较。两种后端保持同步持久性，结束关闭Store并还原hooks。
