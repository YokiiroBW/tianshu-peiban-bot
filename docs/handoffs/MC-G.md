# MC-G：评测工具、集中矩阵与固定身份恢复准备

2026-11-01。分支 `codex/memory-acceptance-20261101`，基线 `1f0146c`。
工作树 `C:/Users/Administrator/.codex/worktrees/tianshu-cr-foundation/tianshu-peiban-bot`。

这是 **written_not_run** 交付。遵守用户“全部代码/前端改完再统一验收”，没有运行 pytest、候选程序、typecheck/build、浏览器、模型或生产/SSH；没有读取私有凭据、访问网络或真实材料。仅阅读授权源码/不可变提交、语料结构和 development 首例/元数据，编写工具/用例并做静态 diff 审阅。held_out 正文没有给实现者用于调提示。没有产品 src/web 改动或部署行为。

## 交付

- `scripts/evaluation/memory_closure/README.md`：一次定向后端命令、F 页面/build 的一次调用、M01–M18 到实际用例/语义故事/生产读回的映射，以及各证据的限制。没有另建全项目 CI 门禁。
- `run.py/dataset.py/runtime.py`：24 故事逐时间流输入真正 product graph，原生 Identity/Conversation/Sources/Episode、共享整理/索引，最终 ContextAssembler 加实际只读 Memory Action/Receipt 工具调用聊天模型。required/forbidden 只在结束后的 review 导出，语义结论始终 pending_human_review，不用合法 JSON 或正则命中判通过。
- `broker.py`：仅评测用单 Broker adapter。先核验隔离合成 Sources，再使用主协调提供的实际授权 model_context、现有 ModelClient、生产唯一 Broker（max_connections=1）；生产请求 source_refs 为空。实际模型路由/物理配额/前后台 service class 不绕开。网络前保存原 attempt 映射，unknown 后只能观察，不重发；结果恢复原合成身份后由原生应用落本地账本。旧镜像新增 receipt 字段的兼容仅在 runner，不修改旧默认算法。
- `metering.py`：按物理 attempt 去重实际回执，input/output/reasoning/cache 分列，未知 null；无费用回执时不制造真实费用。实际文本重复字节不是 token 或人物去重；索引、模型排队/耗时、SQL 实际统计与前台回答耗时各自保存。
- `recovery.py`：固定 manifest 的 plan/apply 接口。复用 C 公开 `repair_completed_imagined_episode` 的原活动/Episode/双 revision 幂等修复；只为同原 Episode 的已知缺来源拒绝且 can_request_review 时调用原 Growth review，unknown 不替换。QQ 只读核对原 event_key/job id/admission/保存的 delivery_allowed，明确当前 mode 与原处置；默认不派发、不造新入站、不更改群策略。
- `tests/core/test_memory_closure_acceptance.py`：评测工具未来材料隔离、实际 usage 不重复相加、adapter 来源/权限分离及持久 unknown 不重发、成本拒绝虚假旧基线、固定恢复双版本/unknown 边界；另补已有管线用例缺少的持续入站最长等待场景。全部仅编写，未运行。没有重写同领域既有测试。

## 对接与执行条件

D 公共端口按其不可变 `9d75b61bfdd3a4d149f6c52a1c151fa0f02b0353` 核对，主协调已集成为 `eb7817c`。源流使用正常的 Conversation 端口；最终读工具使用 Catalog/Actions 的实际 Memory query 和 source.trace。D 已确认真实暂停 synthetic Life activity 可承载 raw trace，Action 保留真实活动 ID、选定的来源、一次额度与统一 deadline；模型不能在参数中扩权。该活动仅用于隔离评测证据生命周期，不激活或执行。

C 修复端口按 `3becfa4`（主协调集成 `7ae598b`）的最终签名准备：
`await life.repair_completed_imagined_episode(context, activity_id, episode_id, expected_revision, expected_activity_revision=...)`。
只处理原已完成 checkpoint，不造新活动或新模型请求。原 occurred_at 保留，补登记 recorded_at 是当前真实时间。上述 D/C 产品依赖无需在 G 分支再重复 cherry-pick；主协调集成 G 后使用全部最终源码执行。

旧成本臂固定为完整实际生产 `dcdf729b5d22f451093e3658d131fd6757a00dde`，镜像 `sha256:679e08db330d32973e40d32f07007ee5b0b3442a1f6e8fc86af550ed39aaa40c`，运行其原单 Episode 默认代码；不是假想按人循环全文。8 development 成本样本 S01/S02/S03/S05/S06/S08/S10/S14。候选 conservative/optimized 同代码、同输入/时间/检查点，optimized 复用 full24 中已完成的同8份结果，不重复付费冒充独立样本。可直接用旧/新镜像挂同一 runner，复用 `--network container:tianshu-core` 的现有内部 Broker，无需新物理 Broker 或公网端口。若需源码，prepare-baseline 仅从固定 SHA git archive 到明确 private-root 内。

所有执行都需要主协调先提供私有配置、明确隔离 PG 管理 DSN 与实际 model_context/Broker URL/token。runner 无默认生产 DSN，禁止 25018；每故事创建 `tianshu_test_UUID`。失败/unknown 保留隔离数据库名及原 step/attempt，不能丢失后自动重跑。产物在显式 private-root 内。开发阶段没有创建/连接 PG，没有 archive 或启动候选。

时间模型明确分开消息 occurred_at 与本次原生 recorded_at，绝不把记录时间回填成故事日期。checkpoint 保存实际 snapshot 时刻。valid_at 按原话时间解释；known_at 没有过去知识记录时保留 unknown，人工 rubric 不按故事预期强行判错。

## 集中验收的实际范围与未覆盖

已有源授权/归档/预算/装配、领域/管线/Growth/模型/能力/渠道/页面定向用例均仅纳入待执行清单；同输入通过后复用结果，只在后续相关修复时重验。真实模型日志、提案/修订/事项/coverage/补查/最终答案用于人工逐人、逐错误类型评审，不能用平均 token 或 health 取代记忆效果。

本工具串行直接驱动原生 Memory handlers，DB 保留真实 enqueue 记录，但没有启动一般队列 worker，所以应用级 Procrastinate 排队耗时不宣称生产测量；实际模型 Broker queue receipt 独立计量。PG 统计可能延迟，没采到 statement latency/真实费用仍为 null。默认不驱动 Growth、Life planner 或外发，其他 C 真实闭环由主协调按其原生场景统一安排。

生产三个具名迁移、受影响域备份、Memory/Growth 固定转换清单和单写切换由 C 私有 cutover 准备与主协调负责。QQ 原 inbox 可能正常排队回复，最终执行前由主协调按实际清单和已有授权决定/一次具体询问；观察或 ignored 项与可能 reply 分列，不以猜测默认允许。GIF/自拍/Fish 配置不在本包，用户暂不绑定音色的决定保持。

当前只有静态 diff/空白检查证据，不能报告工具可执行、测试通过、真实模型语义/成本可接受、迁移成功或业务上线。后续由主协调完整集成后统一启动集中验收。

## 集中返修后的静态兼容准备

按主协调要求，以集成 `5c4d738` 与实际旧生产 `dcdf729` 只读核对，没有执行测试、模型、网络、PG 或生产。首次集中收集发现的 Python 3.12 嵌套 walrus 语法问题已在 `77eb5dd` 改为普通循环，本轮不宣称重新运行通过。

本轮纯工具修复：隔离 Settings 显式 setup_token_ref=null（否则原生 setup 会默认索取真实注册 token）；透传实际 context 配置并纳入共同指纹（否则 tokenizer 预算回退字节）；Windows CLI 的 asyncio.run 使用 Selector；允许原生可选 rerank 文本函数/capability；进度指纹同时检查真实 batch，防止 Episode cursor 不变时过早停止已知截断缩窗，并按 ID 排序避免物理行序造成假进展。相关评测用例仅编写，由主协调纳入受影响集中复验。

静态确认：Dockerfile runtime 只带 installed .venv/web/dist/deploy，缺 `/opt/tianshu/src`。遵照主协调偏好，不增加第二套 installed-source loader；旧/候选均挂各自精确同 SHA 完整 git archive 为 `/code:ro`，同一最终 runner 挂 `/runner/evaluation:ro`，显式覆盖 ENTRYPOINT 为 `/opt/tianshu/.venv/bin/python`。两版 ModelRequest 字段/原生产品入口匹配，旧 receipt 的新字段过滤仅在 adapter；没有改旧 Memory 默认逻辑。当前依赖 manifest/lock/plugin package 在两个阅读提交间没有变化，但实际仍使用各自对应镜像。

主协调已提供 `.230` 的 `/srv/tianshu-core/evaluation-memory-closure-20261101` 私有 config/postgres.env/stories/legacy-code，NAS 隔离 PG `192.168.31.210:25019` 可达。只记录必需字段名和路径，不读取/复述秘密；没有读取 held_out 正文。README 末节给三组准确 Docker 命令、目录层次、ModelCatalog bindings/capabilities、ModelClient wire 及 native初始化/无普通worker的边界。候选 source/image 由 root 在所有产品返修及集中复验、build 后填最终值；不用阅读基线冒充最终候选。

业务写入/文件限定于隔离数据库与私有结果目录；模型请求仍由唯一生产 Broker 正常记录审计/usage/配额，不能称“生产完全无写入”。没有绑定 Fish 音色或驱动外部能力。root 等 C 最后启动修复后统一复验/构建/运行，G 不提前试跑。

## 模型预算与原 attempt 对账修复

本轮基线 `e95cc45b5825fde99450065d4b831363f540c53e`，分支 `codex/memory-model-budget-20261101`。root 已确认 S01/S03 开发证据中的亚秒远端超时与同 quota 后台 hold 链为本批实际缺口并授权实现。没有读取 held_out。生产模型状态只依据 root 保存的原回执与 policy 读取，未由 G 连接/操作。

预算归属：Consolidation 租约更新 RETURNING 数据库真实 lease_until；准备截止时间为该值减既有 `MODEL_REQUEST_SECONDS=120` 及明确 `COMMIT_RESERVE_SECONDS=5`，初始约 55 秒。传到 MaterialBuilder.build/old_records 的同一个 RequestBudget 覆盖所有缩窗，原硬编码两秒去除。MemoryRetrieval 在原 parent 内预留完整 embedding/rerank 执行窗口、排队余量与 0.5 秒终检，不再将不足时限压成亚秒请求；背景使用其较长 parent，原 service_class 不变。profile 的控制请求包在原 query child timeout 内，并在当次查询的存在性判断/向量生成之间复用。没有配置改写、第二套缓存/线程/模型系统或 Broker unknown/并发调整。

候选存在性：CandidateStore.has_vectors 逐类型使用 selection 的完整 where 与 profile/embedding IS NOT NULL 做 EXISTS，范围包括主体、时态、选中版本、来源授权和完整依赖；没有词法/分页/采样推断。只有完整查询返回 false 才认定空，数据库预算失败记录不可用。现有精确/词法、多人物合并、整条 evidence unit 与提交 CAS/终检保留；短预算缺失原因明确，单候选无排序收益时跳过。

G 工具根据已有 cancel receipt 记录原状态；显式 observe 子命令只读已有 mapping 的原 unresolved ID，追加观测并生成 sidecar，compare 按同 run_id 取已对账 usage。真实 unknown 不重发，已确认 cancelled/not_started 分列，不再把它们混进 submitted_unknown；取消前排队耗时保留，缺失用量不推算费用。原答案/report/提案保持原记录，不因对账伪装语义通过。准确调用与挂载要求见 scripts/evaluation/memory_closure/README.md 末节。

新增/扩展定向用例仅编写：tests/core/test_consolidation_pipeline.py、test_memory_evidence_retrieval.py、test_memory_closure_acceptance.py；现有 ContextAssembler/product 装配用例纳入本次 root 最小集中复验。G 只做静态 diff/空白检查，未运行 pytest、构建、类型检查、候选程序、模型、PG、网络或生产。开发提交供 root 集成后统一验证，不能报告本次运行通过。

### 集中复验后的两处测试夹具修订

root 在 `cb7dae9` 集中复验报告 92 项中 83 通过、9 失败，其中 G 的两处夹具问题由本提交修订：共享准备预算用例补齐 AppError 导入；原 rerank 撤回用例增加来源独立、不同 predicate 的第二条真实 Claim，确保确有重排收益而触发 callback。回调检查两条完整单元及联合 source_refs，并将随后撤回的单元排第一；最终仍须整条剔除坏单元、保留好单元与其真实来源，当前/known_at 查询都不返回已撤回单元。没有恢复单候选重排或修改产品代码。

本次只静态 diff/空白检查，G 未运行测试。主协调可复用其余已通过结果，只重验 `tests/core/test_consolidation_pipeline.py::test_candidate_preparation_uses_one_lease_budget_across_input_shrinks` 与 `tests/core/test_memory_evidence_retrieval.py::test_rerank_rechecks_sources_and_drops_the_whole_unit`；其余失败由对应 owner 交付后由 root 集中处理。

## RC28：媒体内部短派工与业务资源隔离

基线 `271ad96e21e0035e7f605b345114c10703542a0d`，分支 `codex/queue-media-isolation-20261101`。root追加完整队列盘点并授权本窄范围实现；私有盘点为 `.runtime/coherent-refactor-20261031/memory-closure-20261101/queue-isolation-inventory-20261101.md`，不含真实正文或凭据。A/B仍分别拥有能力路由与公共ExecutionLane/worker，G未读其未提交工作树。

- 原 `capabilities.media.<name>` 只按数据库实际 due/generation 原子登记 `capabilities.media.work.<name>`，不执行长operation。work保留现有owner lease、generation CAS、心跳、异常与finally续调度。busy-owner任务原生排到lease之后，不占短维护槽等待；相同generation/lease重复dispatch复用原job。旧启动/续调度身份及原来源不变，没有脱管后台任务或第二套调度器。
- `capabilities/service.py` 窄改维护注册loop（A已明确交G独占）。最初提交的library_job别名在下节返修中已按root要求删除；原canonical media_job按实际业务类型选MUSIC_LIBRARY/DOWNLOADS，新旧原key保持。`media.acquire`继续在所选worker内联执行原Action，无子job占同槽等回环。
- 自动视频/书籍/漫画/歌单/书源扫描改为background登记同一个确定性Action，不等网络执行。A已承诺各实际scan capability路由到对应业务lane，避免再次聚到MEDIA。
- 书籍音频一次work最多生成一个未缓存TTS片段，片段文件与completed_parts沿原chapter job保存；下次复用同cache/Action key继续，最后才按完整pieces合成。unknown仍停在原未知状态，不自动重付费；原pause/profile revision/取消逻辑保持。长TTS在BOOK_AUDIO，不占短SPEECH。
- Navidrome一次work只推进最早due的一个link，其他link的时间/revision留给后续原生wake；原connection scan lease/heartbeat/原创建与核验流程保持。Lyrics原tick已限定一个artifact，原240秒收集预算不变，只迁到独立work lane，不另造歌词循环。
- Infrastructure先取node_lock再取全局slots，等待同node的日志/采集不再占满其他node可用资源；同node顺序、连接版本和原外部操作身份保持。

公共装配依赖：root最终冻结33lane/38默认执行槽（另有MEMORY_RECOVERY/SOURCE_LIFECYCLE），B配置本产品PG预算72，G不写这些公共文件。`capabilities.media.work.book_audio`→BOOK_AUDIO；`work.book_subscriptions/work.book_sources`→BOOK_SOURCES（2槽）；`work.comic_subscriptions`→COMIC_SOURCES；`work.subscriptions`→VIDEO_SUBSCRIPTIONS；`work.music_playlists`→MUSIC_PLAYLISTS；`work.music_navidrome`→MUSIC_NAVIDROME；`work.music_lyrics`→MUSIC_LYRICS；`work.comic_extensions`→COMIC_EXTENSIONS；`work.comic_cleanup`→COMIC_CLEANUP；`work.retention`→MEDIA_RETENTION。`work.music_imports/work.music_maintenance/work.infrastructure_observe`仅原生派工/短repo计划，仍MAINTENANCE。无MEDIA_SYNC万能桶；真实provider配额及unknown保护不变。

### 必要集中验证节点（全部written_not_run）

沿用 `tests/capabilities/test_runtime_observations.py`，没有新框架。新增/受影响节点如下（参数化共10例），供root在A/B/G完整集成后统一执行：

- `test_maintenance_dispatch_queues_once_without_running_the_work`（music_lyrics/music_navidrome）
- `test_maintenance_dispatch_preserves_busy_owner_and_schedules_work_after_its_lease`
- `test_future_maintenance_cannot_run_before_its_database_deadline`
- `test_registered_maintenance_adapter_forwards_the_persisted_generation`
- `test_automatic_subscription_dispatch_keeps_original_action_without_waiting_for_scan`
- `test_book_audio_yields_between_fragments_and_preserves_original_unknown`（succeeded/unknown）
- `test_navidrome_work_advances_one_due_link_and_leaves_the_other_for_next_turn`
- `test_waiting_on_one_infrastructure_node_does_not_consume_other_node_slots`

其余原有lease/generation/启动/关闭成功结果按适用性复用；B的lane/task/config节点、A的实际Action路由节点与原生装配一起由root执行。G仅静态阅读、编写和 `git diff --check`，未运行pytest、类型/语法检查、build、PG、模型、网络或生产动作，不声称已解决实况六分钟等待。该结果须完整批次部署/适用读回后判定。

### RC28原媒体job升级路由补齐

基于G原提交 `baaabf49e2ca72ccb6687511a51de303580a31f6`，只静态读取B不可变公共 `d4c2f8aa` 的 `lane_resolver(connection,payload)` 与JobSpec.execution_lane合同，不重复提交B依赖。

- canonical `capabilities.media_job` 注册服务端resolver，经原media.repo公开窄读口，在传入同一原生connection只SELECT该业务job的media_kind/audio_operation；audio+scan/import→MUSIC_LIBRARY，其余→DOWNLOADS。原task/job ID/args/schedule/attempt及业务checkpoint均不改，不建立新job或运行Action。
- `build_product` 在product.py:172已await caps.prepare，由_owners创建media.repo；注册/route_legacy_pending虽在module.start前，resolver可用且不依赖workerloop、Models、网络或另一DB连接。
- 原Repository.schedule_job复用同一分类，未来同步enqueue显式带execution_lane，保持同一个media_job task和job_id/generation/revision key；删除尚未部署的library_job别名/重复注册。这样不会因添加动态resolver而出现dynamic_job_lane_required，也不把旧task同key误换成新别名导致job_identity_conflict。MUSIC_LIBRARY资源lane仍由B保留。
- 新增 `tests/capabilities/test_runtime_observations.py::test_legacy_media_job_routes_from_original_business_type_and_reuses_same_key`：实际隔离PG业务表分别保存scan/import/外部音频下载，旧elastic native job按原元数据路由；再用同原key同步登记，断言无新ID、task/args/attempt/时间和业务完整checkpoint均不变；另验证首次新scan同步登记同task直入music_library，无需initialize。原 `test_registered_maintenance_adapter_forwards_the_persisted_generation` 适配resolver参数并断言无library_job别名。两节点均written_not_run，供root统一验证；本补丁未执行测试/语法检查/网络/模型/PG/生产。

## RC29：Reactions消费完整模型JSON及有界选择预算

本块基线 `47df9a92c503bd7a2dbbc6c8402f411325f4bbe5`，分支 `codex/reactions-model-json-20261102`。仅改 `capabilities/reactions/service.py`、既有 `tests/core/test_reaction_import_scopes.py` 及本交接。A拥有共享解码器：原提交 `0646c4f7`，G本地仅作为依赖cherry-pick为 `5016a440`；root先集成A原helper，再只取G实际Reactions提交，不重复集成这个依赖提交。

- annotate/search均消费 `capabilities.contracts.model_json`：只接受完整原生JSON或完整单一JSON/空language围栏，重复键、非有限数、前后垃圾及多文档仍由原严格loads拒绝。未修改contracts、Perception、Models或Actions共享实现。
- search只读取非thought的text parts，不用工具输出或原生thought文本构造选择ID；结果必须是带id字段的object。只有id=null表示真实无合适，其他id必须是已授权候选集合中的字符串。非object、缺字段和错误类型为reaction_selection_invalid；集合外ID为reaction_selection_out_of_range，均502。没有把非法结果伪装成无候选。
- annotation解析/字段验证、selection解析/结构/越界验证均在资产更新或选择返回前停止，用现有 `ProviderError(effect='none')` 标明功能效果未发生；模型已完成/扣量不等于功能已执行。原模型status/content_completion分类保持，真正unknown、incomplete及Source失效异常不吞、不换attempt、不自动重试。原候选授权、SourceRefs、选后原Artifact/Source与revision/pack复核及ServiceClass保持。
- root批准将reaction.search固定max_output_tokens从100改为1000，30秒deadline不变，路由、model、thinking参数和配置均不改。依据为root保存的实际image.plan回执：1000上限下总output769，其中reasoning539；Models原生Gemini编译直接传maxOutputTokens，归一化output为candidatesTokenCount+thoughtsTokenCount。因此100不能合理容纳已观察的正常思考，1000沿用现有小规划调用的有界上限，不保证模型必成。annotation仍走Perception原1800上限，不扩本块范围。G只读顶层live-capability-inspect私有快照，确认Gemini3.8-flash-high；root负责当前C8原回执的实际配置证据，未读取凭据/正文。

必要集中验证沿用既有隔离PG夹具，由root在A/G完整集成后一次执行以下节点（G全部written_not_run）：

- `tests/core/test_reaction_import_scopes.py::test_actor_import_native_selection_uses_shared_original_in_another_chat`：原跨会话授权GIF真实选中/原图读回，加完整围栏、thought/tool排除、ServiceClass/SourceRefs及有界输出合同。
- `test_annotation_accepts_complete_model_json_and_keeps_original_media`：原生/两种完整围栏落原资产修订，媒体/来源保留。
- `test_annotation_rejects_invalid_model_json_without_changing_metadata`：非object与重复键拒绝，准确502/effect=none且原metadata不变。
- `test_selection_distinguishes_explicit_null_from_invalid_model_choices`：null保留无合适；非object/缺id/错误类型/越界/围栏尾部垃圾明确失败且effect=none。
- `test_selection_rechecks_completion_and_selected_original_after_model_result`：完整JSON仍不能绕过MAX_TOKENS；模型完成后撤回原来源仍拒绝选中。

共享helper自身的严格解码节点复用A本轮检查；未变RC28队列/前端结果由root复用。本块仅静态阅读、diff/空白检查及提交，未运行pytest、语法/类型检查、build、PG、网络、模型或生产动作，不宣称真实语义最终选图或QQ发送通过。

## 插件安装不占普通文件槽的限定补充

基线 `3143e54d195b269403a56184970019408393c5e5`，分支 `codex/plugin-install-isolation-20261102`。root在最后文件池核对后授权实现。此前私有queue inventory已列Capabilities文件池，但未追到Plugin Packages.prepare内的git/pip，原“文件池仅保护磁盘工作”的概括不完整；本块补齐该实际跨功能耦合，不重做全仓盘点。

具体链路：`capabilities/service.py:236–240,325,350–364` 将普通local工作置于默认2线程/2槽，并注入PluginManager；原 `plugins/manager.py:298` 整体await Packages.prepare，后者git fetch等待上限90秒、pip install等待180秒（`plugins/packages.py:118–145,228–243`）。原installation_lock最多允许一项安装，因此不能声称插件单独占满两槽；但确实会把无关普通文件工作容量减半，与另一长文件活共同阻塞短读写。

- PluginManager现在自有固定1线程 `tianshu-plugin-install` executor；仍沿原installation_lock进入原Packages.prepare，git/pip及安装目录准备不再申请共享file_slots。不使用默认asyncio.to_thread池、不增加配置/开关/全局调度器。
- 复用原 `local_work.settled` 等待本次准备，copy_context保留原执行上下文。重复取消仍等原线程结束后才释放安装锁，既有安装operation/source/事务/错误状态与Process异步检查链不变，不另开新安装身份或重发unknown。
- manager.close先收口原background/执行/子进程，再shutdown其安装executor；Capabilities.close对已prepared但未start的非worker owner也调用该清理，不启动新worker。普通file executor/slots数量及原取消持有规则不变。

其余限定调用链已确认无需改动：book Sources.call→RuleEngine._call经独立async子进程运行同步规则/网络，feed/warehouse的网络to_thread不经service.file_executor；Infrastructure Executor.exchange异步启动supervisor，其Popen/sleep在独立子进程。媒体ffmpeg/musicdecode及插件运行RPC也为异步子进程。普通local其余族为Media文件扫描/copy/hash/打包、Projects目录/codec/readonly Git、Content原件落盘/本地目录、Plugins vault/页面及Infrastructure元数据。绑定DomainStore的方法直接走数据库同步池，部分组合元数据函数仍经local。Projects workdir.observe→run的Git为本地只读磁盘操作、每命令10秒上限；这些有界磁盘/CPU及原同资源锁保留，不把资源排队一概判成应拆新池。

仅新增以下既有 `tests/capabilities/test_plugin_runtime.py::PluginRuntime` 节点，全部written_not_run，供root收齐本批后一次执行：

- `test_installation_wait_leaves_both_capability_file_workers_available`：真实专属/普通ThreadPoolExecutor与线程屏障；安装未结束时两个普通文件工作都能进入，原context/安装ID及错误落账保留。
- `test_installation_cancel_and_close_wait_for_original_preparation`：取消与close再次取消不提前释放安装锁；第二项不进入prepare，原线程完成后关闭executor。
- `test_prepared_api_role_closes_its_plugin_owner_without_starting_workers`：prepared非worker关闭同owner及普通文件池。

G仅静态diff/空白检查和提交，未运行测试、语法/类型检查、build、PG、网络、模型或生产。未变RC28队列、前端及其他子进程结果直接复用；不宣称本块已经运行或实机通过。
