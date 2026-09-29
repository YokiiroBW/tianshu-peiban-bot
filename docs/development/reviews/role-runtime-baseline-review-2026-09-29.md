# 多角色运行时基线架构审查

日期：2026-09-29
审查性质：只读架构与验收审查；未检查角色实现 worktree，未修改产品代码，未运行测试。

## 范围与基线

依据 `docs/development/role-runtime-plan-2026-09-29.md` 审查 Platform、Companion、Memory、Model Gateway 已集成实现，固定组件提交如下：

| 产品 | 基线提交 |
| --- | --- |
| Platform | `9bed67e2de8a101f55f9a95e9a8b1288719ce8c2` |
| Companion | `c24230216c41818c425d0b0a61f2f23ba0dfe578` |
| Memory | `10214b2f7dbfb6990e986f38f92c8103e07d6b9e` |
| Model Gateway | `e4f112f02d28a1ded134126feed33f15e003ec20` |

读取位置为 `C:/YOKI/Codex/tianshu-peiban-bot/projects/{tianshu-platform,tianshu-companion,tianshu-memory,tianshu-model-gateway}`。四个检出均在任务卡指定基线；Platform 有未跟踪 `build/` 输出，本审查只看提交中的源码、配置示例和文档。以下所称“缺口”是动态角色相对当前静态基线尚未具备的运行机制，不代表已验证的漏洞。

## 必须保留的现有授权与隔离边界

1. **Actor 是受信登记事实，不能来自网页或模型自报。** Platform 的输入登记项列出 `actor_entries` 与 `default_actor_ids`；路由记录按输入命令持久化有效 actor 列表及 routing version，再为每个 actor 建独立 origin。origin 把登记 actor、账号、channel、audience、路由用途和撤销/过期状态组合成可信 scope。网页对话先在当前已登录主体的登记项中解析 `body.actor`，Core 再核对 origin、channel binding、角色存在性和账号 person binding。动态角色必须经过同一正式授权链，单纯增加一个 actor 字符串、修改进程内配置或放宽通配符不能视为授权完成。
   位置：Platform `services/platform/sources.py:25-85,188-296`、`services/platform/origins.py:43-81`、`services/platform/web_dialogue.py:55-80,163-217`；Companion `src/tianshu_companion/core.py:244-276,470-501`。

2. **各 owner 的名单互不替代。** 本基线没有中心化的动态角色授权目录。Memory `callers.<service>.allowed_actors` 是服务读取来源 actor 的服务器端 allowlist，并在每次 origin resolve 后核对；`event_scopes` 是后台消费/来源检查使用的完整精确 scope 列表。Platform 网页 Persona 的 `allowed_subjects` 是可读 subject 闭集，`apply_subjects` 还必须是其子集。Bot adapter 创建连接时只接受配置 `actors` 中的 actor，Platform BOT 连接再将可用 actor 限定到输入 origin 的 actor 登记交集。新增角色不能仅加入其中一个列表后推导出记忆、人格编辑、BOT 发言或网页访问权限。
   位置：Memory `src/tianshu_memory/auth.py:32-110`、`src/tianshu_memory/app.py:150-170`、`docs/runtime.md:21-35`；Platform `services/platform/persona_page_config.py:350-403`、`services/platform/bot_adapters.py:119-155,533-601`、`services/platform/bots.py:370-401`。

3. **角色、用户与会话是不同维度。** Core scope 包含 `actor_id`（运行角色）、`person_id`（真实用户）、`audience` 和 `conversation_id`。Memory 由 Platform 当前 origin 给出 actor/account/channel，按当前账号 person binding 补齐 person；select 要求请求完整 scope 与可信 scope 相等。相同 person、甚至相同群会话不意味着可跨 actor 读取记忆。Memory 的跨用户画像是另一套显式 `public_preference` / `group_only` 投影规则，并限制目标、来源、可见性和 scope version。原始消息来源仍以引用/证明进入记忆链，不是创建角色时批量开放历史私聊。
   位置：Companion `src/tianshu_companion/source_sync.py:330-420,560-603`；Memory `src/tianshu_memory/service.py:74-95,361-433,531-666`、`src/tianshu_memory/profiles.py:120-223`。

4. **Persona 不是 Role，也不是权限。** Companion 启动时从部署配置加载 `roles`/`bindings`；若启用注册人格，Core 在 turn 准备时通过 `_pin_role(actor)` 固定该 actor 的人格版本，之后重试仍使用已固定快照。可复用 profile 与角色记录共用 Persona 存储，但 `kind="profile"` 的档案不会因为存在就成为 Core role；当前没有持久的 `role → profile_id + revision` 绑定。网页 Persona 的读取和应用 subject 另受 Platform 闭名单限制。因此动态角色应固定显式选中的档案修订版，不能把 profile ID 当 actor，也不能让档案后续发布暗中覆盖既有角色。
   位置：Companion `src/tianshu_companion/app.py:441-455`、`src/tianshu_companion/core.py:131-141,1084-1096`、`src/tianshu_companion/personas.py:1211-1219`、`docs/personas.md:7-10`；Platform `services/platform/web_persona_author.py:68-70,198-203`。

5. **当前模型选择是全局默认，不是按角色选模型。** `source_sync` 在接收新 actor turn 前通过 `DefaultModelSelector` 为该 turn 取得版本 lease，并在 collection/turn 中固定 `config_version`。Platform `ProviderAuthority.select` 校验 actor/person/audience/conversation 和 companion service 身份，但实际选取 `provider_catalog.view()["default"]`；actor 参与 turn grant 的 scope digest，不参与 provider 选择。Gateway 再核对固定版本、provider workload binding 和 grant。当前设计没有“角色绑定 provider/model”的语义，故角色模型选择只有在 Platform 发布对应角色配置、每轮固定其版本，且录制上游请求证明确实命中时才算生效；显式线路失效时必须失败关闭，不能退回全局默认或其他角色线路。
   位置：Companion `src/tianshu_companion/source_sync.py:604-635`、`src/tianshu_companion/model_selection.py:19-95`；Platform `services/platform/provider_authority.py:21-111`；Gateway `src/tianshu_gateway/config.py:116-140`、`src/tianshu_gateway/routing.py:76-146`。

6. **BOT 的输入授权、回复连接和观察策略彼此独立。** BOT 创建将 actor 固定在连接的 `actor_ids`；接收消息要通过来源登记、作者允许列表和 route defaults。回复端依 actor origin 的 channel 与 actor 找到唯一有效连接，并以 `(conversation, actor, turn, segment)` 限制重复。相同物理 endpoint 不允许两个启用连接同时拥有相同 actor。观察策略默认是 `observe_only` 且 `actor_id=null`；切到白名单/黑名单发言模式仍需显式选择已配置 actor 和受限目标。创建/启用运行角色不能自动生成 BOT 连接、扩大作者列表或把观察改成发言。
   位置：Platform `services/platform/bot_adapters.py:119-155,533-601`、`services/platform/bots.py:157-189,665-768,820-870`、`services/platform/bot_observation.py:53-64,550-583,948-999`。

7. **日记、短上下文、出站与角色 scope 共用角色边界，但各自有独立规则。** 日记按 life actor/day 保存，素材标为 fictional；阅读还要求日记已发布且 actor reader ACL 命中。短上下文以 actor、audience、conversation、channel 为边界，私聊进一步要求完整 person scope 相同；只纳入仍当前有效的输入及已确认发送成功的回复，不纳入 pending/unknown。Core memory outbox 携带 turn scope、来源和回复状态，重启遇到 `submitting` 标为 `unknown`，不自动重放；Core 出站回复和 Platform BOT reply queue 也各有独立幂等账本。不能为了动态角色把 actor 从 key、scope 或幂等键中移除。
   位置：Companion `src/tianshu_companion/life.py:470-506,681-695`、`src/tianshu_companion/short_context.py:50-145`、`src/tianshu_companion/core.py:900-994,1710-1888`、`integrations/nonebot/tianshu_nonebot/journal.py:21-154`；Platform `services/platform/bots.py:665-768,820-870`。

## 固定基线的实现缺口

- **运行角色仍是静态部署映射。** Companion `app.py` 把 `config["bindings"]` 和 `config["roles"]` 直接交给 Core；Core 授权只判断 actor 在静态 binding 的 `actor_ids` 和启动加载的 `roles` 中。当前没有角色启停持久状态，也没有网页角色生命周期 API。动态管理必须由 Companion 作为唯一角色事实 owner，保证写入幂等、版本冲突、重启恢复，并让 ingress 和执行边界读取同一生效版本。
- **运行角色到可复用 Persona 的版本绑定尚未存在。** Persona revision/history 已有版本固定机制，但角色配置只以 actor subject 查人格，profile 只是可复用档案目录；不能由网页 profile 选择器自行推断关联已经落库或生效。
- **角色级模型选择尚未存在。** actor-aware turn grant 仍选择全局 default。UI 显示不同模型、或仅变更 Memory/Core context，不会改变 Gateway 看到的 provider/model。
- **停用在途语义尚未定义到 Role。** 当前 BOT connection 的 disable 会将 pending/claimed 出站行改为 `unknown`；Core 也只在 turn 状态、取消标记及回复 outbox 边界处理。这些不能直接等同于“角色已停用”。角色停用需要分别定义 queued、preparing、generating、已生成未提交和已越过外部调用边界时的处理；尤其不能把已经可能发送的未知结果重新提交。
- **生活与网页名单不随角色自动扩展。** Diary life actor、Bot adapter `actors`、Persona `allowed_subjects/apply_subjects`、Memory `allowed_actors/event_scopes` 分属不同 owner。角色管理流程需要返回每项授权生效/未生效状态，不能在仅完成陪伴侧保存时报告“角色已可用于全渠道”。

## 必须覆盖的失败场景（12 项）

| # | 场景与断言 | 主要代码锚点 |
| --- | --- | --- |
| 1 | **网页伪造 actor / 复用其他人的 origin。** 用当前网页会话提交未列入该 conversation 的 actor，或提交 actor A 的 origin 搭配 actor B、其他 account/channel；读历史和发消息都必须拒绝，不能创建可见 turn 或跨账号提交记录。撤销、过期、修改登记摘要后旧 origin 也拒绝。 | Platform `web_dialogue.py:60-80,89-127,163-217`、`origins.py:43-81`；Companion `core.py:244-276` |
| 2 | **只改一份名单造成越权。** 新 actor 已加入 Core roles，但未列入 Memory `allowed_actors`、Persona `allowed_subjects` / `apply_subjects` 或 adapter `actors` 时，对应能力各自应拒绝；反过来，列入 Memory 名单不能使网页能读/应用人格或让 BOT 发言。空列表/未知 ID 不能退化成通配。 | Memory `auth.py:101-110`；Platform `persona_page_config.py:374-403`、`bot_adapters.py:533-541`；Companion `core.py:244-260` |
| 3 | **同 person、同 conversation 的 actor A/B 串记忆。** 用 A 的来源、短上下文或 turn/outbox 查询 B 的 actor scope（以及用 B 的 origin 查询 A）；所有跨 actor 请求必须拒绝，不能因 person 或 conversation 相同而命中另一 actor 的记录。群内按同 actor 复用上下文仍须符合原来的 group 规则。 | Companion `source_sync.py:330-420`、`short_context.py:75-100`；Memory `service.py:74-95,361-433` |
| 4 | **伪造 Memory scope 或越过共享画像边界。** 请求体把 `requested_scope/requester_scope` 改成另一 actor、person、private conversation，或把 group-only profile 当作 public；不能返回/写入私有资料。跨角色 identity 共用不得自动产生 profile share。 | Memory `service.py:74-95,361-433`、`profiles.py:120-223`、`auth.py:101-110` |
| 5 | **BOT 角色/作者/物理路由伪造。** 创建连接时 actor 不在 adapter actors 或来源 actor 交集、输入作者不在 `allowed_authors`、私聊作者不等于私聊目标，均拒绝；同一物理目标同 actor 不能被两个启用连接争抢。A/B 两个连接并发时各自只收自己的 actor 回执。 | Platform `bot_adapters.py:119-155,533-565`、`bots.py:157-189,370-401,665-768` |
| 6 | **观察账号意外变成主动发言。** 新建或启用角色后，未修改观察策略的连接仍为 `observe_only`/`actor_id=null`，不创建 reply connection、不排 BOT 发言；未显式设白名单/黑名单或选择合法 actor 时必须不发送。 | Platform `bot_observation.py:53-64,550-583,948-999` |
| 7 | **角色 A/B 选不同模型、全局默认并发变化。** 使用真实应用服务，捕获两个 turn 的上游请求：A/B 必须各命中其已固定的 provider/model；变更全局 default 或并发修改角色配置不能改写已接收 turn。被撤销、失效或不可用的显式 provider 应报明确失败，不静默使用全局 default/另一角色线路。仅检查 UI、selector 返回版本或网关替身不足以通过。 | Platform `provider_authority.py:21-111`；Companion `source_sync.py:604-635,746-797`；Gateway `routing.py:76-146` |
| 8 | **Persona profile 修订与 turn 准备竞态。** 两角色可显式绑定同一/不同 profile revision；编辑并发布新 revision 后，只有明确应用的角色在后续 turn 生效。已准备 turn 的重试、依赖等待和发送继续使用 pinned revision；无已发布修订、已退役角色或跨角色 revision 引用时不调用模型。 | Companion `core.py:1084-1141`、`personas.py:327-343,1211-1219`；Platform `persona_page_config.py:374-403` |
| 9 | **日记和短上下文串角色或混入未确认回复。** A 的 diary/material/access 不可由 B 读取；A 的 recent context 不包含 B 的 turn，也不含 pending/unknown/sending 回复。角色日记保持 fictional，与现实聊天和 Memory 长期记忆分离；读者不在 ACL 或日记未发布时无正文。 | Companion `life.py:470-506,681-695`、`short_context.py:50-145` |
| 10 | **角色停用跨越在途阶段。** 在 admission 前、queued、preparing、generation 中、能力调用前、回复生成未提交、outbox pending/blocked 和已开始外部调用各时刻停用 A：停用后不能开新 turn、执行新的能力或提交未提交回复；已有历史不丢；在途继续/取消/封存 unknown 的规则一致且可恢复。停用 A 不影响 B，也不能让 A 的排队回复换到 B 的连接发送。 | Companion `core.py:397-429,461-501,1023-1054,1153-1165,1318-1343,900-958,1795-1888`；Platform `bots.py:403-432,820-870` |
| 11 | **重启、重复事件与停用 BOT 连接竞态。** 重复输入/重试不得扩展已固定 `effective_actor_ids`；重启后 journal 与连接 ID、BOT replies 与 actor/turn/segment 仍匹配。connection disable 将 pending/claimed 标 unknown 后不得 claim/replay；Core memory outbox `submitting` 恢复为 unknown 后不自动再次提交。对已可能越过网络边界的发送只能核对结果或保留 unknown。 | Platform `sources.py:188-219`、`bots.py:403-432,820-870`；Companion `journal.py:21-154`、`core.py:960-994,1795-1888` |
| 12 | **多角色竞争共享模型容量。** 多 actor 同 provider 和不同 provider 混跑时，Companion 全局模型 semaphore 与 Gateway 全局/每 provider 容量仍生效；一个角色失败、lease 失效或 provider 排队不能扩大并发，也不能阻塞另一个未饱和 provider 的独立 turn。 | Companion `core.py:143-146,1265-1310`；Gateway `scheduler.py:235-260,546-585` |

## 验收判定

角色页面新建成功不等于角色已运行。验收必须分别证明：角色配置在 Companion 重启后仍存在；Platform 网页/各 BOT connection 可选到且只获得明确授权 actor；Memory 每轮基于新鲜 origin 解析并校验完整 scope；Persona revision 和 provider/model 都被固定到该 turn；跨角色对话、上下文、日记、Memory outbox 和 BOT 回执各自正确隔离。任何未接通的 owner 应显示未应用/不可用并阻止相应路径，不得把局部持久化显示为全链路生效。
