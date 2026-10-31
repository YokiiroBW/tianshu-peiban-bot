# CR18 前端交接

状态：`implemented_not_tested`，首轮可集成代码；CR18B/C/D 与 CR18 最终接线继续中，当前不是整卡完成。基线 `e034276dfb757b7dd64cd2f56e952d81193a5248`，分支 `codex/cr-frontend`。唯一写入范围 `web/**` 与本文件。

## 冻结来源与保留

只读确认 Platform HEAD `21bdb7b9e11e18d47111e656e829edd331a77b14` 包含部署前端 `911a1dc60318abad76287013a05afacd1e63f9f3`；两者 `apps/web` Git tree 均为 `940b85b8bbaf909e52af3dabaabf181f1c4257be`，目录 diff 为空。迁入使用协调者冻结的 platform/apps/web，保留当前认可页面及 8 个 public 文件。没有从较旧根协调副本覆盖页面。

完整迁入应用壳、角色/Web聊天/人格/设置、生活/日记/创作书架/衣柜/阅读/经历、小屋（Three.js及插画）、用户/群画像/本人记忆/人物详情、视频/音乐/书籍/漫画/下载/账号/存储、知识/资产/项目经验、设备/容器/Dockge/节点、插件全管理页、模型/连接/技能/天气/检索/任务/链路图，以及原有视觉和用例。没有新增依赖。

## 首轮实现

- `web/` 自有 manifest/lock、Vite/TypeScript/Playwright 入口，移除旧 apps/web 路径依赖。
- 数字段地址替换为语义路由；新增角色成长/约定、语音、图包、图像引擎入口。账号关联入口进入现有人物空间；原未实现身体与活动仍准确显示不可用。
- `src/api/client.ts` 为唯一 fetch：同源Cookie/CSRF、标准错误、取消、CommandResult、原action/attempt/receipt、未知结果保留；统一读取/命令/二进制媒体。旧 sessionTransport 退出运行树，不存在 `/api/web` 自动回退。
- 正式 SessionView 平面字段由 `src/api/session.ts` 转为 UI 显示数据；各领域 HTTP 包装复用同一客户端。
- 作用域读取支持会话变化/角色变化后的取消及旧结果隔离；命令在结果未知时保留原请求标识，统一反馈展示执行与投递差异。
- 按能力所有者 `067d1dc` 冻结投影实现 Fish连接/Plus OAuth/独立余额/实际功能、音色查询/创建/角色绑定/试听/词典/文件转写；图包导入导出/动图/标签与用途纠错/标注索引/语义检索；ComfyUI/NAI/GPT Image配置、工作流导入、形象参考、场景提示预览、实际生成/取消/unknown核对、相册原件。均调用正式域API，不内置生产mock或成功替身。

## 依赖及继续工作

基础契约原提交 `ec2fc7c`、能力契约原提交 `067d1dc` 已在本分支 cherry-pick，仅作为只读正式输入；后端文件未由本实现者修改。

下一轮需核心所有者冻结并实现 identity/roles/personas/conversation、sources/memory/people、companion/life/growth/commitments、channels、runtime/settings 的具体操作与投影。现有保留页当前已统一传输路径，但还包含旧业务 DTO 和跨产品信封（特别 life runtime、角色legacy、关系score/stage、记忆/观察范围），这部分不以首轮迁移冒称新核接通。成长与约定新页使用已提交给协调者的待定字段，服务未注册时显示真实请求失败，不假成功。CR18B 需要随核心定稿删除旧积分/阶段/legacy分支并增加认识纠正；CR18C 需完成消费覆盖与完整ComfyUI参数绑定体验；CR18D 需接模型Broker/OneBot完整能力矩阵/真实任务资源等待。旧ComfyUI专用组件当前仍作为待替换源码保留，主生活相册入口已经改接三引擎页面。

语音/图像/图包界面按正式端点与DTO编码，能力服务实现尚在另一泳道进行，当前不能宣称实际音频/图片/插件或付费API验收。旧前端合成fixture需在新核心接口齐备后更新；新写统一传输与语义路由用例，尚未执行。

## 实际检查与限制

按用户“全部代码完成后统一测试”要求：未安装依赖、未运行测试、typecheck、build、浏览器、真实账号/服务、生产或收费调用。只阅读源码/正式契约与diff；静态相对源码import检查未发现缺失路径，源运行树搜索未发现旧 `/api/web` 或旧产品源码引用。此检查不替代编译或行为验证。

协调者可集成首轮前端提交；前端唯一写入归属保留，后续完成全卡接线后再集中验证。


## 第二次源码交接：传输与真实会话（implemented_not_tested，前端仍在重构）

明确读/写操作注册，唯一CommandResult解析；写取消、200正文截断、网络中断保留原client_request_id/修订和未知状态，读取消静默。所有能力pending只轮询capabilities/get原action，真实终态后才刷新，不自动重发；未知/等待意图在同一登录会话内跨组件保留。会话采用真实setup_token_required/CSRF/Cookie，目标选择通过Identity验证；Native媒体URL附实际actor/person/conversation选择器，音视频使用浏览器Range播放。

角色会话/聊天重写为Identity conversations/open与Conversation消息/发送/turn/取消，连续输入由后端Collector处理。上传消费正式data.media/source_ref作为InputPart原件，artifact.media只供能力素材；不上传本机路径。小屋消费实际life/state；Growth消费当前规则/真实认识与Sources.read。所有现有视觉资产和书架、房间呈现仍保留。

生活总览/目标活动/衣柜/阅读、日记/作品以及Speech/Reactions/Images在继续替换实际DTO；目前仍有原页面尚未接线，不得据此标CR18完成。People/Memory/模型/Channels/插件/媒体/基础设施后续分别收口。共享api/app/路由/依赖锁由本前端写者持有，若协调者将插件/媒体/基础设施页面交C，须明确子目录边界并先消费本次客户端。未执行测试/typecheck/build/浏览器，定向传输测试只写未跑。


### 原操作查询与缓存失效增量

- 共享操作表纳入 Catalog actor policy/manifest、AssetLibrary 实际读取/连接命令、capabilities/find、commands/get、Life access/取消/核对等正式端点。
- 原命令监控保留提交时 actor/person/conversation，使用同一原 ID 只读查询；页面切换不改写原目标，不重发 mutation。目标同查询参数一起带到任务页。
- useDomainResource 遇 401/403/404/410 或来源失效清缓存与编辑依赖，保留普通瞬时网络错误的重读入口。
- 状态：implemented_not_tested；按用户要求未运行测试、typecheck、build 或浏览器。Life 全页面接线仍在后续提交中，不以本基础增量标整批完成。


### CR18B 生活、日记与作品实际接线

- LifePage 按真实 life/access 区分作者管理/生活读取/已发布读者。保留原生活总览、时段插画、书架与抽屉视觉，目标/活动编辑、暂停继续取消、衣柜原图上传及穿着、持续阅读范围与进度均接实际 Life/C 能力端点。目标、活动、衣柜、阅读、经历、作品、章节和发布账号/会话均可继续翻页。
- 日记按本地日收集实际经历；作者正文修订、历史、取消/核对原模型、账号/会话发布；作品简介/提纲/人物、章节计划、真实生成、人工初稿/修订、审校与发布。读者仅使用后端独立发布投影，无生成材料。编辑持有原revision，冲突保留编辑，不自动套用新版本。
- QWeather 真实连接配置、位置查询/角色绑定与天气读数；展示 fetched_at 与真实未知 observed_at，不伪造观测时刻。作息和普通联系节奏使用实际 routine/preferences。
- SourceEvidence 按完整 locator 区分原文片段，使用实际字符 next_position；来源撤销/目标切换清当前视图/编辑依赖。
- 仅运行既有 Prettier 对本次自己所有的 TS/TSX 源码格式化，未安装依赖，未运行测试、typecheck、build、浏览器。状态 implemented_not_tested。
- 后续仍在完成：角色/人格/关系旧投影替换；Speech/Reactions/Images 完整配置与素材流程；Channels；Tasks/Skills/flow-graph；小屋控件持久保存与约定高级安排。A/C各自白名单页面另由其交付，不能据本提交宣称CR18整批完成。


### CR18B/D 角色、人格、关系与唯一能力目录

- RoleManager 已删除 legacy_roles/role.legacy/stage/capabilities/provider 等旧投影，角色只保存实际 name/enabled/persona_id/persona_revision。详情读取真实 can_manage；逐角色模型通过 C 提供的 ModelFunctionsPanel(actorId) 使用唯一Models.bindings，不保留 roles/models API。
- PersonaAuthor 接正式catalog/view/history/save与角色CAS绑定，保留新建、完整作者正文/语气/风格/称呼/世界、复制、作者修订历史/对照、明确固定角色版本。未运行的旧persona API包装已删除。
- companion/relationships 已整体删除旧分数/阶段/legacy_import/旧history消费者。接A memory/relationship 与save，展示带来源的背景、边界、偏好、共同经历，冻结/解冻保留当前关系字段并不重写未编辑详情；编辑固定原revision。依A0044c1f can_manage投影。
- SkillsPanel 只接 C Catalog actor policy；按真实availability分别显示未实现、未配置、停用、对端支持未知与实际can_execute。manifest来源真实保存/刷新/固定hash/只写token，无独立Skills账本或凭据转发。
- Auth使用正式AccountSession，删除虚构dialogue/conversations与部署账号claim路径；首次创建与登录使用真实SessionView，密码/账号输入约束与现有Identity实现一致。RoleWorkspace可继续读取全部授权角色页。
- 公共operation补A models/attempts/read、memory/relationship/save 与C Archive端点。C Models组件actorId props为已约定待其提交接口，未复制其代码。
- 仅既有Prettier格式化，未测试/typecheck/build/浏览器；implemented_not_tested。仍继续Multimodal/Channels/Tasks/flow-graph/其余设置与小屋。

- 共享证据/媒体控件正式支持可选 scope:DomainTarget，显式scope清空未提供的conversation/person，不借当前聊天目标；原文与MediaPreview同scope。同版本不同locator稳定区分。CommandFeedback携原操作/目标/attempt查询链接，普通作者Models原receipt读取使用A0044c1f端点。未运行功能验证。

## 原任务与日志查询收口

状态 implemented_not_tested。TasksPanel 按原 scene 分开读取能力 Action、普通作者 Models attempt、Expression 与 CommandStore 身份；同路由任务链接可切换原目标。核对/取消后重读原账本，unknown 不重发。原表达展示有序公开片段和实际媒体。运行任务、三角色资源和 Loki 范围日志明确使用 GLOBAL_SCOPE；纳秒边界以 BigInt 十进制字符串传输，消费 A47c8f0f 的 next_end_ns:string|null。日志上限与同纳秒密集记录局限明确展示。CommandMonitor 手工核对后的成功/失败/取消与效果未知分别报告。仅源码阅读、diff 和已有 Prettier 格式化，未运行测试/typecheck/build/浏览器。

## Speech真实用例收口

状态 implemented_not_tested。音色查询改为真实Action并保留分页；克隆与转写通过受控multipart上传，传canonical媒体，消费recordings与transcript正式字段。Plus连接带真实redirect_uri并在官方OAuth返回后显式完成授权，成功后清URL临时code/state。连接配置使用global，声线配置actor-only，试听/转写保持当前实际scene；声线试听带profile_revision，speed/tone与实际合成器一致。词典支持供应商ID/版本，校验行格式，不把未知用量或余额写成零。未执行测试/typecheck/build/浏览器；只有源码与已有Prettier格式化。

## 图像与表情真实用例收口

状态 implemented_not_tested。Images实际保存endpoint/account_id/quota_group/reference_mode/原生参数；角色参考走multipart受控产物，默认profile以actor ID保存。明确分享采用requested_delivery+真实conversation目标；预览保留场景修订/来源，生成只读查原Action控制，不用空revision冒充CAS。衣柜选择直接分页读life/outfits，本次场景支持outfit_id且不改当前衣着。ComfyUI工作流用graph/bindings/outputs正式合同，候选来自C唯一workflows.inspect规则，保留节点语义、追加/替换、数值边界及输出选择。Reactions按ReactionIntent实际选图，原件/ZIP采用受控媒体，真实单产物导出、逐项标注、图包/素材启停和每项CAS批量编辑。旧pack_id读取过滤假设已移除：图库分页真实读取后按所选图包展示，并明确未读页未知。仅读源码/diff与已有Prettier格式化，未运行测试/typecheck/build/浏览器。依赖C正式workflows.inspect和Images.scene outfit参数增量。

## 小屋环境持久接线

状态 implemented_not_tested。保留既有插画/GPU/几何预览与动态姿态、真实穿搭原件。窗户/纱帘/遮光帘、书桌灯/床头灯亮度及光色采用life.room真实CAS保存，首次拖动固定编辑revision；重读与自动昼夜预览明确分开。保存状态进入Life.scene.place.controls/light，真实自拍消费者可见；环境时钟预览不推进生活事件。新空库schema统一bedside_light与0..1亮度/光色，删除无消费者ceiling_light假映射。未运行测试/typecheck/build/浏览器，仅读源码/diff与Prettier。
