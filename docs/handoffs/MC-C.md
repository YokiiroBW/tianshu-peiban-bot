# MC-C：Growth、Life、渠道与能力闭环

2026-11-01。基线 `8bca244`，分支 `codex/product-closure-20261101`。
工作树 `C:/Users/Administrator/.codex/worktrees/model-nullable-response/tianshu-peiban-bot`。

本包是本地实现交付，尚未统一验收。依用户要求，开发阶段**没有运行测试、typecheck、build、模型试跑、浏览器或实机验收**。只进行了代码/文档阅读、接口协商、diff 阅读和提交；下面的用例均为已编写、未执行。没有生产、QQ、账号、远程推送或配置资源变更。

## 提交与集成顺序

| SHA | 内容 |
| --- | --- |
| `3fe73bc` | Growth 固定批次/阻塞材料 SQL 初始接口 |
| `16dccd4` | ModelCompletion 原生 stop/incomplete 与物理状态分离 |
| `38acd56` | Responses 明确 failed 保持物理失败 |
| `34da646` | 有界 Growth、原子反思、imagined 实际完成来源、QQ NUL 与引用核验元数据、授权外 recall 消费 |
| `e64fb03` | capabilities 运行观察 SQL |
| `ce723b8` | Growth 实际 Episode 版本队列、Sources 事务/support_refs 端口 |
| `e95ffe7` | 有限旧 Growth 转换、原 GenerationRequest 清单恢复、MemoryPort 同步 |
| `a79e575` | 基础设施 last_test/失败终态、媒体 worker 持久投影、next_due/变更唤醒/空闲退避 |
| `90ef791` | ModelReceipt.hold_until、统一 content_completion、Responses 残缺工具参数延后解析、Broker 结算用例 |
| `4977585` | 天气等待与旧读数、角色语音/图片 readiness、理解/图片计划/表情截断处理 |
| `a14be27` | 预注册 maintenance 任务包装器透传 wake_generation，避免构造期重复注册 |
| `c7bfba2` | 实际 action receipt 来源、内容分页续接、Life/日记/写作完整性消费 |
| `dfc25d1` | 天气旧上下文按领域 typed 边界比较，避免跨进程权限集合序列顺序差异 |

`a14be27` 必须与 `a79e575` 一起集成。其余后续提交按上表顺序承接。
主协调者已反馈 `ce723b8/e64fb03` 集成为 `29183b2/12cc765`，`e95ffe7` 集成为 `1d4714c`；其余合入 SHA 由协调者记录。

## 领域与恢复

Growth 继续只产生主观 Reflection、可撤销表达习惯和短期 affect，不改 authored persona，不产生人类事实，也不授予权限。

- 固定的 32 条只是候选页上限。完整 Episode 前缀按实际 mandatory 预算减半，保留完整人格和单个证据单元；单项仍超预算或缺来源时保留具体 blocked_material，阻止新消息反复触发相同无解批次。
- 固定 material_key、原上下文、输入 ID/revision、原 request/attempt 在同一事务登记；新事件只进待消费版本表。unknown 沿原身份观察，不能通过 review 换新身份。
- `companion.growth_inputs` 覆盖实际 Episode 修订；最大 sequence/cursor 仅为诊断信息。Memory.observe_episode 同事务 observer 由 B 装配，包含幂等恢复分支。晚提交的较低 sequence 仍能消费。
- 初次恢复先按页（最多 128）读取自身 GenerationRequests 中迁移时刻之前的 organize 原请求，保存引用清单和分页进度；再把旧 cursor 后的 Episode 分页登记到版本队列。已审水位相等也执行初始化，不会因新队列表为空将旧材料直接判定完成。原清单先于新组批消费；没有复制/重建原 prompt 或替换未知 attempt。
- 反思、style、affect、消费覆盖与 cursor 在同一事务提交。调用 B 的 `record_reflection(..., connection=conn, episode_revisions={id:revision})`，Sources→Episode→consumer state 的锁序与 B 一致。状态/输入队列写入统一 state→inputs。
- Memory internal memory_revision Episode 不进入 Growth 或日记。imagined 来源只证明软件完成了角色想象活动，不能证明其中的身体活动或人类事实。

Life 想象完成记录通过现有 `Sources.record_event` 登记 ACTOR 原件，与 Episode 同事务，明确 experience_kind=imagined、imagined=true、representation=fictional_world、software_action=false。实际工具完成额外登记 TOOL_RESULT action_receipt 原件，保留实际 execution/effect/action_id/receipt_id 和全部结果来源（包括视频 consumption.source）。计划依赖只作为支持闭包，不升格为动作成功或真人自述。

旧生产中已经完成却没有原件的想象 Episode 没有在开发环境回填。现已提供窄领域端口，供 G 固定清单工具后续受控调用：

`LifeService.repair_completed_imagined_episode(context, activity_id: str, episode_id: str, expected_revision: int, expected_activity_revision: int | None = None) -> CommandResult`

它复用默认完成链同一个 ACTOR 原件构造函数，只接受 completed、无 action/receipt/capability 的 rest/reflection/room 活动和 `fiction:activity_id:oldrev`、fictional_world/software_action=false 的原 Episode；完成后的活动 revision 必须仍为 oldrev+1，避免把后续改写的活动当作过去材料。清单应显式填写 expected_activity_revision；省略时也锁定当前行并核对不可改写的完成版本关系。原 Episode ID/event_id/occurred_at 保留、revision+1，新来源 recorded_at 为当前事务时间，actual_result 保留 imagined/软件 checkpoint 语义，不改活动或重跑模型。只通过 Memory.episodes_by_refs/observe_episode 公共口读取和修订，源校验、Episode CAS 与命令幂等同事务。重复同一清单返回原修复结果，重读仍复核来源和版本；漂移/遗忘/撤权拒绝，不回放旧私有缓存。

返回 data 含 state=repaired、activity_id/activity_revision、episode_id/event_id、previous_revision/episode_revision、source_refs、occurred_at、recorded_at。B 的 observer 会登记新版 Episode 并唤醒 Growth。若旧 Growth 仍停在已知缺来源拒绝（例如 reflection_support_invalid），G 应先读 growth/view，确认该固定批确包含修复前版本且 can_request_review=true，再用清单固定 client_request_id 和当前 review.revision 调现有 growth.request_review。unknown 不允许调用新审、不替换原 request/attempt。端口不自动创建第二条 Growth writer，没有新 HTTP/UI/迁移。

Sources 扩权仅限 record_event 的 connection/support_refs，同步 `sources/ports.py`；conversation/generation.py 仅增加 `pending_materials(context, prefix, after, limit)` 有界读取，不改默认执行语义。B 已确认 episodes_by_refs Page 和反思事务接口。

## 故障及能力代码检查结论

| 项 | 本包结果 |
| --- | --- |
| QQ NUL | 派生显示/数据库文本替换 NUL、孤立 surrogate；原传输 JSON 另登记来源并与派生材料关联。没有静默丢消息，未触碰原 accepted 生产事件。 |
| quote / at / reply | B 修 Conversation quote 准入与作者核验；C 将 Identity decision 核验的 mentioned_person_ids/reply_to_event_id/original_author_person_id 写入对应 InputPart.metadata。转发 node 显示名保持外部归因。统一 256 parts 的端口由 B 对齐。 |
| 授权外 recall | 只将 channel_target_forbidden 的 operational recall 记为 ignored 并消费；其他错误仍失败。没有扩大目标权限、修改绑定或重放历史通知。 |
| RC04 天气 | 观察同一上下文/查询的原 pending/unknown attempt 及 hold_until，守护期内立即返回 observe_original_attempt/retry_after，可带授权可读的旧读数 stale=true。hold 到期后只允许后续独立周期读取，旧 attempt 仍 unknown。新读取 deadline=60 秒对应现有有界网络/排队，未知占位仍服从 Broker policy，未削弱 unknown hold。 |
| RC06 Fish | 现有 native HTTP/SSE/WS、ASR、音色检索/克隆、Plus MCP/OAuth 管理链存在。用户明确暂不绑定音色，本包没有选音色/克隆/默认绑定。连接 configured 与角色可合成分开报告；管理/ASR入口仍可用。 |
| 多模态 | 原件经 Perception.direct_parts 进入已配置主/辅助 Broker 路线；GIF 有界采帧，视频有真实 interval/Consumption。明确 length/incomplete 或空正文不会变成 analysis_completed。群 observe 没有改成全部强制付费理解；缺分析仍诚实保留。 |
| RC07 表情 | 复用 packs.import→逐 asset annotate→index→purpose/attitude/irony 语义选择。空授权图库不先支付查询 embedding。GIF 原件保留；import 本身不自动上传到模型。没有描述的条目 index 会跳过，不能只 import+index 就宣称完成语义化。 |
| RC08 自拍 | 复用 Life.scene→Images.prepare/计划→ComfyUI 或既有 nai/gpt_image 引擎→真实 artifact/receipt→create_only/share_candidate/requested_delivery。活动主动自拍仍进入既有 contact 决策；被动请求用原表达链。角色 readiness 要求角色自己的 image_profile 与可用引擎；只有一个自有非默认 ID profile 时可正确选用，没有发明外观。 |
| RC09 内容/日记 | web/forum content.read 登记实际原文与规范文本，B站 VideoOriginals 获取实际原件，Perception 处理实际音画区间。补 Life 原先遗漏的 content.read continuation，恢复用返回的精确 capability/arguments。书籍/视频范围续接与覆盖仍复用既有实现。日记只消费授权 Episode、保留删节 coverage、主观想象标签，不把摘要/日记当第二事实来源。 |
| OneBot 完整消息 | 复用现有 23 类 segment、38 API 目录及结构消息/引用/at/合并转发/原件链；没有依据零历史发送记录另造系统。真实回复/发送/回执需最终授权场景统一验证。 |
| 关系/主动联系 | 继续读 Memory relationship/projection；冻结关系不自动改 style，关系不成为权限。Life 决策、contact、Scene/Expression 复用既有入口，没有从配置或空运行历史推断它们已验收。 |
| RC11 基础设施 | ExternalCatalog.record_test 持久 last_test，核验连接/目录 revision，测试观察不改作者配置 revision。collector 异常保存 collection.run 失败终态并发结构 job.failed，HTTP 可读真实最近 run；原观测先保存的事实不被抹掉。 |
| RC13 媒体 | engine/background 从 worker 持久观察读取，附 execution owner/代次/时间；失活状态未知，不把 HTTP 未启动副本当真实引擎失败，也不在 HTTP 启第二后台。 |
| RC14 空闲维护 | 先拿 lease 后工作；复用 Procrastinate next_due 与代次合并；Repo.put 同事务唤醒受影响任务。无业务已知族等下一业务 due/空闲五分钟，未知清理族有界退避至 300 秒。没有新增轮询器。 |

## 原生配置入口与必需参数

所有写入仍使用现有 client_request_id / expected_revision 和授权上下文，开发未实际调用。

- `POST /api/v1/speech/connections.save`：transport=native|mcp|edge|compatible、account_id、quota_group；native 需要实际 API key（endpoint 可复用既有值），MCP 通过 connections.connect/complete OAuth，Plus 计费走该连接；还保留 connections.refresh/disconnect、voices.search/clone、preview、transcribe。voice_profile 后续由用户决定：profiles.save 需要 actor_id/connection_id/voice_id/model，可选 language/output_mode/dictionary_ids/vocal_defaults。当前**不创建** voice_profile。
- `POST /api/v1/images/engines.save`：engine=comfyui|nai|gpt_image、model、quota_group、account_id；ComfyUI endpoint；其他引擎实际密钥。workflows.import 需要 name/graph/bindings/outputs。profiles.save 需要 actor_id/engine_id/character，ComfyUI 使用 workflow_id，可附 references/outfit_defaults/style。协调者可复用原 workflow/engine 与作者外观，不能补虚构密钥/外观。
- `POST /api/v1/reactions/packs.import`：name、files（MediaRef 或 data_base64）或 archive_media/archive_base64、actor_ids/scope_ids；既有上限 1000 文件/128 MiB 解压量/16 MiB 单素材。授权导入后按 asset id 调 annotate（模型路线 reaction.annotate 需 vision）再 index（embed 路线，1024 维 profile）。原 AstrBot 库未读写；67 GIF 的迁移/语义化与模型上传由协调者后续受控处理。
- content providers.save、既有 browser/session、B站账号/engine、Life routine/active contact/公开投递权限均复用现有管理入口。需要实际账号或浏览授权的场景仍待最终配置与统一验证。

## F 字段合同

- growth/view.review：revision、last_error、last_reviewed_at、blocked_material、material_key、cursor、attempt_id；batch={material_key,start_cursor,end_cursor,watermark,has_more,inputs:[[episode_id,revision]],legacy_material?}（去 context）；pending_inputs/pending_original_requests；legacy_state=requests|episodes|complete、legacy_request_cursor/legacy_episode_cursor/legacy_missing；recovery=observe_original_attempt|null。can_request_review 是唯一是否可请求新审的判断，unknown 不提供重发入口。
- Media.engine：state=ready|unavailable|not_verified、version/code、observed_at、generation、owner_id/owner_live/stale。无观察时仅返回 not_verified、null version/time、missing code、owner_live=false；不存在 available 字段。
- Media.background[]：name/state/code、next_due_at、generation、owner_live；持久 body 包含 worker_owner/started_at/observed_at/idle_runs。这里代次字段是 generation。
- overview.database：engine=PostgreSQL、size_scope=schema、size_scope_name=capability_media、size_bytes（本域表/索引/TOAST字节）、query_ms；不是整库或 SQLite 主文件。
- synthesis availability.reason：speech_not_configured / voice_profile_not_configured / speech_profile_connection_unavailable；images：image_engine_not_configured / image_profile_not_configured / image_profile_engine_unavailable。沿 capabilities/catalog/view 的 availability/can_execute 消费。
- 天气 observe：原 fetched_at/expires_at/source_refs/stale 加 recovery/retry_after；routine.planning 没有新造 attempt 或恢复动作，只补 planner_output_incomplete 技术状态码。

## 未执行用例与依赖

已编写/更新 tests/models/test_completion.py、test_native_settlement.py；tests/companion/test_growth_output.py、test_growth_budget.py、test_growth_legacy_recovery.py；tests/capabilities/test_runtime_observations.py、test_available_materials.py；tests/life/test_planner_execution.py；以及前述渠道 NUL、核验元数据、授权外 recall 相关用例。它们覆盖原 unknown 身份、低 sequence 晚提交、预算缩批/单项阻塞、无部分写入、legacy 空队列转换、明确 failed 与 incomplete 的真实 Broker 路径、worker 投影失活、空闲调度/变更唤醒、语音未绑定、媒体截断、天气 hold 和精确续接。没有执行结果可报告。

窄修复另补 tests/life/test_imagined_repair.py：原身份/完成时间保留、当前登记时间、幂等、真实动作/错关联/活动或 Episode 漂移拒绝、事务回滚、重读撤权和既有管理权限；同样仅编写，未运行。

依赖 A 唯一注册 `0034_companion_growth_batches`（最终 companion/growth_batches.sql）和 `capabilities-0002-runtime-observations`（capabilities/runtime_observations.sql，位于 capabilities-0001 后）；迁移均未在本包执行。依赖 B 的 Memory observer/episodes_by_refs/反思事务与版本 CAS、Conversation quote/作者元数据和装配。前端由 F 单写。集中验证应包括事务回滚/并发锁序、预注册队列适配、启动旧状态转换与原 unknown；真实模型/付费理解/QQ 投递只在整批实现集成完成、相应真实授权与配置就绪后执行。

## 集中验收后的 Models 窄返修

基线 `5c4d738`，分支 `codex/memory-closure-model-fixes-20261101`。root 的 backend-validation-01 报告两个非流式 Responses 结算用例失败；本次只静态阅读对应合成 trace 和模型路径，没有重新运行测试、构建或模型。

根因是非流式 `ResponseCollector` 把物理终态识别放在 `output` 字段分支内，旧夹具只提供明确的 status/usage，没有 output。流式终态事件在独立分支识别，因而通过。现在明确 native status 为 completed/incomplete/failed 即登记终态及用量，输出项解析只负责内容；缺状态、queued 或 in_progress 仍不结算。`failed` 继续结算为物理失败并保留供应商错误；`incomplete` 仍是已结算的请求和不完整内容，不进入 unknown hold。

`test_native_settlement.py` 保留原 failed/incomplete、error_code、容量释放无 hold 和 active 清理断言，增加 output=[] 与无 output 两种夹具，分别经过流式/非流式真实 Broker 路径，同时断言原 native、usage 和实际计费单位保留。`test_completion.py` 补缺失/非终态 status 不可结算的保护用例。以上仅编写，交 root 定向集中重验。

## PG 集中验收后的旧 Growth 与维护调度返修

承接同一分支的 `f980500`。root 的 backend-pg-01 首次业务执行中，三个旧 Growth 用例在 `GenerationRequests.pending_materials` 遇到 `boolean -> unknown`，一个维护调度用例保留初始 next_due_at 而未退避。MC-C 只阅读对应合成 trace、调用方和 diff，没有执行测试、构建、数据库或模型。

- `pending_materials` 显式括起全部 JSON context 路径，修复右侧 `@>` 与 `->` 结合导致的 SQL 类型错误。仍按 principal、actor 和双向包含的完整 scope 集合隔离；仍保留 prefix/after/limit、原冻结上下文/attempt，以及 request 清除后只按精确 context_key 返回失效身份的分支。
- 维护原初始 due 来自应用时钟，领取条件用 PG now；在分离主机时，“立即”可能被当作未来任务并跳过。现 `schedule(name, due=None)` / `wake_sync(..., due=None)` 明确使用 DB now，activate 与已经到期的业务变更走此语义；真正未来业务 due 保留绝对时间和领取门槛。空闲五分钟及未知族退避也按 DB now 计算，未缩短原 lease 或移除代次过滤。
- 任务收尾在同一事务完成 owner CAS、释放 lease、保存观察、保留运行期间更早的业务 due、推进代次及入队；旧 owner 丢失 lease 后无法续排/覆盖新 owner。删除仅为原两段收尾使用的 force 分支，没有新增轮询器，保持关闭状态不入队后继任务。

已有三个 Growth legacy 场景和维护代次场景仍保留；补写 actor/少量或过宽 scope 拒绝、scope 重排仍读取同一个原请求、失效请求精确上下文、应用时钟正负一小时、未来任务不提前执行、运行期间变更与 busy 旧代失效、owner 漂移不可续排用例。维护退避断言以实际调度权威 DB now 为参照，仍要求超过四分钟。相关文件为 `tests/companion/test_growth_legacy_recovery.py`、`tests/conversation/test_generation_recovery.py`、`tests/capabilities/test_runtime_observations.py`；全部未运行，交 root 收齐修复后集中定向复验。迁移和产品装配错误由其他任务负责，本返修未修改迁移或共享装配。
