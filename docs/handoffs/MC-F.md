# MC-F：记忆时间、证据与业务状态前端

基线 `e752d5c`，分支 `codex/memory-frontend-20261101`。主体代码提交 `29bee05`；本交接所在的后续提交补齐共享整理展示的样式加载。修改只在 `web/` 和本文件内，没有修改后端、根任务文档、其他工作树或生产配置。

用户明确要求所有实现完成后统一验收。本交付是**代码完成、尚未运行验收**；没有运行 tests、typecheck、build、候选程序、浏览器、模型或实机请求。执行过 Prettier 写格式、Git diff 静态阅读和 diff 空白检查，不将其称为功能验证。

## 页面与复用

- Memory 页面、人物认识与群页使用同一 `MemoryTimeFilter`，支持 current / valid_at / known_at；游标绑定范围、集合和时间参数。事实与 hypothesis 分开展示，旧未知时间仍显示未知，不用观察时间或迁移时间补首次记录时间。
- `EvidenceDetails`、`EvidenceRelations`、`EvidenceHistory` 复用 Sources 原文组件和现有权限读取。显示作者/assertion、完整条件、事件/有效/记录时间、状态、当前 head、原因、支持组、反例/派生依据；关系展示 from/to 两端完整证据。claims/history、matters/history 都是按实际 cursor 的有界分页。
- Claim 更正每次先读 `claims/get` 的 current；Matter 更正每次先读新增的 `matters/get` 当前 head，再走原 correct/save 和 `expected_revision`，不把历史 revision 写回当前。既有披露、遗忘、命令幂等与回执机制保留；历史结果不直接披露。事项完成依据区分 human_report 与 action_receipt，未知依据不宣称工具成功。
- 群页使用 projection.group.subject_id 作为共享主体，身份名称仅取合法 group_consolidation.subjects。移除旧话题正则筛选的默认页，改为真实批次、固定 targets、已提交 target_results、no_change 与 unresolved；目标名单不等于完成回执。当前处理窗口与历史事实时间查询分开说明，完整记录继续走 episodes 分页。
- `EpisodeProcessing` 在 Memory、Life 时间线和群页共用；只依据服务器 action 显示原执行观察或重取材料。wait_for_new_evidence、configure_budget、legacy_application_abandoned 分别说明，未知结果不伪称失败或成功。按需读 extraction/status，显示片段位置、实际批次、逐人覆盖和缺项。
- `MemoryEvidenceSearch` 直接调用统一 query，五类证据可选，完整列表与 ranked 候选分别说明并保留实际游标；`MemoryIndexRebuild` 在现有检索设置中复用命令接口，默认五类、类型可选、`i1:` 游标按原样续批，只在显式点击时排队，读取页面不会自动重建。
- Growth 展示固定 batch/实际 Episode 版本、pending_inputs、pending_original_requests、legacy 进度、blocked_material 与原 attempt。unknown 只提供读取状态；只有服务器 can_request_review 允许时，才使用原 review 命令。人物认识、想象活动和真实动作继续区分。Life planner_output_incomplete 有明确文案，没有新增未知规划重发入口。
- Media 概览和运行页根据 PostgreSQL / size_scope='schema' / size_scope_name='capability_media' 展示本域表、索引与 TOAST 字节。engine/background 读取持久 owner 观测；缺观测、过期或失活 owner 显示未知。时间格式同时接受 PG ISO 时间和原 Unix 时间。
- Speech、Image 和 Life 活动页通过既有 capabilities/catalog/view 展示角色 readiness；共用既有设置表单、目录与状态文案。Fish 未绑定音色明确显示角色状态，没有代选或保存声线。Image profile 只匹配当前 actor，避免将唯一的其他角色形象作为当前绑定。Reactions 原生包/上传/索引/选材、Image 自拍/相册/生成、Life 主动活动和 Content 读取入口继续复用；没有处理 67 GIF 资源、真人账号、人物草案或生产接线。
- Infrastructure `NodeConnections` 已按真实 last_test.checked_at/state/code 显示观测及错误，本次静态核对后沿用，不新增另一套测试状态。

## 必须共同集成的接口

| 所有者 | 接口 / 字段 |
|---|---|
| MC-A / MC-B | claims / matters / projection 的完整 revision 字段、claims/history、matters/history；extraction/status / resume 的 action、batch_id、fragment_cursor、target_results、unresolved |
| MC-D | POST memory/matters/get（body.matter_id，只读当前 head）；query 的五类 EvidenceUnit / list 稳定分页 / ranked coverage；合法关系两端证据；经来源与当前主体授权过滤的 targets，以及 group_consolidation.subjects |
| MC-B | index/rebuild：默认五类或 kinds，稳定 i1 游标，queued/kinds/next_cursor/coverage/replayed |
| MC-C e95ffe7 | Growth review.batch、attempt_id、pending_inputs、pending_original_requests、legacy_state/cursors/missing、blocked_material、recovery、can_request_review |
| MC-C a79e575 / a14be27 | Media engine/background 持久 owner 观测与 generation；database.size_scope='schema' / size_scope_name；原 Infrastructure last_test |
| MC-C 4977585 / c7bfba2 | Speech / Image readiness 精确 reason；Weather recovery='observe_original_attempt'、retry_after、stale/fetched_at；Life planner_output_incomplete |

以上 MC-C 提交只通过已提交 Git 内容静态对齐，没有取用其未提交工作树，也没有将后端提交混入 F 提交。

## 已写但未运行的场景

`web/tests/memory-closure.spec.ts` 新增 10 个 Playwright 场景，使用隔离 native-fixture：

1. known_at / valid_at 请求、Claim 历史 cursor、事实/假设分区，以及更正读取最新 head revision。
2. Matter 历史 cursor、人类报告与真实回执的区别，以及历史页编辑仍使用当前 head。
3. Episode 依据 action 恢复；failed+wait_for_new_evidence 不出现重取按钮，unknown 只观察原执行；imagined 明确主观语义。
4. 群固定目标、已提交 no_change、未决及服务器实际共享主体。
5. 五类型索引的显式排队与不透明游标续批。
6. query 的完整关系两端、list cursor 和 ranked 不完整说明。
7. Growth 固定版本、pending/legacy 与 unknown 原 attempt；无替代 review 写入。
8. PostgreSQL schema 大小与失活工作进程状态。
9. Fish 未绑定音色的 readiness，不自动写入 profile。
10. Weather 保留陈旧读数、读取原未知请求状态及正确天气设置入口。

`web/tests/native-fixture.ts` 补齐默认只读 Memory / capabilities catalog 空响应。没有新增依赖、测试框架、基线、快照或 CI 门禁。

## 统一验收与局限

协调者在 A/B/C/D/F/G 全部代码完成且接口集成后，再运行受影响构建（已有 build 包含 typecheck）及本 spec、既有相关原生页面/Life 状态用例。开发阶段没有执行这些命令，没有运行截图、移动端或真实权限/来源撤回浏览器验证，不能据静态阅读声称已通过。

人物/群页依据合法服务器返回显示名称；无法证明的作者与旧时间明确未知。实际模型语义、费用、跨进程恢复、真实 QQ/外部能力、图片成品与 GIF 原生导入仍由协调者统一验收和唯一生产接线。用户暂不绑定 Fish 音色这一明确选择仍保留。

## 第一轮统一验收后的集中返修

返修分支 `codex/memory-frontend-fixes-20261101`，基线 `5c4d738`。协调者已完成原批构建（含 typecheck）及 36 个桌面/移动端页面场景，原记录为 16 通过、20 失败。本次只读取该批日志、error-context 与 trace.network，不在返修工作树运行测试、构建、候选程序或浏览器。

- 9 个新增场景共同被夹具拦截规则阻断：真实 `client.selectedEndpoint` 会把 actor/person/conversation 选择器附加到 POST URL；原 spec 只匹配无查询参数的完整 URL。现有 trace 显示 `memory/claims?actor_id=actor` 取得默认 200 空页、`memory/index/rebuild?actor_id=actor` 落入默认 404、`capabilities/catalog/view?actor_id=actor` 取得默认空目录。全局 Media 没有 actor 选择器，故原 Media 场景通过。
- 在既有 native-fixture 增加精确 API pathname matcher，并让新增 spec 统一使用它；查询参数与真实 URL/body 选择器全部保留，claims 与 claims/history 也不会因前缀匹配互相覆盖。额外保留 Claim 读取和索引写入的 URL actor 与 body actor 一致性断言；没有删场景或放宽原业务断言。
- Life 的旧场景还发现真实展示回归：共用 CoverageNote 遗漏 coverage_unknown 的原有说明，EpisodeProcessing 没有展示旧 Episode 的 no_original_material。现补回清楚的覆盖/无原件说明，在 Life 和 Memory 共用展示；旧 extraction_state 只用于说明记录，恢复按钮仍完全依据服务器 action，不按旧失败字符串构造动作。
- 覆盖原始诊断改为按需打开现有 DetailCard，日常页面保留人类可读的缺项说明；技术码和原 JSON 在用户打开诊断时仍可查看。来源读取权限失效后不会回退展示旧提取状态。

本次只做代码修复、格式整理与静态 diff 阅读；当前返修代码尚未复验。协调者收齐本轮各包修复后，统一重验这批受影响页面场景及必要构建，不复跑未受影响的真实模型/生产操作。

## 第二轮集中返修：精确标签关联

分支 `codex/memory-frontend-repairs2-20261101`，从集成 `753eaa2` 开始。协调者第二轮构建（含 typecheck）通过；24 个受影响页面场景中 20 通过，剩下两个场景在桌面/手机各失败一次。原 native 12 个有效通过结果继续复用。这些是协调者已执行的结果，本返修没有另跑测试、构建或浏览器。

只读 trace 与 error-context 证明：Claim 历史卡片仍显示“周末散步”，`claims/get` 已返回 200，当前 v3 编辑框已有“当前认识”；query 第二页也返回 200，后续证据和读取方式表单均保留。因此没有用更改卡片定位器或删除断言掩盖数据问题。

两处精确标签定位都受到原生嵌套控件的子文本影响：trace 中 `LABEL` 的内容分别为“更正后的内容”加 `TEXTAREA` 初始正文，以及“读取方式”加 `SELECT` 两个选项正文。现使用 React useId 和独立标签 span，通过 `aria-labelledby` 明确关联 Claim 内容/条件编辑框与查询方式 select；控件的正文或选项变化不会改变精确标签文本。原场景、原精确 getByLabel、当前 head / expected_revision CAS、完整证据分页和 ranked 不完整断言均保持不变。

本次仅上述两个组件和此交接变化；没有改动已通过页面的输入状态、请求参数、游标或业务逻辑。只做格式整理及静态 diff，完成后交协调者与后端本轮修复一起统一复验。
