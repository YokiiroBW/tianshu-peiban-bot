# 天枢统一宿主与异步联动

本文件记录整合目标、现有链路核查及本轮落实边界。单业务宿主已在隔离本地实现，并通过真实安装 wheel 的六应用联合启停、配置发布/读取、普通与流式模型替身、SDK 及回执验证；没有构建 Linux 镜像或部署。交付状态见 [本批交接](../handoffs/TS-340.md)。早期多进程打包原型已退出发行源码。

## 事实来源与范围

本地静态追踪基线：Platform f4bbdb23795e0f9d89345aed98c09906fb24031f，Companion 677fcb1f1392c134084d6e2be0d61f1a49f757b4，Memory 110a41bf851163901f7fc4a8f13cc19817285a3a，Gateway 5ff98947f7eaf82384d359ca84ad581a5cf113f0。漫画插件候选及后续调度修正以本批交接的精确提交为准，不能用候选代码冒称已上线。

覆盖所有第一方功能领域的入口、模块归属及外部边界见 [领域核查](../development/single-container-domain-review-2026-10-27.md)。本文件进一步追踪调度、数据库、外呼、回调及恢复。AssetLibrary、Chat Audit、SnowLuma、HA、媒体服务器、真实模型和账号仍是各自的外部能力，不因天枢打包而接管它们的实现或数据。

## 目标：统一宿主，不建立全局业务大队列

宿主只拥有配置装配、模块生命周期、内部调用、资源预算、健康与关闭顺序。Platform、Companion、Memory、Knowledge、Gateway 继续拥有各自的业务规则、事实、授权、来源与回执，不跨模块直接读写数据库。

内部调用采用明确注册的模块接口，不为这五个模块启动 TCP 监听。外部 AstrBot 等平台通过正式适配协议接入；用户网页与外部 API 使用统一接入层。漫画辅助保留独立 UID 的子进程，通过本地 Unix Socket 接入现有插件系统。Vector、Loki、Prometheus、Grafana 是成熟观测引擎，在完整替代功能之前保留其进程与正式接口；合并业务模块不等于删除时序、检索和告警能力。

统一业务进程也不能让所有同步 I/O 挤在一个事件循环里。现阶段需在明确的存储归属执行上下文中保留连接线程约束，通过有界执行器处理阻塞工作；不能简单把现有共享 SQLite connection 传给任意 to_thread。CPU/GPU 或外部程序的重工作继续由所属 worker 执行，宿主线程不直接执行转码、模型推理或大文件解析。

复用现有 aiohttp 和 ASGI 入口的阶段，可通过进程内适配保留原合同、鉴权、准入和流式语义，不走网络。该阶段仍有部分合同编解码成本，不能宣称所有 JSON 和 HTTP 适配代码已消除。后续直接领域接口的抽取必须在原归属处完成，不能全局 monkeypatch ClientSession、按 URL 猜测本地目标、伪装 TLS，或以导入另一模块的内部对象绕过原授权。

## 现有主链路与必须保留的顺序

### 聊天、送达与记忆

1. 网页或渠道插件接收入站，保存原事件与稳定 ID。Platform 完成原会话、作者、角色、策略及来源检查。
2. Core 接收请求时先完成 origin/identity 等外呼，再进入短接受事务，保存 admission、collector、queued turn 及幂等回执。请求返回接受，不等模型生成完成。
3. Core 后台按会话推进 turn：查询 Memory context → 获取/验证 bundle → 占模型执行名额 → 发送前再次核验 → Gateway 流式生成。
4. 分段送达按原 turn/reply/attempt 顺序落账，Platform 调渠道 SDK，实际发送回执回到原账本。超时或异常不能直接当作未发送并重发。
5. 原 outbox/material 进入学习链；抽取 proposal 先持久化，再提交 Memory。提交结果不明时先查同一 key 的 receipt，不能再次生成或重复消费来源。

证据：Companion `source_sync.py:663–790`、`core.py:1107–1240,1531–1676,1924`、`delivery.py:83`、`memory_learning.py:667,739,1044–1082,1307–1340`；Platform `web_dialogue.py:182–263`、`sources.py:780`；Memory `memory_context/application.py:107`、`segments.py:640`。

### 生活、日程、写作、图像与提醒

Companion 现有七条独立循环：Core tick 50ms、MemoryLearning 1s、Life 2s、Images 2s、Writing 5s、Proactive 2s、Direct 0.5s。它们是独立任务，不等于底层数据库、线程、模型或发送资源已经完全隔离。

|链路|触发、执行及后续|必要顺序与隔离|
|---|---|---|
|生活日程|角色时区与 schedule → 当前生活阶段/持久 generation task → 后台模型 → 日计划、阶段、日记或影响记录|同任务按版本/阶段推进；不同角色不应因管理入口的全局锁而互等。读取生活投影不能启动另一份计划。|
|活动|原 activity.save/pause/resume/cancel → Companion 所属活动/步骤账本 → 后台执行 → 阶段结果|暂停、取消和恢复使用原版本规则；任务完成后再产生后续状态，不根据计时器猜成功。|
|写作|持久写作任务/章节 → generation selection → 后台模型 → 章节与回执|按原任务及章节推进；不与聊天共用一条串行工作队列。|
|图像|对话工具或后台生活机会 → 意图/工作流分析 → ComfyUI submit/poll → 原图像结果与送达|分析的 selector、执行身份、模型名额必须一致；聊天已持模型 slot 时复用它，不能再申请自身名额造成自等待。ComfyUI 的 GPU 工作另计。|
|提醒/目标/动机|持久 subject/subscription → 到期 candidate → quiet/来源/授权复核 → expression → delivery attempt/receipt|不伪造入站 turn；只有已装配且支持合同的 dispatcher 才能称真实送达。failed/unknown 保留原显式恢复语义，不自动二次发送。|
|关系与经验|实际记忆/互动事实 → 原关系活动/经验记录 → 到期过滤或惰性结算|关系冻结不衰减；原 read/settle/history 的幂等结算不另开全库定时扫描。|

“日程”目前分属 Life 生活阶段、Proactive 提醒机会及 Platform 插件 interval/cron；不能称已有一个完整的全系统日历总队列。Platform Task Center 当前是只读聚合，Companion、assets、dialogue 的任务合同仍标 not_connected；Workbench 部分近况/事项为静态占位。

证据：Companion `app.py:682–713`、`life.py:267–314,403–467,643–780`、`proactive.py:12–49,112–150`、`image_catalog.py:174–177,500–517`、`runtime_execution.py`；Platform `web_life_management.py:282–351`、`tasks.py:99–141`、`plugins/scheduler.py:131–174`、`LifecyclePanel.tsx:74–124,158–198`。

### 内容、媒体、插件与设备

|链路|真实归属与持久推进|互不干扰的范围|
|---|---|---|
|Memory context|候选发现 → 来源屏障 → 最终事务 bundle；prepare/verify 不重新跑模型|embedding/rerank 在来源锁或事务外。bundle、权限和来源保持原有效期与再验证。|
|Memory 学习|outbox/material → segment/job → 冻结 batch/window → proposal → write_ledger|来源消费与结果原子提交；proposal 复用、未知先查回执，不再抽取。|
|索引|派生文档/缺失向量 → 事务外 embedding → 再核来源 → 写向量/游标|每批4向量，pending 0.1s/idle 5s；不把索引当事实库。前后台检索预算尚须接通。|
|Knowledge 原文/项目|上传/抓取/读取/目录 → owner/client+request/key → 外部读取/解析 → 再授权/版本 CAS → 结果|网络、文件和 Git 不放入长写事务；目录按项落进度；保留 KnowledgeStorage 代际租约。|
|插件计划|interval/cron → plugin_schedules → invoke 接受 → plugin_calls → next_run/pending|同实例 action 串行，实例之间并行；跳过/合并和 queue_one 沿原设置。失败计划不能饿死后续计划。|
|角色装配|apply/resume → staged role row → Core 暂停 → Memory 授权 → Knowledge 授权 → Core 启用 → verify|同角色的阶段与取消顺序保持；不同角色只在短的共享目录更新处互斥，远端等待不占全局角色锁。|
|媒体订阅/下载|家族扫描 lease → media job/dedupe → owner/generation claim → 下载/发布/AS及库核验 → 成品回执|book、comic 各专用 worker；video/audio 共可配池。相同目标路径排他，不同资源并行；扫描家族独立。|
|音乐/有声书|音乐 request/publication ledger；有声书 part attempt/checkpoint → TTS/合成 → 成品|按供应商/账号速率与文件目标控制；有声书本身串行，不占聊天模型队列。unknown 不盲重放。|
|基础设施|节点任务/到期 → snapshot/inventory/stats → cache；控制操作 intent → submit/reconcile → receipt|2个观测 slot、每 node lock；控制按 node/stack/container 资源锁，同 sent operation 重启只查状态。|
|HA 控制|client_id intent → owner/lease CAS → observe/re-authorize → 发送一次 → receipt|同 intent 去重，SENDING 接管只观察。不同 intent 目前不按 entity 排他，不能宣称同设备所有命令已有严格 FIFO。|
|BOT 观察与回复|SDK 队列 → 分账户 batch → 来源/归档/回复 admission → ACK → reply claim → SDK send/status → ACK|poll 与业务 batch 分离以续租。legacy adapter loop 仍逐连接串行，慢 legacy 连接会影响同 loop，需在统一宿主中显式隔离。|
|观测|全量源日志 → 位点/持久缓冲 → guard/去敏协议验证 → Loki；Prometheus → 15规则/Grafana|保持完整检索、ACK/回压、补传和720h，不能用容器最近日志预览替代。|

证据：Memory `memory_context/read_v2.py:695`、`memory_context/preparation.py:73`、`memory_reindex.py:137`、`relationships/ledger.py:133`、`knowledge_content.py:252`、`knowledge.py:534`；Platform `media/runtime.py:204–223`、`media/repository.py:500–590,650–699`、`infrastructure/runtime.py:102–285`、`infrastructure/control.py:383–519`、`home.py:683–856`、`bot_observation.py:1594–1744`、`server.py:801–831`。

## 调度约束：并行在资源边界内，串行在真实冲突处

1. **顺序不等于全系统排队。** 同会话回复、同角色配置、同插件实例 action、同目标文件发布及同设备控制 intent 按原语义排序；无关角色、会话、实例、文件、节点继续并行。设备不同 intent 的顺序尚未定义，不擅自补成已实现的保证。
2. **数据库可以并发，必要原子边界保留。** PostgreSQL 的并发能力不代表能删除业务锁。Memory SourceAuthority 的全局 FIFO/RLock 与 schema 的 memory-authority transaction lock 共同保护联合来源证明和恢复 checkpoint，本轮不能直接缩成每 scope 锁。SQLite WAL 允许读写并行，但同库同时一个写者。
3. **事务只覆盖本地原子修改。** 来源、模型、下载等外呼放事务外；外呼后重新检查当前权限、版本和来源。跨模块流程用原持久阶段和回执衔接，不做跨库长事务，不持有数据库写事务等待模型。
4. **短锁不跨未知等待。** 注册、取任务、版本校验、落账是短临界区；模型、HTTP、磁盘长工作不占应用全局锁。SourceAuthority 例外属于已证明的来源证据区间：虽无 DB 事务，却仍跨同步外呼持权威锁，需先减少通信成本及传播截止时间，再量测，而不是提前释放改变证明。
5. **回调必须可达。** Companion → Memory → Companion facts 与 Platform current 是真实读回调。Actor 阅读还会回到 Knowledge。不能把整模块请求放单队列并等子调用，也不能让 Memory 同步任务占满唯一执行器后再等待其上的回调。保留身份预留槽、Knowledge 独立 client 与 actor-memory 独立 lease。
6. **共享预算由真实执行者持有。** 复用 Gateway 原 global/provider admission、interactive reserve 与公平轮转；改变请求 header 不会改变已认证 caller 的 workload class。前台聊天、后台生活、自动学习、索引需在组装时使用一致的 selector、执行身份和资源类，不能各套一个“最大并发”后互相绕过。
7. **事件只有明确归属与消费者。** 复用原 request_id、event_id、idempotency_key、correlation_id 与领域 outbox。某一事实完成后才产生下一步骤的 intent；不让任意 memory.updated、日记或生活投影广播成新触发，避免相互触发成环。读取投影不产生业务事件。
8. **超时不等于工作已经停。** 未开始的排队工作可取消；已开始的同步写必须保留容量到真实结束，并回查原 receipt；外部发送结果不明保持 unknown。不能通过提前放回 slot 或重新生成换取表面吞吐。
9. **公平且有界。** 保留 Core ModelSlots 的前后台配额/轮转，Gateway 的 provider 配额，Memory 的身份预留和后台 phase 轮转；每域慢工作只占本域容量，资源耗尽通过原可恢复状态/回压表达，不无限创建 task 或积压内存。
10. **关闭先停止接受，再排空。** 先停止新计划和入口，等待/取消已接纳调用及流，按原 finally 落 receipt，再关闭 callback 端口、连接池、日志与 Store。不能先关 Memory authority 或 Gateway ledger 让在途回调失去结果；进程异常重启从领域阶段恢复，不能把所有任务自动重放。

## 本轮已识别的具体问题与实施归属

|问题|本轮处理|
|---|---|
|插件 tick 在整轮之外捕错，坏计划可每轮挡住后面的计划|TS-338：单项隔离，保留 due/receipt/misfire/overlap。|
|角色管理全局 asyncio.Lock 覆盖跨产品 staged workflow|TS-338：按 actor 串行；每次取得锁后重读当前行；共享目录短临界区，不跨 await；有界恢复。|
|自动 MemoryLearning 本地用 background slot，Gateway 却使用前台 companion 身份|TS-339：组装一致的后台 selector/client/slot，保原无后台配置兼容边界。|
|图像分析 selector 使用 Life 背景 caller，却由前台 Gateway 身份执行|TS-339：按实际触发来源显式选择匹配端口；保留已持 slot 的复用。|
|Core 同步 SQLite/PG 事务在事件循环，锁等待可暂停7条循环|统一宿主设计与实际存储改造的前置问题；不能用简单 asyncio.gather 或随意 to_thread 宣称已解决。|
|索引与前台 retrieval 同 credential/独立 Semaphore(4)，不计入 chat provider ledger|在统一 Gateway 所属预算中接通，需真实分类与生产者/消费者联合验证；尚未实现。|
|来源屏障的同步外呼、3轮重试没有统一剩余截止预算|复用原 deadline/取消/unknown 语义改造，保留联合证明；尚未实现。|
|统一 app 的 factory/config/env、流式桥接与排空仍有缺口|TS-334–337：不轮流改 os.environ，不全缓冲 SSE，不让一个全局 executor/queue 自等待；尚未实施完成。|

### TS-338 精确本地范围

Platform 当前隔离分支 codex/unified-platform-20261027；只改 plugins/scheduler.py、role_runtime.py、必要局部 helper/相关测试及 docs/handoffs/TS-338.md。保留短事务、稳定 request key、peer expected_version、单 Platform owner 前提；不增加跨进程分布式角色锁或产品门禁。核对坏计划不影响后项、跨角色进度、同角色取消/重放/恢复顺序。

### TS-339 精确本地范围

Companion 当前隔离分支 codex/unified-companion-20261027；只改后台模型端口的组装、MemoryLearning、图像分析所需局部调用及相关测试、docs/handoffs/TS-339.md。不得改聊天事实、账本、来源、生产身份/权限或 ComfyUI 协议。核对选择身份与执行身份一致、真实前后台 slot、一旦持 slot 不重入申请、原未配置后台能力的明确 fallback。

## 集成时实际验证的场景

复用现有测试与明确夹具，对受改代码验证，不新增全仓门禁。验证至少覆盖受影响的这些相互作用：聊天与后台生成同时进行；后台持续积压仍可推进；两角色同时配置而同角色 apply/cancel 不乱序；一个插件计划失败后下一项仍执行；Memory 来源读回调可重入；慢数据库不堵网页/无关调度；发送超时保持原 unknown；同 key 重试不重复模型/发送；取消只释放真正结束的容量；重启继续原阶段；停止时流式与同步任务先收尾再关资源。

当前仅有静态链路证据、已交付的局部日志/插件改造及正在进行的 TS-338/339；没有统一业务宿主的 Linux/Docker 构建或实机性能结果。单容器前后内存、CPU、镜像实际大小、源日志/账本/索引和恢复行为需在相同功能、负载及保留期下比较，不以容器计数或合成 SQL 基准替代。
