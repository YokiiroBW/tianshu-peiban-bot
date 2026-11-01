# MC-B：共享材料整理、固定执行与恢复

## RC28：业务执行并发隔离（本次交付，written_not_run）

本次基线 `271ad96e21e0035e7f605b345114c10703542a0d`，分支 `codex/runtime-execution-lanes-rc28-20261101`。公共小提交 `4458c2da` 后的最终提交需要依次集成；A 已将小提交取为依赖，其业务提交不应重复集成该依赖。下方旧 Memory 交接保持历史，不是本次基线。

执行容量由 `runtime/execution.py` 的 `ExecutionLane`、唯一 `TASK_LANES` 默认表决定，不再用 Models ServiceClass 选择 Procrastinate queue。保留原 JobSpec 位置参数，尾部新增 `execution_lane`。已注册任务通过 `lane_resolver(connection,payload)` 读取服务端业务身份，适用于新 enqueue 和旧 todo 原位转换；A 负责 capabilities.execute 与 conversation.run 的动态 resolver。未知新任务必须由服务端注册明确 lane 或 resolver，这只是内部接口约定，不增加用户流程门禁。

最终 34 lane / 39 默认槽：control、inbound、delivery、conversation、book_sources 各 2，其余各 1。固定任务覆盖如下；能力 ID 的具体映射归 A 的唯一能力路由，媒体重活派工归 G。

| lane | 默认固定任务（省略共同 task 前缀仅用于表述） |
| --- | --- |
| control | channels.cancel；conversation.cancel/reconcile/expire/expression_expire/expression_render/render_expire/expression_reconcile；life.generation_control/activate/clock/activity_cancel/activity_reconcile；capabilities.control |
| inbound | channels.ingest；conversation.seal |
| delivery | conversation.result/expression_release/expression_result |
| conversation | conversation.run 默认；A 的 resolver 按持久化 turn.autonomous 分到 conversation 或 proactive |
| proactive | conversation.participation；life.contact_due |
| schedule | life.commitment_due |
| life | life.activity_step：静态核对实际 Action 为 background=True，不等待完整外部执行 |
| planning | life.plan |
| writing | life.diary_collect/diary_generate/chapter_generate |
| memory_consolidation | memory.extract/consolidate/observe_legacy |
| memory_index | memory.index/index_object |
| memory_recovery | memory.recover：原有长扫描独立，不占整理或短维护槽 |
| source_lifecycle | sources.propagate；memory.purge_source |
| growth | companion.organize |
| images / reactions / speech / perception / media | capabilities.execute 由 A 按服务端 capability 身份路由；不按 long_running 或 origin 猜测；reactions 图库维护独立 |
| downloads | capabilities.media_job；A 的下载能力动作 |
| plugins | capabilities.plugin_event/plugin_schedule；A 的插件动作 |
| infrastructure | capabilities.infrastructure_job；A 的采集能力动作 |
| book_audio | capabilities.media.work.book_audio |
| book_sources | capabilities.media.work.book_sources/book_subscriptions |
| comic_sources | capabilities.media.work.comic_subscriptions |
| video_subscriptions | capabilities.media.work.subscriptions |
| music_playlists / music_navidrome / music_lyrics | capabilities.media.work 下对应同名任务 |
| comic_extensions / comic_cleanup / media_retention | capabilities.media.work.comic_extensions/comic_cleanup/retention |
| music_library | capabilities.media.library_job |
| maintenance | capabilities.media 下原 14 种维护入口只做短派工；work.music_imports/work.music_maintenance/work.infrastructure_observe 只做原生派工或短计划。必须与 G 的完整派工实现同批集成 |

process 为每个 lane 启动一个原生 Procrastinate worker，复用同一 App / schema / queue SQL pool / 原锁与取消机制。34 个真实 LISTEN 已入 Settings.database 计算，不按执行槽数增加监听，也不保留 interactive/due/elastic 旧消费者。默认角色池 24 + queue 8 + LISTEN 34 + Broker owner 1 = 67；现有显式角色池为 23 时需求 66。配置预算明确 72；不改变业务角色池大小、硬件或供应商真实配额。

配置准确键值：`database.connection_budget=72`，`worker.queue_connections=8`，`worker.lanes` 使用 `deploy/core/settings.example.json` 的完整表；未覆盖项取上述默认。旧 `interactive_concurrency/due_concurrency/elastic_concurrency` 已退役，不保留解析。`broker.transport` 为 `foreground_connections=8 / due_connections=4 / background_connections=24 / control_connections=4`。README 的最小示例使用 `lanes={}` 并指向完整表。

单个 ModelClient 中，INTERACTIVE、DUE、ELASTIC 分别使用独立有限 HTTP connector；status/result/cancel/外部 permit 确认保留原独立 control。generate、stream、external resource admission 都按已确定的 request.service_class 选择传输池；不会根据客户端 lane、能力是否长任务提升模型优先级。取消仍仅清理原 attempt，不自动重发；close 关闭全部四个 session。

`route_legacy_pending(database,batch_size=128)` 在全部 module.start（含 prepared owner / 动态 work task 注册）完成后、lane worker 启动前固定 cutoff，分页、逐项事务将旧三队列中的 todo 原 ID 改到服务端业务 lane。保留 args、scheduled_at、priority、locks、job_keys、attempts（包括已重试但当前 todo 的记录），doing/terminal 与 Model/Action 账本不动。单项未知 task / resolver 拒绝 / SQL 失败留原行，发去敏 `job.routing_blocked`；正常项照常启动。process.registration.legacy_job_routing 含 routed / cutoff / blocked_count / blocked（最多 128）/ blocked_complete，全部阻断项另有现有持久 EventLog。没有无限旧队列消费者或自动重派循环。

`reroute_pending(connection,job_id,*,execution_lane,task=None,service_class=None)->bool` 仅接受原 todo 且 attempts=0 的行，供 A 在来源与原 Action/Attempt 锁定事务中纠正未开始的动作；service_class 只调整原 job 的 DUE priority，不替代 A 的业务 class 账本。成功改 queue 同事务发 Procrastinate 原生通知，保持原 job / attempt / receipt。不是 resume、新 attempt 或重新执行 unknown。

`observe_on(connection,job_id=None,*,key=None)` 只读 job_id/job_state/queue_name/execution_lane/attempts/priority/scheduled_at/wait_reason，不读任务参数或推导 ServiceClass。与 RuntimeDiagnostics.tasks 共用 job_observation：todo 未知 lane 为 execution_lane_unresolved，未来定时为 scheduled（DB 时钟），已到期为 native_task_queue，doing 为 executing，终态为 null。系统 tasks 保留原 jobs/counts/limit/complete，仅给 row 增加两个显示字段，A 已对齐 UI。

本次只做源代码 / 调用链 / 完整 diff 静态阅读和提交。未运行任何测试、解析器、lint、构建、模型、网络、PG 或生产操作。新增未执行用例包括：实际 Procrastinate 占住 memory_consolidation + downloads 时十种其他业务队列取得任务；两条 chat 占用时入站/投递/控制可运行；旧坏项隔离与所有原身份/调度元数据保留；仅原 todo0 可纠正；sync enqueue；Model HTTP 后台 / due 饱和时前台 stream 与控制继续；取消回收与四 session close。旧测试夹具只补明确 lane / 已要求的 ServiceClass；实际产品装配用例要求两项动态 resolver 已注册。

根在 A/G/B 全部集成后统一执行的最小相关命令（由根提供既有显式隔离 PG 管理 DSN，不使用生产默认）：

```text
python -m pytest -q tests/runtime/test_execution_lanes.py tests/runtime/test_model_transport.py tests/runtime/test_transactions.py tests/runtime/test_events.py tests/runtime/test_product_assembly.py tests/runtime/test_broker_recovery.py::test_second_broker_cannot_own_capacity_and_control_cancels_external_owner tests/models/test_configuration_lifecycle.py::test_duplicate_external_admission_cannot_cancel_original_owner
```

这条命令仅为待执行建议，不是通过记录。A/G 各自新增节点由根合并去重后一起执行；不追加未改变的模型调度算法全套、真实模型或 QQ 验收。root 继续唯一部署者。

---

本分支基线 `8bca244`，实现者 gpt-6.1-sol / max。用户要求全批代码完成后统一验收。本交接只说明代码与静态阅读结果，未运行测试、typecheck、build、模型或实机调用，未操作生产、账号或远程。

## 提交与依赖

- 协议小提交 `32625c3` 已由协调者和 MC-A 取用。
- 领域依赖：`4a70af8647f5f8d52915a1cfb1140188183bb4b8`、`f668254be07e0457e54503ca85aaf5ea152eb0e4`、`bad09f4eeb313bfcd5cfdc12e9d29977875782e7`。本分支已取用；协调者已有对应集成，勿重复回收。
- Broker completion 依赖 `16dccd4`，本分支已取用。
- Growth 实际版本队列依赖 MC-C `ce723b8`：与 Memory 的 `episode_observer` 和 `episodes_by_refs` 成对装配；本分支不复制 C 代码。
- 检索依赖 MC-D 的 `candidates`、`query`、`indexing.index_text` 及 Episode 唯一可证 speaker 列。D 尚在本批实现，协调者应在统一验证前集成。

## 结果与责任

新默认仅 `memory.consolidate` 写整理结果。分区由角色、执行身份、会话、可读权限域和原件写域确定，群发言者不进入分区。静默、最大等待与材料预算共同触发；无材料不调模型，无新材料及无可恢复原请求不循环整理。

`materials.py` 选择有界完整前缀，超长原件以源序号和字符位置继续，保留真实来源、作者、引用元数据、邻近连续前文及固定对话截止位置。新增消息正文不按人物复制；稀疏主体与当前旧事实、假设、事项、词法/向量候选并集统一进入一次多人请求。群目录不使用前 256 人截断。旧证据关系两端实际版本也进入冻结 read_set。

`consolidation.py` 拥有冻结 batch/step、来源先锁、分区/批锁、领域调用、逐人覆盖与游标同事务提交。局部别名不重排，最多一次固定补查；第一次结果不提前落库。`ModelSteps` 持久化派发权，跨进程未知只观察原 attempt。供应商物理 succeeded 与业务完整输出分开；length / max_tokens 明确为 truncated，整批不提交、游标不动，并有界缩窗，达到最小窗后要求预算配置。

`recovery.py` 只将未完成旧 Episode 登记到固定转换清单。已提交或未知旧请求只观察原 attempt；存量原结果可证明映射时才转换，不能证明则留可解释废弃回执，再按原权限把尚未处理的新材料交给唯一默认流程。已完成历史不重新播放。`memory.extract` / `memory.index` 只保留真实已排队键的有限适配；新任务使用 `memory.consolidate` / `memory.index_object`。

Memory 统一在 `observe_episode` 事务中通知 Growth，传实际 Episode 身份与版本。Conversation 输入、能力结果和公开表达不再各自发无版本的 Growth 通知。`episodes_by_refs` 精确读取实际版本，缺失或改版返回 coverage 缺项，不用 max sequence 当已消费证明。`record_reflection` 可复用调用方事务并 CAS 冻结 Episode，锁全原件闭包；ACTOR imagined 完成原件只能支持主观反思，不能成为人类自述。

手动纠正和真实派生失效通过 `domain_changed_on` 引入有来源的修订材料；自动批次只对实际派生失效唤醒，避免自激。撤回清除 Episode/Reflection 正文、索引、batch/step 敏感输入，领域历史/关系清理由 MC-A 的唯一来源回调执行。索引先写规范词法正文，再请求可选 embedding，旧空索引按稳定任务键有界补齐，不伪造 recorded_at。

合法 quote 允许 256 个结构片段，原件仍为 observation；引用原作者只从已核验原事件解析，转发显示字段不会升级为本人确认。Memory 的邻近读取端口固定截止位置、权限域和原件复核。

## 实际 HTTP 合同（供页面装配）

以下均为 POST，请求有 actor_id 及当前合法 person_id / conversation_id；写操作还需 client_request_id。领域返回 `Page={items,next_cursor,coverage}`，完整证据单元字段由 `memory/types.py` 定义。

| 路径 | 请求主要字段 | 实际结果 |
|---|---|---|
| `/api/v1/memory/claims` | subject_id?, cursor?, limit?, time_mode?, valid_at?, known_at? | ClaimRevision Page，含 head_revision / relations / memory_kind / 双时间与缺项 |
| `/api/v1/memory/claims/get` | claim_id, time_mode?, valid_at?, known_at? | 单个已授权固定时间命题 |
| `/api/v1/memory/claims/history` | claim_id, cursor?, limit? | 完整 MemoryEvidenceUnit 历史 Page |
| `/api/v1/memory/matters` | subject_id?, cursor?, limit?, time_mode?, valid_at?, known_at? | MatterView Page，含完成依据、条件、due_at、关系与历史语义 |
| `/api/v1/memory/matters/history` | matter_id, cursor?, limit? | 完整 MemoryEvidenceUnit 事项历史 Page |
| `/api/v1/memory/query` | plan:{query?,subject_ids?,matter_refs?,time_mode?,valid_at?,known_at?,evidence_kinds?,coverage_mode?,budget?}, cursor?, limit? | EvidenceUnit Page；服务器构造 deadline，list 是稳定分页清单，ranked 是有界候选且不冒称完整 |
| `/api/v1/memory/episodes` | cursor?, limit? | EpisodePage（items / next_cursor / watermark / coverage）；每项 consolidation 为实际批状态和恢复动作 |
| `/api/v1/memory/extraction/status` | episode_id | episode_id / revision / batch_id / status / error_code / action / fragment_cursor / target_results / unresolved；来源可读时还有 step_ids / schema_version / context_unfulfilled |
| `/api/v1/memory/extraction/resume` | episode_id, expected_revision, client_request_id | CommandResult.data:{episode_id,queued,action,batch_id}；重放不重发任务，复核后重建当前 consolidation，replayed=true |
| `/api/v1/memory/projection` | person_id? 或 group_id?, time_mode?, valid_at?, known_at? | claims、hypotheses、matters、reflections、evidence_units、time、pages、coverage；假设与事实独立 |
| `/api/v1/memory/index/rebuild` | client_request_id, kinds?（默认五类）或单 kind?, cursor? | CommandResult.data:{queued,kinds,next_cursor,coverage,replayed}；游标 `i1:` 绑定同一类型集合、kind 与实际稳定 ID，所有任务走唯一 index_object |

群 projection 另有 `group_consolidation={view:'current_processing',items,subjects,coverage}`：按最近 16 条当前可读 Episode 的真实 batch 去重，返回被授权逐人 target_results 和 unresolved；窗口截断、来源与主体缺项均在 coverage 中。它是当前处理状态，不伪装成历史事实。需要完整处理记录时继续使用 episodes 分页。反思页也支持 time_mode / valid_at / known_at；首次知道时间未知或反思有效时间未知会明确降低 coverage。

Episode.speaker_id 只取来源中可证明的唯一人类作者，多个作者、只有引用或工具前置的人类材料都留 NULL。它与 person_id（所讨论/所服务人物）不同。新重建入口默认覆盖 claim / hypothesis / matter / episode / reflection；重放只复核当前可读分页、不重新派任务，queued=0、replayed=true。

`consolidation.status` 使用真实 batch 状态 `prepared/waiting_model/ready/applied/stale/failed/revoked`；尚无批时为 Episode extraction_state。`target_results` 含 subject_id、status=`changed/no_change/unresolved`、processed_material_ids、operation_ids；这些字段来自已原子提交的回执，未提交结果不展示为成功。

页面只使用以下 action，不从失败字符串推断：

- `observe_original_attempt`：观察原 batch / step / attempt，不新建模型请求。
- `refresh_material`：已知旧应用不可用，用原权限和未推进游标重新准备当前材料。
- `wait_for_new_evidence`：当前材料已处理，缺项需要新证据，不空转重模型。
- `configure_budget`：输入或最小窗输出预算不足；先调整配置，再恢复。
- `legacy_application_abandoned`：旧映射无法证明，原执行回执保留，不伪称旧结果已应用。

## 编写但未运行的用例

- `tests/memory/test_consolidation_contract.py`：逐人精确覆盖、错误人物/版本、群分区、固定授权序列化、短 manifest、来源区间去重、连续前缀、推理占用预算、证据关系端点 read_set 与五类索引游标。
- `tests/core/test_consolidation_pipeline.py`：多 Episode 单请求、未知观察原 attempt、非法/截断全批不提交、301 人群稀疏身份、quote 原作者不冒认、按实际 Episode 版本读取、唯一可证作者、反思版本 CAS 与历史时间缺项。
- `tests/core/test_incremental_consumers.py`：原增量场景切换到共享整理器、长原件尾部完整覆盖、真实视频分析与未消费模态缺项。

## 集成后必须核对的限制

本批尚无执行证据。领域、Broker、Growth、检索和前端全部集成后再由协调者一次运行必要用例与装配验证。D 的规范索引 helper / speaker 字段和 C 的 Sources kwargs / Growth SQL 为成对依赖，不可只发布 B。模型语义、真实成本、跨进程故障和生产迁移均未在开发阶段验收。

## 统一验收第二轮：B 集中静态修复

分支 `codex/memory-pipeline-repairs2-20261101`，基线 `753eaa2`。协调者报告本轮已完成 85 pass / 3 fail，长原件用例在原地反复准备首批，另有 33 项未执行；这里不将这些上游结果当作本修复的验证。B 本轮只阅读、写代码/用例及审阅 diff，未运行测试、解析器、构建、模型或任何数据库命令。

- 长原件首批的已知失败为 required 12803 / configured 12000，默认 Counter 用 UTF-8 字节作保守估算。原 Schema 重复展开 Claim 字段、时间和局部 ID 枚举，使最小 256 个汉字仍放不下。现在同一输出 Schema 以 `$defs/$ref` 复用公共定义，Claim 变体仍由 Draft 2020 的 `unevaluatedProperties=false` 严格关闭额外字段；人物、材料、读取版本、逐人 no_change 和所有操作语义不变，没有提高默认预算。
- 本整理器的 Schema 仅由 `consolidation_instruction` 序列化进 `ModelRequest.system`，输出交本地 `Draft202012Validator`；`_request` 不传 provider 原生 `response_schema` / `response_format`。因此本次没有新增供应商适配要求或将本地 `$defs` 下发到原生结构化输出字段。
- 缩窗以同一实际 request 的 Counter 测量值计算固定开销和正文余量，重新构造完整前缀后再次测量；超长单原件必要时可到精确一字符选择，保留 source_index / position 继续尾部。只有最小前缀、人物与协议仍放不下才返回 budget blocked。129 个未选来源引用保留在服务端依赖和 manifest，未进入模型正文，不能靠删引用或漏尾部绕预算。
- 既有预算失败快照加入这次真实输入布局版本 `input_layout=consolidation-shared-defs-v1`。旧展开布局产生的 blocked receipt 在新布局下可沿既有 `refresh_material` 命令重新准备一次；它不是用户配置或第二套恢复器，原 unknown 的固定请求仍优先观察，不因布局变更重发。
- `PreparationReads` 是单次 `_prepare` 的临时读集合，键含冻结授权上下文、source_id/revision、精确 locator 和起点；只复用本次已合法读到的窗口，缩短时同步 Coverage 与 next_position，不扩读无关原文，也不跨 batch/补查/恢复持久缓存。提交前原有来源/权限复核和 Episode/read_set CAS 全部保留。
- `episodes_by_refs` 现在复用与普通 Episode 页同一个 `statuses_on` 注入可读批状态；未提交目标只列冻结主体，不生成 target_results，来源不可读时继续遮蔽。
- 多作者用例的旧 owner 上下文没有 Alice/Bob 私有范围读取权，原失败发生在合法来源校验。夹具现在先保留该拒绝断言，再明确只给这两个来源范围的 read grant 后验证多作者；未改变全局权限、生产 Speaker 实现或原有唯一作者/tool 先决来源不得冒认的断言。
- `run_pending` 对相同配置、无 active batch 且 wake 已关闭的 budget failure 立即带诊断失败，不再强行写 wake 连做 80 次 SQL。129 片段尾部断言保留，并增加实际请求预算及长原文只覆盖一次的断言。

待协调者集中复验的现有合同文件：`tests/memory/test_consolidation_contract.py`（公共定义引用、每种 Claim 变体必填/多余字段、人物/材料引用、read_set/no_change）、`tests/memory/test_extraction_contract.py`（有限旧合同未变）。受影响调用链文件：`tests/core/test_consolidation_pipeline.py`（含本轮两个失败节点、原 unknown/原子性/版本）、`tests/core/test_incremental_consumers.py`（129 片段尾部、默认字节预算、视频派生来源）。新增的默认 Counter 最小前缀和临时缓存精确范围用例也在 contract 文件中，均 written_not_run。

## 真实开发样本后的首次类型选择修复

分支 `codex/memory-semantic-contract-repair-20261101`，基线 `3153c53f621cc786498296327d01312bb58af8d6`。协调者确认开发样本 S01 虽回答和逐人覆盖正确，却把两项明确未来安排只记成 Claim，没有 Matter。本修复只改 `extraction_contract.consolidation_instruction` 及本交接；未读取 held_out，未修改冻结候选的输入、输出或评测记录。

- 同源提示现在明确首次类型选择：独立事实、属性和条件偏好用 Claim；可跟踪进展的具体安排、约定、承诺、待办首次明确表达即用 `upsert_matter`，不能只拆成时间属性 Claim。兴趣、愿望、假设、提问及他人建议不自动成为安排。陶艺习惯与约定取陶坯的通用对照仅解释语义，不构成关键词分类器或 S01 特例。
- 旧事项以主体、上下文与来源确认身份；改名、改期、进展、取消沿同一个固定 `r` 引用及 `expected_revision/reads` 追加版本，同名或同日不证明相同事项，不同人的独立安排不合并。关键身份不明则补查或未决。六种 Matter 状态、约定/截止时间和条件均说明；仅知日期、不知时刻时 `due_at=null`，原时间粒度保留于 `conditions/summary`，不制造午夜或把发言时间当安排时间。
- 完成依据继续区分真人报告 `human_report` 与服务端材料内真实成功 Action/Receipt 的 `action_receipt`；真人明确报告已完成可以记 completed，只证明被报告完成，不要求另获执行回执。缺少真人完成报告且无真实成功回执才不能记 completed；报告与执行依据分别保留，执行失败不得伪称成功。ACTOR 虚构活动不能证明真人或外部动作完成。Matter 记录事项认识，不创建提醒、任务或外部执行。相关独立 Claim 可以 `about_matter` 关联，不重复拆出同一安排凑数。此处已消除首次提交中“未核实或失败不填 completed”可能错误否定真人完成报告的歧义。
- 通用 JSON Schema、合法操作集合、逐人覆盖、来源校验、CAS、固定 ModelSteps、未知请求观察及所有模型参数均未改变。Schema 仍只序列化为 system 提示文本并由本地 Draft validator 校验，不新增 provider 原生 response_schema 要求。新提示只影响随后准备的新请求，原固定请求不换提示、不重发；S01 的原 rerank unknown 也未操作。

静态文本计数（含完成报告歧义补充）：提示组装内固定字符串从 740 字符 / 1712 UTF-8 字节变为 1524 字符 / 3472 字节，净增 784 字符 / 1760 字节，较首次修复提交再增 141 字节。动态 Schema 和无变化示例未变，因此无 tokenizer 的默认 `TokenCounter` 对同一完整输入净增 1760 个估算 token；配置 tokenizer 时及供应商实际 token/费用增量未测量，不以字节冒充这些数据。`max_input_tokens=12000` 及输出/推理预算均未提高；固定开销增加可令原有有界前缀变短，尾部仍须沿原游标续处理。

实际只做代码/调用链阅读、完整 diff 审阅与 PowerShell 静态字符串字节计数；未运行测试、构建、产品代码、模型、PG 或生产操作。此次没有新程序约束，未新增仅复述提示字符串的测试；现有来源/CAS/Schema 验证结果可复用，模型是否按新指引选择 Matter 必须由新开发样本运行确认。

建议协调者整批交付后最小复验：`tests/memory/test_consolidation_contract.py::test_default_byte_counter_can_hold_a_prefix_without_serializing_unselected_sources`、`::test_manifest_keeps_mapping_without_a_second_full_model_input`，以及 `tests/core/test_incremental_consumers.py::test_long_original_and_more_than_128_parts_reach_the_tail`（固定提示开销改变涉及的实际预算与完整尾部）。真实语义用新 SHA、新 run 保留原 S01 输入，检查各自 Matter 的 planned/due_at、C 的 no_change 和逐人覆盖；若继续核对改期/完成，复用开发集已有相应样本。保留 `3153c53` 冻结证据，未知 rerank 仅允许原 attempt 观察。本分支未执行上述复验。

## 语义提示预算回归收敛

分支 `codex/memory-semantic-budget-repair-20261101`，基线 `cb7dae9`。协调者统一复验记录 `acceptance/backend-semantic-06-events.jsonl` 确认默认字节 Counter 合同和 129 片段尾部两个回归；后者首字符前的固定输入已需 12089 / 12000，现有 helper 已立即报 blocked，没有重复空转。本轮仅压缩同源提示、更新既有输入布局标记及本交接，不改 Counter、Schema、预算上限、人物数量、原件、测试输入或断言。

- 提示合并重复的身份、时间、完成和补查说明，状态值直接引用 Schema 枚举。首次具体安排/约定/承诺/待办使用 Matter，事实/偏好使用 Claim；愿望或建议不自动成计划。旧事项的主体/上下文/来源身份、同一 `r` 的 `expected_revision/reads` 延续、改期取消、时间粒度、真人完成报告与真实成功回执、ACTOR 虚构边界全部保留。来源 m / 旧记录 r / 新对象 n、支持组 AND/OR、逐人覆盖、no_change/unresolved 和最多一次补查也保留；原子应用与完整 read_set 仍由同一未改 Schema/领域流程执行。
- 固定提示从 1524 字符 / 3472 UTF-8 字节缩至 1080 字符 / 2326 字节，节省 444 字符 / 1146 字节。相对 `3153c53` 的 740 字符 / 1712 字节，Matter 语义净增量从 1760 字节收敛为 614 字节。动态 Schema、合法无变化示例和所有原文未删减，因此同一完整输入在默认无 tokenizer Counter 下减少 1146 个估算 token；已知 12089 的固定输入可按该差值静态推算为 10943，留 1057 单位余量，此为静态算术，未执行请求或宣称用例通过。供应商实际 token 和配置 tokenizer 的差值未测。
- `_input_budget_record` 只将既有 `input_layout` 从 `consolidation-shared-defs-v1` 更新为 `consolidation-matter-semantics-v2`，无新恢复层。旧布局下无 batch 的已知预算失败经原比较规则返回 `refresh_material`，可从未推进材料继续；新布局和配置不变的再次阻断仍返回 `configure_budget`。已有 active batch 优先观察原 attempt，原请求的提示、映射和结果不改变、不重发。

本轮只静态阅读、审阅完整 diff、用 PowerShell 计数字符/字节并提交；未运行测试、构建、产品代码、模型、PG 或生产命令，未读取 held_out。未新增提示字符串测试。协调者与 A/G 整批集成后最小复验沿原两失败节点，加 `tests/memory/test_consolidation_contract.py::test_input_layout_change_rearms_only_a_known_blocked_receipt`（含同配置 configure_budget、布局变更 refresh_material、原 active attempt 观察断言）；继续既定开发样本语义确认，保留原冻结评测记录。本节计数覆盖前节首次语义提示和完成歧义补充后的当前版本。

## 授权人物显示映射闭环

分支 `codex/memory-identity-context-repair-20261101`，基线 `ba2b95daf6eb1d62ade0e14f5f8d71c38f484cd4`。开发 S01 的两项 Matter 已正确，但回答材料没有 C 的名称映射；正常聊天与评测复用的 ContextAssembler 均存在这一消费缺口。本分支不修改 runner、不注入故事标签、不为转述者造 Claim、不创建权限或扫描全员。

- `MemoryEvidenceUnit.display_subjects` 为读取时的 `[{subject_id,display_name,aliases,kind}]`，表示当前授权名称，不冒充历史身份或独立事实；无法解析时为空，内部 ID 不充当昵称。新 `memory/display.py` 复用原完整单元来源批量复核，再仅按实际可读单元的 subject/speaker 及关系两端证据中的 subject/speaker 调用 `Identity.resolve_memory_subjects_on`。每次最多 128 个精确 ID，返回名单再次按请求 ID 过滤，默认群主体或无关目录成员不能混入。
- 同一装配由 `projection.evidence_units`、检索/最终 revalidate、原生 `memory_search`/`memory_claims`/`memory_matters` 和 `memory_source_trace` 使用。工具自动映射位于 `items[].display_subjects`；原 `resolved_subjects` 仍表示显式 `subject_aliases` 的解析结果。当前无 Claim 的转述者可以通过已有 Episode 原件获得正确显示身份，引用证据作者和工具后续回答亦使用相同字段。
- 映射与整个证据/原文结果绑定，来源或范围不再可读则不查询人物、不返回该单元。Context 最后来源复核、引用闭包和工具结果来源传播继续生效；长昵称/别名计入既有完整单元 token 预算，不能留下脱离证据的名字清单。显示资料仍置于动态材料/工具结果内，Context 固定规则明确其不可信性质和未知姓名边界。原文追查仍使用原前台身份的一次命令，不增加读取次数或绕过原 locator。
- `model_material` 排除该显示字段，整理器继续使用原冻结 `subjects`，不将名字重复加入旧记录输入；规范 `index_text` 仍不含显示映射，无需重建索引，不改 12k 整理预算、模型参数、表或迁移。Context 固定规则仅净增 133 UTF-8 字节；动态映射随真实单元计数，供应商 token/费用未测。

新增但未执行的回归：`tests/context/test_memory_evidence_units.py` 两项覆盖可读主体/引用作者、排除私有及无关人物、未知昵称、完整单元别名预算，并扩充末次撤回不得遗留姓名的原断言；`tests/core/test_memory_evidence_retrieval.py` 两项覆盖只有 Episode 的发言者在正常 Context/原生工具/工具续轮获得同一映射，以及 projection 当前昵称、检索预算、跨主体隔离与来源撤回；既有原文追查用例补可读作者断言。没有运行任何测试、解析器、构建、模型或 PG；实际仅源码/已授权开发证据阅读、编辑、静态 diff 审阅和文字字节计数，未读取 heldout。

供根协调者全部返修合入后统一执行（下列命令本分支均未运行；核心用例沿用既有隔离 PG 环境，不使用生产 DSN）：

```powershell
python -m pytest -q tests/context/test_memory_evidence_units.py tests/context/test_budget_units.py
python -m pytest -q tests/core/test_memory_evidence_retrieval.py -k "episode_only_speaker_is_named or projection_display_is_current or model_catalog_query_uses_time_plan or original_followup_is_range_bound or conflict_has_both_complete_sides or vector_exact_keeps_five_types_and_public_or_support_across_owners or rerank_rechecks_sources_and_drops_the_whole_unit or context_rechecks_background_revision"
```

根已安排 A 单写通用整理协议/schema 与 S09/S12 后续语义规则；本分支不改 `extraction_contract.py`，避免并写。S12 已按实际请求将共同主体与已表态/未来承诺的证据交 A，最终同批集成后再由根选择必要开发故事复验，不修改现有冻结结果。

静态附查：candidate-02/S07 的 `memory_unavailable` 来自可选 search 总分支的通用异常捕获，实际 model-calls 有 answer 阶段 query embed 成功（858.5ms），无 rerank；原文材料只导出通用缺项而没有异常类型，不能证明具体 SQL 错误或供应商失败，本次不泛化修改该路径。评测 `disposable_database` 默认 `keep_database=false`，成功故事 finally 删除自建 UUID 库；失败或原 attempt 未决才自动保留，显式 keep 仅原已有配置。runner 没有仅复验回答的正式入口，observe 只读取原未决 attempt。本分支未修改评测工具、数据库保留策略或既有数据库。

## f7dd305 启动时旧 Growth 授权阻断修复

分支 `codex/growth-startup-recovery-20261101`，基线 `f7dd3055de603fbfa40c6a96874c57076d840d57`。root 已确认三项迁移成功；interactive/worker 的 module.start 直接调用 `Growth.recover → recovery.step → Identity.check`，一个旧 epoch context 的 `access_changed` 因而中断整个应用启动。本修复不改迁移、不回滚库、不手工改运行数据，也不扩大为调度或模型架构重构。

- `recover` 只用既有 JobQueue 登记逐分区 `companion.organize`，不在 module.start 做身份或旧材料转换。一个恢复 job 的旧 context 不再影响另一个分区或应用启动。
- legacy worker 仍一页一事务推进。仅 inventory/经历版本登记在 `access_changed` 且没有 active batch 时复用 `Identity.refresh_scheduled_context`，由现行主体/角色/权限校验并受原 scope/audience/permission 上界限制。原 requests 阶段始终按旧 context（含 epoch）发现原请求及 request=NULL tombstone，避免改 epoch 后漏清单；请求扫描结束后，经历登记可保存已合法重核的有限 context。没有修改任何 GenerationRequest、原 attempt、旧请求 manifest context 或原游标。
- 无法合法续接时用已有 `last_error/blocked_material` 留下 `reflection_recovery_authorization_required`、原错误、原阻断材料和精确 phase，不推进位置、不登记已审核、不自动重排。已阻断项的新 Episode 只登记实际版本，不覆盖旧授权 context，不反复派工作。正常 worker 的 frozen active batch 也按项记录同类授权阻断，不借 timer refresh 改写执行授权。
- 现有 Growth view 使用 `recovery=reauthorize_original_scope`；授权阻断时省略可选的 `legacy_state` 以免已有页面显示“正在转换”，真实 phase 仍在 blocked_material。无 active batch 时既有 review 按钮可重核原权限后续接原扫描，仅增加 command/state revision，不增加 generation_revision、不放弃 legacy 原请求、不清游标。有 active batch（包括原 unknown）时该按钮不可用，命令明确拒绝替换；原模型结果只能沿合法原身份另行观察，不能把 epoch 刷新当作结果读取/执行授权。

新增三项定向回归（仅编写）：启动不校验 context、失权项阻断且另一分区可推进/新消息不覆盖旧权限；合法 epoch 续接保留原 unknown/tombstone 的 context/attempt/发现身份；现有 review 命令重核权后按原位置完成旧转换且 generation_revision 不变。原两项 legacy 用例随启动从同步推进改为队列推进，增加显式 worker 步骤，原业务断言保留。`test_growth_output.py` 的 unknown 不可替换、已拒绝结果不自激等既有用例未修改。

本分支没有运行测试、解析器、构建、网络、PG、生产或模型，未读取 heldout。只阅读授权源码/现有用例，编辑并静态审阅完整 diff。root 收齐后最小统一验证命令（未执行）：

```powershell
python -m pytest -q tests/companion/test_growth_legacy_recovery.py tests/companion/test_growth_output.py
```

Identity 的原权限上界机制无修改，已有真实领域定向验证可复用；本分支不另外重跑全套。实际容器启动、已迁移状态下的替换镜像与 guard 恢复、八个分区进展由 root 单写发布和读回确认；此交付不声称服务已经恢复。没有 active batch 的受限扫描续接不能解锁一个旧 epoch 的在途模型请求，这是保留原身份的明确边界。
