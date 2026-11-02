# 日程流式与自然运行故障修复

用户于 2026-11-02 10:54 授权：日程改为流式，按本轮复盘及输出预算方案修复查出的全部问题；智乃绑定 ComfyUI「智乃自动生成」，澄汐绑定「澄汐」；语音暂不绑定。基线源码 5c9f9cf55913c9f94c8ed6c2375db38ddadbd85b，当前生产 b87773926e5c4ca6039fa390c3bcf469f34f7c98。根为唯一集成/配置/生产写者，产品实现沿用 gpt-6.1-sol/max 子智能体。

## 交付范围与负责人

- A：Life 日程模型流式、简短完整 JSON、模型输出/上下文/执行预算一致、截断后有界恢复、无效结果与有效决定分离、空闲到期重新考虑、程序日期/星期、留白公开 DTO、相关 Life 前端状态。独占 src/tianshu/life、必要 models/client/broker/protocols、conversation/generation.py 及精确 Life 前端文件；新增 Life 迁移及注册由 A 单写。不得改用户 Memory 和 Companion 算法。
- B：媒体定时作者上下文合法刷新、真正失权与单项错误隔离/退避；web/forum/bilibili 等发现来源可用性与真实搜索接入合同。独占 capabilities/media 调度与 books、content/content_discovery 及必要目录可用性；实际搜索服务部署/配置由根执行。不得把 provider 可用配置当成实际搜索成功。
- C：channels.read 内部补取来源与技术结果分类；保留原始消息/展开原件、调用审计，停止无原文生活经历污染；检查并按必要范围完善现有图片工作流导入/角色绑定。独占 channels、conversation/results.py、capabilities/images 及对应必要前端文件。真实工作流/配置读取与写入由根执行，先核对实际名称和 graph，不猜节点/人物。
- 根：共享记录、明确必要的跨边界装配、实际配置核对与导入、单写集成、全部收齐后的必要联合验证、原生受影响路径验证和串行部署、交付记录与干净源码包。

补充核对：四条真实 book_feed 属于全局管理员，原作者 context.actor_id 均为空。B 获得 Identity.refresh_scheduled_context 的精确扩展归属：保持现有 Life/Growth 默认必须有角色的语义，允许媒体全局作者显式保留空角色刷新其原授权；仍校验主体、原 scope/audience/permission 上界，不把书源猜绑给任一 BOT。纳入定向验证，无用户 Memory/Companion 源码改动。

用户进一步明确：订阅与 BOT 属于不同功能分区。本批只修复独立订阅的作者授权刷新、定时扫描与错误隔离，不增加订阅与 BOT 的绑定，不把其结果接到日程、角色记忆或内容发现配置；四条现有书源继续保持 actor_id 为空。从 BOT 找书、找视频、找音乐属于后续开发，本批不扩展这一入口。

集中验收追加的精确修复：Models 原生响应先校验结构再提取 completion，避免 malformed choices 抛原始 Python 异常；图片恢复后的 create_only 继续归档，不改成表达复查；图片原生实测发现未显式提供估计时把 Base64 传输体算为文本 token，Models 修正已识别内联图片的准入估计并复用既有媒体预留，真实 reported 用量结算和纯文本/显式估计保持。Conversation Context 只允许数学等价地引用该共享估值，不改用户 Memory 算法。原生不完整响应的 unknown 保留；已有 ComfyUI 成图可按原 handle 取回，不重新提交 prompt。

## 实施边界

1. 流式复用既有 ModelClient/ModelBroker 和原请求/回执身份；完整 JSON、来源及 CAS 校验通过后统一应用。断流、unknown、取消、晚结果不能制造第二次外部动作。不把原始推理或半份规划变成正式日程。
2. 日程 Go DeepSeek 输出预算以 16,384 起，Gemini 3.8 high 以 32,768、Gemini 4 普通起点 32,768；高上限与输入预留、超时、配额估计一致。不得仅在协议参数末尾覆盖数字造成账本预算失真。按实际支持的协议/模型能力处理；Gemini 4 continuation 不可冒称已有自动续接。
3. 短标题、单句理由、紧凑 JSON、不重复已有目标，不压掉必需参数、来源与目标约束；保留角色选择留白的自由，不用强制忙碌伪造活跃。有效生活窗口/明确到期允许重新考虑，普通情绪衰减或群聊风格水位不引发高频付费规划。
4. 用户侧 Memory 源码/算法冻结。新的用户业务数据与作者配置保留；仅精确确认的技术性噪声可用一次性操作撤回，不增加永久旧数据兼容层、不再整库清空。单个失权书源不能阻断其他独立来源。
5. 用户指定的工作流分别绑定。语音保持未绑定，不自选 Fish 音色，不因本批修复改变 QQ 群观察/回复策略、设备配置或给其他人发送验收消息。
6. 子智能体可以编写必要用例并做静态审查，全部实现与装配收齐前不运行 tests/typecheck/build；根之后统一运行与改动相关的最小有效验证，已通过未变结果复用。生产动作只由根执行。

## 必要完成证据

至少覆盖：完整与截断/断流规划，原 unknown 保留、有限恢复、同材料有效决定去重与空闲复查；真实 free slot HTTP 读取；书源 epoch 变化合法刷新/真失权/后续来源仍推进；实际网页搜索返回可读候选及来源缺配置；内部 channels.read 不生成独立生活经历而引用原件仍可读；两角色工作流真实 graph/绑定与一次对应引擎执行。界面与运行状态如实显示错误和等待。最后核对实际版本、健康、两个 QQ 连接及受影响路径，不用健康状态替代业务验收。

## 集中验证与第一份正式运行证据

所有实现收齐后执行必要集中检查，按 testcase 身份去重并以精确返修结果覆盖失败：后端 288 项通过，另有子用例；桌面/手机 24 项通过；前端类型检查与 Vite 构建通过。第一次后端 270 通过、3 失败，浏览器 22 通过、2 失败，原失败记录保留。后端两项是 nullable Chat choices 的校验顺序，一项是测试替身未接收生产已有的关键字参数；浏览器两项是新用例未打开今日安排页签、未匹配实际 query 的夹具问题。修复后只重跑失败和受直接影响用例，没有重复全套通过项。

2026-11-02 12:27 首次修复版本正式上线：源码 `13e48e1abbcf0bda41888185a00ceae8481f23ef`，镜像 `sha256:4d5d67defef3741e9d18adfc1da079460497d1e8f71845e3c31d391956bee05c`。Life 的 `0035_life_planning_recovery` 已应用；配置文件摘要和 11 个挂载保持，容量守护 active。迁移前只备份 Life 域，没有清空新业务库。后续订阅回执收敛补丁的最终版本另见下节。

| 路径 | 实际结果与界限 |
| --- | --- |
| 供应商流式 | DeepSeek Go 与 Gemini native 经正常 probe 返回公开正文和完整终止标记，之后才登记 text/stream。不是配置名称推断。 |
| 实际日程 | 两角色通过正常“重新考虑”命令唤醒，保留原请求，生成新的流式完整规划。智乃 reported input/output 为 8,321/4,386，澄汐为 14,546/2,462；均 complete/stop 并应用。当前共 3 项活动，含正在进行的想象日常及后续安排，不冒称实体外部活动已执行。 |
| 日程读取 | 两角色实际 POST day-plan 均 200，包含活动和留白；free slot 的 checkpoint_data 为 `{}`，不再因为 NULL 返回 500。 |
| 网页发现与阅读 | 独立 SearXNG 辅助服务无对外端口，使用 Sogou/Brave；正常 content.discover 返回 5 条候选，随后真实读取 IANA 正文并保存 Source；另一次显式 browser 读取 example.com rendered DOM 成功。候选仍标记未读，实际正文才允许成为经历。 |
| 独立书源 | 四条原 feed 合法刷新到 epoch 5，actor_id 始终 null；四个原扫描 Action 均 succeeded，每条导入/复核 1 项且 invalid=0，内容状态 unchanged，正常间隔仍 24 小时。不接 BOT、Life、Memory 或 content provider 的本地库开关。 |
| 技术噪声 | 精确撤回 64 条内部 channels.read 误生成的无原件经历；均无日记、反思、风格规则或活动 Growth 批次引用。原消息、Action、receipt 与 result_consumption 保留；同事务确认其余 699 条经历及 95 条 claims 的数量保持。没有清空用户记忆或写旧数据兼容接口。 |
| 渠道与进程 | interactive、worker、model-broker ready；35 个执行通道正常注册，旧队列路由阻断 0；两个 QQ reverse_ws available。没有发送 QQ 验收消息。 |

## 自拍绑定、分流与实际效果

从实际 ComfyUI canvas 及 object_info 转换两套工作流，而非复用旧澄汐测试图。智乃 profile 绑定 `comfyui-chino-autonomous-20261102`（智乃自动生成），澄汐绑定 `comfyui-chengxi-20261102`（澄汐）；复用同一已配置本地 ComfyUI 引擎。固定人物、模型、LoRA、原工作流风格保持，动态姿态/场景等通过已有绑定进入 graph。未改用户原始 ComfyUI 文件。

智乃实际 ComfyUI 成图完成后，原调用出现 unknown；根按原 handle 调用既有 reconcile 取回相同产物，没有再次提交 prompt。产物约 1.81 MB，人物、白天咖啡馆自拍画面已看图核对。原 physical attempt 的 unknown 审计保留，不把后来取回说成首次调用正常。

澄汐在同一 Gemini 供应商的 native 和 Chat 规划接口上有三份未满足终止合同的回包。Chat 实测命中独立规划 route、8192 输出，并非仍受调用方 1000 上限；原始不完整回包未落盘，不能进一步断言是网络、长度或供应商策略。三个原 unknown 不重放。

最终仅 `image.plan` 的 interactive/due/elastic 绑定改为独立 `image-plan:deepseek-go`，复用已有 Go 供应商、输出 16,384。主聊天与 vision 仍是 Gemini；Life/Memory 共用的 Go route 参数保持原值。临时 Gemini Chat 规划 route 已禁用并保留审计。新独立验收意图完成 Go 规划、ComfyUI 出图、Gemini 图像检查和相册归档：产物约 2.08 MB，视觉准入 estimated input 3,943，实际 reported input/output 1,461/803，不再把 Base64 算成数百万 token。

该张澄汐图的角色、日光、晴天匹配，但没有画出当时“画速写”的活动，consistency=conflict，按 create_only 正确 archive。生成与回执链路成功不等于每张图语义完全匹配；没有为使验收通过而删除冲突标记、反复抽图或自动发送。Fish 音色及语音绑定继续为空。

## 两项原生收尾补丁（已完成并发布）

真实刷新发现，业务完成已推进 feed.next_scan_at/revision，而 schedule 仍留在 submitted；原实现按业务下次扫描时间跳过了原回执观察。这四条实际已成功，页面依赖的 code/last_scan_at 仍正确，但原始 API 状态不准确；如果同一窗口发生 unknown，下一轮还可能越过原未知状态。

补丁仅修共享调度/仓储：业务扫描期限与原回执观察期限分别保留，未收敛时优先按原回执期限唤醒；在入队前登记原 key；submitted/pending 先观察原 Action，业务版本推进不能改 key；成功收敛不推迟原正常间隔，unknown 阻断新一轮。明确终止失败后，原 schedule 的 retry_generation 才加一，使退避后的新扫描拥有新的身份。并发作者保存/恢复仍按原授权与 CAS 处理。

澄汐成图差异另查到一个确定的结构错误：当 outfit 为空，Workflow.render 将节点 35 的整份未分段旧提示（海景阳台、站姿、早晨）继承进 User/extra_prompt，同时动态 Composition 段又包含书桌和速写本。读取原 image_work 的冻结提交 graph 已确认两者同时送入实际编码节点。修复删除这种隐式整段继承，仍保留明确定义的分段默认和固定人物/模型/LoRA/画风节点，不加入旧模板猜测或兼容解析。

智乃已有明确服装默认，保持原样；澄汐只把其原工作流中可明确辨认的 `ivory and ice-blue sea-observer dress with subtle silver instruments` 设为 profile.outfit_defaults.prompt，保存默认服装而不把旧姿态、海景与时段当成固定场景。用户原始 ComfyUI canvas 不改。旧成品及 conflict 记录仍保留，新编译输入和最终真实样本另记，不能倒改原验收结果。

两项补丁全部收齐后，集中运行媒体调度、工作流编译及四个直接相关维护恢复用例：38 passed、8 subtests passed。没有重跑之前未变化的 Models/Life/前端检查。这 38 项与前一批 288 项存在重合，不能相加作为不同用例数。

最终运行源码 `a6794bd8204ce5ebd145f58f1eb7f1a3089a9c87`，镜像 `sha256:f5a1c90b6058108d4cce4987475b9528897e90fb113762baa109205b9b087ca6`；容器 `87cd74b661b54f686178de68881f0fd51ed185537b21113eebc0038802902d10`。此补丁无 DDL，未再次备份或清业务库。版本、ready、容量守护、配置摘要与 11 个挂载核对通过；两个 QQ 连接可用，35 通道/40 执行槽正常，旧路由阻断 0。

正常启动后四条原 submitted 均自然收敛为 completed。逐条核对原 Action key、last_scan_at 和次日 next_scan_at 未改，actor_id=null、epoch=5，四条扫描未产生任何 Memory Episode。没有手工修补 feed、重新导入或绑定 BOT。首次补丁后已有 20 次实际 channels.read，active Episodes 为 0，旧技术噪声仍为 0。

修正模板后的一次独立实机样本 `a1a898f9c17cfc6fd7c02a557787b694f3296429351382a7b7051e27cf6702aa` 已生成约 2.01 MB 图像并归档，SHA256 `fd78a10e87abf63d6c9ae4e50e329f5413f77d87a5b350ab9ac5709885406896`。冻结提交 graph 确认旧海景阳台/早晨描述消失，实际服装默认、当前书桌/速写本请求存在；人工看图可见室内书桌、书本和笔，人物外观保留。不能据此把所有绘画细节说成完全符合。

这次 Gemini 自动视觉检查又收到 `upstream_response_incomplete`，因此系统原样保留 consistency=unknown；没有把人工看图写成供应商 matched，也没有重发该原请求。图片创建/保存已成功，主 Gemini 供应商仍存在非完整回包的外部限制；本批未解决其内部响应生成原因。主聊天和视觉绑定保持用户原选择，自拍规划独立 Go 分流已实测完成。

## 清理与交付范围

本轮隔离测试 PostgreSQL 容器和其专属目录已退役，删除逻辑数据约 54.46 MiB，并移除本地临时测试 DSN。生产数据库、共享镜像、Life 迁移前备份和原始审计保留。三份本轮实现 worktree 通过受支持工具归档，集成检出及用户原工作区保留；原先三个受限的 Windows 构建目录仍不打入源码包。

干净源码包由已提交 Git 内容导出；最后交付文档提交与运行版本之间的 src/web/packages/tools/deploy 及依赖 manifest 核对无差异。包不含运行数据、凭据、模型权重、虚拟环境、node_modules 或构建产物。没有执行 QQ 发送验收、破坏真实授权，或把这次短窗口的正常运行当作长期多日日程与所有外部服务的质量保证。BOT 找书、找视频、找音乐入口保留后续开发，本次订阅保持独立。
