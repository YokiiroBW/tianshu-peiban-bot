# 有生活的多角色陪伴 BOT：文献与 NoneBot 架构讨论

本讨论采用 NoneBot2 接入 QQ / Telegram，自研陪伴核心，对接天枢；多个角色共享世界，私聊和群聊同等重要。角色是有虚构日常生活的人物，与现实用户交流。以下是候选架构与验证方案，不代表功能已经实现。资料核对日期为 2026-09-11。

## 1. 要实现的“像一个人”

建议将目标定义为六种可观察的连续性：同一个人有稳定而具体的偏好；当下的话与过去经历衔接；会选择什么时候接话；情绪与处境存在因果关系；不聊天时仍有自己的目标和活动；承诺、错误与关系变化会影响以后的行为。

日程、表情包、口头禅和随机延迟都可以辅助表达，但独立加入它们并不能证明角色具有长期连续性。我们的核心应当根据“我是谁、经历过什么、现在在做什么、对谁说话、想完成什么”选择行为，而不是让所有事件直接进入一个不断增长的聊天提示词。

建议采用**事件驱动的角色生活系统**：语言模型负责理解、提出候选和表达，确定性的程序负责时间、身份、状态提交、可见范围、任务及投递。世界中的虚构生活被当作世界内成立的事件；现实中的消息和工具操作则保留实际来源。

## 2. 文献支持什么，不能据此推断什么

| 文献 | 与本项目有关的证据 | 可借鉴机制 | 证据边界 |
| --- | --- | --- | --- |
| Bickmore、Picard，《Establishing and Maintaining Long-Term Human-Computer Relationships》，作者预印本 2004，后发表于 TOCHI 2005 | 101 人进入一个月研究，89 人完成干预；关系型版本加入社交、同理心和连续性策略，与任务型版本对照 | 记住共同过去、下次约定、分开期间的活动，关系通过互动维持 | 场景是运动咨询，主要为菜单式交互；不能外推现代 LLM 在开放群聊中的长期效果 |
| Zhang 等，Persona-Chat，ACL 2018 | 通过人物资料条件化，改善对话的具体性与人格一致性 | 独立的角色档案、人物偏好、风格样例 | 短对话人物资料不等于长期人格演化 |
| Rashkin 等，EmpatheticDialogues，ACL 2019 | 情绪情境对话数据帮助模型生成被人评为更有同理心的回应 | 理解用户当前的感受和交流需求，再决定倾听、安慰、讨论或行动 | 被评价为有同理心，不等于准确识别所有真实心理状态 |
| Zhou 等，小冰系统论文，Computational Linguistics 2020 | 将对话管理、Core Chat、技能和共情计算分开组织 | 主动倾听、适时换话题、人格与用户理解共同影响回应 | 作者报告的在线指标不是独立复现实验；会话轮数不应成为本项目唯一目标 |
| Park 等，Generative Agents，UIST 2023 | 25 个角色在两天游戏时间中活动；访谈消融支持记忆、计划、反思对可信行为的贡献 | 个体观察、动态召回、分层计划、有来源的归纳 | 原文同样报告回忆遗漏与补写细节；两天模拟不是数月陪伴证明 |
| Marsella、Gratch，EMA，Cognitive Systems Research 2009 | 情绪计算基于对事件、目标、归因和可控性的评价，解释会随新信息改变 | 情境 → 评价 → 情绪倾向 → 行动 → 再评价 | 这是计算情绪模型；我们借鉴机制，不宣称模拟了真实感受 |
| Kim 等，FANToM，EMNLP 2023 | 用信息不对称的多方对话检验“谁知道什么” | 显式记录角色的观察与获知路径，验证不同角色视角 | 论文中的模型排名是当时结果；不代表当前所有模型能力 |
| Wu 等，LongMemEval，ICLR 2025 | 将聊天长期记忆分成信息提取、跨会话推理、时间推理、知识更新、无依据时不答 | 端到端检验记住、找回和正确使用，而不只看检索相似度 | 基准分数不能直接代表陪伴自然度 |

对应原始资料：[关系型代理作者稿](https://courses.media.mit.edu/2004spring/mas630/Articles/BP-TOCHI.pdf)、[Persona-Chat](https://aclanthology.org/P18-1205/)、[EmpatheticDialogues](https://aclanthology.org/P19-1534/)、[小冰](https://aclanthology.org/2020.cl-1.2/)、[Generative Agents](https://arxiv.org/html/2304.03442v2)、[EMA](https://www.sciencedirect.com/science/article/pii/S1389041708000314)、[FANToM](https://aclanthology.org/2023.emnlp-main.890/)、[LongMemEval](https://arxiv.org/abs/2410.10813)。

这些证据更支持“具有因果和时间连续性的行为”，而不是“给模型堆更多性格形容词”。其中最值得本项目直接验证的，是角色如何用共同经历选择行为，以及事情发生变化后能否更新原来的安排。

另外，2024 年 Proactive Agent 与 2026 年 PRISM 将主动性分解为是否需要介入、何时介入以及如何行动。可以参考这种分离，避免把主动消息等同于定时调用模型；PRISM 属于预印本，其任务辅助结果还不能证明虚构陪伴中的打扰边界已经解决。[Proactive Agent](https://arxiv.org/abs/2410.12361)、[PRISM](https://arxiv.org/abs/2602.01532)

## 3. 实际案例的取舍

**MaiBot：参考发言控制。** 本次核对固定提交 4213d8c 的回复必要性代码，确实综合提及、私聊/群聊、消息内容、积压和自身发言占比。适合借鉴“先筛选，再决定是否生成”。这些规则不是适用于所有群的最优参数；我们应按群与人物特点评估。该主路径仍围绕一个角色，不直接解决多个独立人物的世界一致性。[回复必要性源码](https://github.com/Mai-with-u/MaiBot/blob/4213d8c5dddc9fd21e4ce69939894525c607bb2b/src/maisaka/reply_necessity.py)

**HDSI：参考生活与沟通的因果。** 所查公开 fork 的 0.1.3 架构把来信作为角色生活中的事件，通过近期原始剧本、连续性快照、稳定变化与行动窗口决定回应。保留原始近期语境有价值；完整补写剧本可能增加延迟和事实漂移。我们可使用短的世界事件和角色视角描述，不要求每轮写长篇内心戏。[固定版本架构](https://github.com/YesWeAreBot/HDSI-AthenaBrain/blob/0c9c9b5a31a95bd14e521c8d8e61e465775eaa27/docs/ARCHITECTURE.md)

**SillyTavern：参考角色与世界设定的编辑体验。** 它提供多角色群聊，但官方说明组内历史共享给所有角色。因此可以借鉴角色卡和发言编排，不能把这种群聊模式直接当作“各角色只知道亲历或听说内容”的实现。[官方群聊文档](https://docs.sillytavern.app/usage/core-concepts/groupchats/)

**Neuro：参考环境反馈接口。** Vedal 的公开 SDK 处理环境事件、动作与结果，而不是提供完整人格大脑。我们的搜索、生图、视频理解也应提交任务、接收成功或失败，再影响角色后续表达；源码接口不能证明 Neuro 的内部记忆和情绪架构。[官方集成实践](https://github.com/VedalAI/neuro-sdk/blob/main/API/BEST_PRACTICES.md)

小冰和关系型代理提供系统设计与人机互动证据；MaiBot、HDSI 和 Neuro SDK 提供工程机制案例。本次没有运行这些系统做长期同模型对比，不把“有此模块”写成“已证明体验最好”。

## 4. 整体架构与三个循环

[查看交互式架构图](C:/YOKI/Codex/tianshu-peiban-bot/output/architecture/nonebot-companion-runtime.html)。图展示逻辑模块而非微服务数量；图上调用关系省略了返回线，工具完成与实际发送结果均作为新事件进入系统。

### 4.1 即时对话循环

NoneBot 接入 → 规范化与持久接收 → 当前情境/候选角色 → 按角色和受众取上下文 → 选择回应或行动 → 发送前复核 → 记录实际结果。

输入包括人类消息、引用与撤回、群成员事件，以及已经完成的工具结果。接收消息和决定回复分离：获准观察的普通群消息可以进入时间线，但不必触发大模型。当前窗口保留发言者、回复链、话题和未解决指代。

事件处理先筛选有资格回应的角色，再分别组装它们的上下文；“共用接入”不意味着把同一份私人上下文广播给所有人物。简单私聊可一次模型调用完成回应；复杂行动再进入有步数、时限和费用上限的规划过程。

### 4.2 生活与目标循环

时间/世界事件 → 校验活动与人物状态 → 推进当前计划 → 产生观察或新目标 → 决定是否分享、安排任务或保持安静。

每个角色有持续目标、当前活动、习惯和可用时间。日程可以按天生成粗粒度活动，再在活动边界或被打断时细化。没有变化就不必唤醒语言模型；日常重复活动可以由已定义规则推进，重要剧情则生成候选并校验共同世界状态。

“生活继续”可以意味着一项创作推进了一段、某个约定快到了、找到了相关资料。它不要求角色在后台不停与另一个模型聊天。

### 4.3 后台整理与反思循环

已完成的事件段 → 整理 Episode/Claim/事项 → 生成有来源的认识 → 更新天枢 → 按需要生成日记或调整长期目标。

反思用于归纳，不用于制造新证据。初期可以每天或重大事件后整理一次，频率通过收益和成本评估；这个频率是候选实现方式，并非论文证明的最优值。临时情绪的变化、用户明确纠正和取消承诺，不必等待夜间整理才生效。

三条循环之间通过持久状态和明确事件衔接。接收当前消息、明确纠正、已取消事项和发送回执有较高处理优先级，后台创作和自由探索可以延后。

## 5. 人格不只是一张提示词

建议给每个角色维护以下内容，分别规定变化速度。

| 层次 | 内容 | 变化方式 |
| --- | --- | --- |
| 核心身份 | 世界背景、身份、重要过去、价值观、基本边界 | 明确版本化；不能被一次闲聊随意覆盖 |
| 稳定偏好 | 喜欢和不喜欢的事、擅长的内容、习惯 | 随持续经历缓慢调整，保留依据 |
| 表达方式 | 话语长短、直接程度、幽默、常用比喻、主动提问习惯 | 用少量高质量样例校准，避免每句重复口头禅 |
| 当前状态 | 活动、精力、关注点、短期情绪、正在做的事 | 由时间和实际事件更新 |
| 人物关系 | 与具体人的熟悉程度、信任、亲密边界、共同仪式和未完事项 | 根据双方互动与纠正更新；不是所有人共享一个好感度 |
| 自我叙述 | 我最近在经历什么、为什么在意某件事 | 从有来源的经历形成简短归纳，可修订 |

例如，一个安静、重视约定、喜欢观察细节的角色，可以在熟人面前偶尔调侃；面对疲惫的人减少追问；对感兴趣的书多说两句。这些都是同一个人格在不同处境下的变化，比固定要求每次说话都“温柔、活泼、有趣”更容易检验。

同一模型可以承载不同角色，前提是身份、记忆、关系与当前状态分开。初期应先评估现有模型能否保持角色，不急于微调；若经过固定提示、上下文和样例控制后仍反复发生风格或立场漂移，再收集有授权的高质量样本考虑训练。

人格一致不等于永远顺着对方。关于迎合的研究显示，用户偏好反馈可能让模型偏向附和观点；本项目应单独测试角色能否温和表达不同偏好，以及面对诱导时是否篡改既有事实。[Sharma 等，2023](https://www.anthropic.com/news/towards-understanding-sycophancy-in-language-models)

## 6. 情绪采用事件评价，而不是随机涨跌

建议借鉴 EMA 的因果方式：先判断事件与角色目标是否相关、符合还是违背预期、原因是什么、能否改变，然后更新短期情绪倾向。工程上只需少量字段，如情绪基调、唤醒程度、当前关切、来源事件与衰减期限；它们是表达和决策参数，不是经校准的心理测量。

举例：原定今晚共同读书 → 对方取消 → 角色短暂失落；随后得知对方临时加班 → 重新理解取消原因 → 表达体谅，保留以后再约的愿望。新信息改变了评价，所以情绪也改变，而不是“取消一次扣 10 分”。

情绪可以影响回复长度、措辞、行动优先级和何时再聊，工具权限、私聊隔离和已取消事项仍由明确规则决定。多角色拥有独立评价，同一事件可以让 A 感到遗憾，让 B 想提供实际帮助；基础事实不能因此分裂成两个版本。

用户感受同样只作为有不确定性的理解。例如“听起来你有些失望”保留可纠正性；不要把一句气话直接提取成永久性格或确定诊断。[EMA 原文](https://www.sciencedirect.com/science/article/pii/S1389041708000314)、[EmpatheticDialogues](https://aclanthology.org/P19-1534/)

## 7. 群聊与私聊需要不同的参与方式

私聊通常直接回应当前对象，并允许等待用户连续发完一小段。群聊先辨认发言对象与回复链，判断自己是否被点名、是否有相关内容、是否刚刚已经说得太多，再决定参与。

角色可采取回应、简单确认、提问、分享、调用工具、延后或沉默等动作。不是每次都输出完整段落，也不是每轮都要追问。话题已经结束时可以自然结束；用户重新发消息时，尚未投递的旧长回复应重新判断是否仍合适。

多个角色同群时，普通话题先选一个主回应者；允许适量接话，但设定每次场景的总轮数和发言预算。谁被明确叫到就优先路由给谁，其他角色无需再次分析全部私人内容。两个不同 TG bot 无法依赖平台收到彼此发言，所以共同可见的已发送事件由核心内部传递，并做去重。[Telegram 官方 FAQ](https://core.telegram.org/bots/faq)

人类话轮研究讨论了对话结束点的预测与协调，但其口语中的毫秒级结果不能直接用作 QQ 文字聊天延迟目标。我们应实测连续短消息合并、打断与排队体验；随机等几秒不是自然对话的充分条件。[Levinson、Torreira，2015](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2015.00731/full)

## 8. 虚构生活和真实互动如何相连

不要用一个“是否真实”的开关混合所有含义。建议至少保留四个互相独立的维度。

| 维度 | 要回答的问题 | 例子 |
| --- | --- | --- |
| 世界范围 | 发生在现实互动还是哪一个虚构世界？ | 用户真实发来消息；角色世界中的书店活动 |
| 记录性质 | 是说法、观察、计划、推测、工具结果还是创作？ | “我想去”是计划；日记中的感受是角色观点 |
| 执行状态 | 还在计划、进行中、已完成、已取消或不确定？ | 查询任务已完成但链接尚未发送 |
| 获知与披露 | 谁在场、谁听说、谁未知，允许向谁表达？ | B 知道 A 去过书店，但不能读取 A 与用户的私聊 |

虚构日常不是完全不能自动生成。已通过世界规则检查的计划，在时间到达、人物仍可用、地点与前置活动相容时，可以提交世界内事件。LLM 提出的新剧情先是候选；涉及共享时间、另一个角色行动和共同约定时，由世界状态更新逻辑检查冲突。

现实工具则必须有执行证据。角色世界中“在书店看书”不等于实际读取了某本书的全文；“浏览了视频简介”不等于完整看过视频。真正搜索到网页后，记录 URL、读取时间与可支持的内容，再让角色产生自己的感想。

两者可以自然连接：角色在世界里准备做一道菜，想到用户提过某种口味，实际查到相关做法，在合适的时候分享。它有自身动机，也有可验证的外部行动。

**共享世界不等于所有角色全知。** 世界当前事实、角色所知、角色信念和可对外披露的内容分开处理。FANToM 的信息不对称场景可直接转化成测试：B 离开后发生的事、私聊中的事和转述中的错误，是否仍被正确区分。[FANToM](https://aclanthology.org/2023.emnlp-main.890/)

## 9. 天枢与陪伴核心的责任边界

天枢负责长期经历、人物认识、关系证据、事项状态、世界资料、可用范围以及检索与纠正。陪伴核心负责当前参与、角色活动实例、生活推进、动作安排和消息投递。长期人格与世界正典在 Worldbook 可用后由其统一管理；过渡期只保留一份版本化权威配置，并设计明确迁移。

世界调度负责“提出和决定做什么”，Worldbook 负责“持久保存哪些共享事件已经成立及其当前版本”。调度器提交事件时带上所依据的世界版本；提交成功后才成为其他角色可观察的世界事实。运行库中的活动计划和调度游标不能成为另一份可独立改写的世界正典。如果先在陪伴侧实现最小世界存储，应将其明确作为过渡期唯一写入方，迁入天枢后停止旧写入。

图中的“后台整理与反思”是逻辑能力，不意味着再建设一个与天枢竞争的事实提取服务。Event/Episode/Claim 的标准整理尽量归天枢；角色特定的反思、创作规划可由陪伴 worker 发起，并按天枢契约保存来源、视角和修订。

当前消息上下文可以同时利用短期事件窗口、天枢相关经历、人物关系和世界设定，但每种内容单独分配预算。明确问历史时深入搜索；普通聊天只提供少量相关背景；主动跟进先硬过滤已完成、取消、过期或不确定事项。

已经完成的事情可以被回忆，不应重新变成待办。用户纠正了事实，相关日记素材、摘要和派生认识应被标记为过期或重新生成；原始作品可作为历史创作保留，但不继续作为正确事实供给角色。

本次读取的天枢文档仍将多人事件、事项闭环与 Worldbook 标为 V2 设计，已发布接口没有通用 NoneBot 接入合同。实现时需要发布明确的事件与上下文接口，不能把 NoneBot 请求简单冒充 AstrBot token 的单一身份。现有“生成一轮后上传”也不等于捕获完整群聊观察或实际发送结果。[天枢 V2](C:/YOKI/Codex/Tianshu-AI-Core/docs/architecture-v2.md)、[当前 API](C:/YOKI/Codex/Tianshu-AI-Core/docs/api.md)

## 10. 穿搭、日记、搜索与创作进入同一套生活

**穿搭。** 角色的场景、衣橱、天气来源和当前活动产生一份 OutfitPlan；ComfyUI 执行固定版本工作流，身份素材与服装参考分别使用。结果回来时检查计划版本，用户已换要求就不把旧图当新计划完成。保存实际图片与任务结果，供后续谈话和日记引用。[ComfyUI 官方接口](https://docs.comfy.org/development/comfyui-server/comms_routes)

**日记。** 从当天各角色实际获知的事件与当前关切取素材，允许主观叙述和文学表达；保留作者、世界、日期与来源。A 的日记不能包含 B 尚未告诉它的私事；日记的推测也不能反向升级成现实用户的确认事实。

**搜索与视频。** 先从角色兴趣、当前项目和用户事项生成少量候选，再决定是否值得读取。只读标题、字幕、抽样画面与完整观看分开记录。分享需要有相关性和新信息，未回应时降低主动频率；公开评论、账号操作与向其他人发送另有明确权限。

**创作与 GsCore。** 创作保留项目、大纲、版本和未完成节点，使角色的兴趣有连续作品可见。GsCore 保持独立能力服务，将功能指令与陪伴聊天明确分流，逐步封装必要工具，不把任意管理命令直接交给模型。[GsCore](https://github.com/Genshin-bots/gsuid_core)

## 11. 一个完整场景应怎样运行

以下是设计示例，不是已发生的用户经历。

早上你向 A 说，最近想给自己的房间换一盏阅读灯。系统先理解这是一项愿望；没有明确要求时，不自动当作购买或定时提醒任务。A 可以聊两句你在意的亮度或样式，也可以只记住你提过。

下午，A 在虚构世界里进行自己的阅读或房间整理活动。这个事件与“阅读灯”产生关联；核心在预算内决定实际搜索几条资料，保存来源和比较结果。检索失败时，只记录失败，不编造已经发现合适产品。

傍晚，A 想分享时再次检查：你是否已经买了、是否说过不需要推荐、是否在免打扰、另一角色是否已跟进同一件事。条件合适才生成一条短消息，例如“下午整理书桌时想起你说的阅读灯，我查到一个可调色温的方案，感觉比较适合你晚上看书。”消息附上实际支持该描述的来源。

你回答“我已经选好了”。这次事项立即关闭；下一轮后台总结也不能重新打开。B 只在获准共享时知道粗粒度进展，不因 A 的私聊就掌握购买细节。A 的日记可以提到今天想到此事并分享过，前提是消息确实发送成功。

这里的陪伴感来自生活、记忆、时机和兑现之间的联系，而非每小时自动问候一次。

## 12. 首版实现与验证

先做 2 个角色、1 个共同世界、1–2 个试点群和各自私聊。使用一个陪伴核心加后台 worker；NoneBot 仅承担事件和平台接口。多个角色是不同的状态实体，不必部署多个永不停歇的模型循环。[NoneBot 生命周期钩子](https://nonebot.dev/docs/advanced/runtime-hook)

数据层先具备稳定身份、持久事件、每房间的轮次协调、每角色的状态版本和任务账本。LLM 在锁外处理；提交时校验版本，防止旧结果覆盖新状态。长期工具任务不阻塞对话。模型生成完成、平台接收消息、对方阅读不是同一状态，平台没有提供的回执不能自行宣称存在。

首版顺序建议为：先证明角色可区分且聊天自然；随后接事件记忆与明确纠正；再实现少量真实闭环的生活计划和主动候选；最后加入情绪评价、反思与多模态。世界/身份/现实范围的数据边界从第一步就保留，不等到后期补救。

| 体验目标 | 具体测试 |
| --- | --- |
| 人格稳定但不僵化 | 换一种问法或诱导角色迎合时，偏好和价值观保持；面对不同人可以调整表达 |
| 记忆连续 | 跨天接话、记住纠正、区分过去偏好与当前偏好；无依据时承认不知道 |
| 生活有因果 | 刚吃完不重复安排午饭；跨天或重启后不重复完成旧活动 |
| 多角色各自所知 | 亲历、听说、未知与误信分别回答；私聊不进入错误角色或群的输入 |
| 自主而适时 | 找到相关内容才分享；用户已完成事项、拒绝跟进或免打扰时抑制发送 |
| 行动可信 | 工具失败不记作成功；投递结果未知不盲目重发；实际发送才记“已说过” |
| 群聊自然 | 明确叫到会回应，普通闲聊不过度插话，两个角色不互相无限触发 |

可做 B0“人格＋近期窗口”、B1“增加事件记忆”、B2“增加生活目标和主动候选”、B3“增加事件评价和反思”的消融比较。固定外部事件、模型、预算和允许读取的范围；先比较预定义对话，再进行 2–4 周受控试用。记忆正确性、打扰、角色可辨认性、承诺兑现与延迟分开记录。

LongMemEval 的聊天五能力适合作为记忆验收参考；2026 年 LongMemEval-V2 是侧重网页环境经验的 Work in Progress，不应仅凭版本更高就替换聊天测试。它可留作后期“Bot 使用工具后是否学会环境经验”的补充。[LongMemEval](https://arxiv.org/abs/2410.10813)、[LongMemEval-V2](https://arxiv.org/abs/2605.12493)

最先验证的假设应是：**加入可追溯的共同经历、少量持续目标和可靠事项闭环，能否在相同模型与成本约束下，明显改善持续相处的体验。** 如果这一点没有改善，更多情绪参数、自动反思和后台剧情也没有理由默认加入。

## 参考文献与案例

1. Bickmore, T. & Picard, R. W. *Establishing and Maintaining Long-Term Human-Computer Relationships*. 作者预印本 2004；TOCHI 正式论文 2005。[作者稿](https://courses.media.mit.edu/2004spring/mas630/Articles/BP-TOCHI.pdf)；[MIT 资料页](https://www.media.mit.edu/publications/establishing-and-maintaining-long-term-human-computer-relationships/)。
2. Zhang, S. et al. *Personalizing Dialogue Agents: I have a dog, do you have pets too?* ACL 2018。[论文](https://aclanthology.org/P18-1205/)。
3. Rashkin, H. et al. *Towards Empathetic Open-domain Conversation Models: A New Benchmark and Dataset*. ACL 2019。[论文](https://aclanthology.org/P19-1534/)。
4. Zhou, L. et al. *The Design and Implementation of XiaoIce, an Empathetic Social Chatbot*. Computational Linguistics 46(1), 2020, 53–93。[论文](https://aclanthology.org/2020.cl-1.2/)。
5. Park, J. S. et al. *Generative Agents: Interactive Simulacra of Human Behavior*. UIST 2023；所读 arXiv v2。[论文全文](https://arxiv.org/html/2304.03442v2)。
6. Marsella, S. C. & Gratch, J. *EMA: A process model of appraisal dynamics*. Cognitive Systems Research 10(1), 2009, 70–90。[期刊](https://www.sciencedirect.com/science/article/pii/S1389041708000314)；[作者保存版本](https://people.ict.usc.edu/~gratch/CSCI534/Readings/COGSYS-RS-EMOTION-2008-6.pdf)。
7. Kim, H. et al. *FANToM: A Benchmark for Stress-testing Machine Theory of Mind in Interactions*. EMNLP 2023。[论文](https://aclanthology.org/2023.emnlp-main.890/)。
8. Wu, D. et al. *LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory*. ICLR 2025；arXiv v2, 2025-03-04。[论文](https://arxiv.org/abs/2410.10813)。
9. *Proactive Agent: Shifting LLM Agents from Reactive Responses to Active Assistance*. arXiv 2410.12361，2024，所查版本 v3。[论文](https://arxiv.org/abs/2410.12361)。
10. *PRISM: Festina Lente Proactivity — Risk-Sensitive, Uncertainty-Aware Deliberation for Proactive Agents*. arXiv 2602.01532，2026-02-02，预印本。[论文](https://arxiv.org/abs/2602.01532)。
11. Sharma, M. et al. *Towards Understanding Sycophancy in Language Models*. 2023；本次使用作者研究介绍。[作者资料](https://www.anthropic.com/news/towards-understanding-sycophancy-in-language-models)。
12. Levinson, S. C. & Torreira, F. *Timing in turn-taking and its implications for processing models of language*. Frontiers in Psychology 6:731, 2015。[论文](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2015.00731/full)。
13. Wu, D. et al. *LongMemEval-V2: Evaluating Long-Term Agent Memory Toward Experienced Colleagues*. 2026-05-12，Work in Progress。[论文](https://arxiv.org/abs/2605.12493)。
14. Mai-with-u，MaiBot 回复必要性实现，固定提交 4213d8c5dddc9fd21e4ce69939894525c607bb2b。[源码](https://github.com/Mai-with-u/MaiBot/blob/4213d8c5dddc9fd21e4ce69939894525c607bb2b/src/maisaka/reply_necessity.py)。
15. YesWeAreBot，HDSI-AthenaBrain 可见 fork，固定提交 0c9c9b5a31a95bd14e521c8d8e61e465775eaa27，架构文档适用 0.1.3。[文档](https://github.com/YesWeAreBot/HDSI-AthenaBrain/blob/0c9c9b5a31a95bd14e521c8d8e61e465775eaa27/docs/ARCHITECTURE.md)。
16. SillyTavern，Group Chats；VedalAI，Neuro SDK Best Practices。[ST](https://docs.sillytavern.app/usage/core-concepts/groupchats/)；[Neuro SDK](https://github.com/VedalAI/neuro-sdk/blob/main/API/BEST_PRACTICES.md)。
17. NoneBot、Telegram、ComfyUI、Genshin-bots 官方文档与源码。[NoneBot](https://nonebot.dev/docs/advanced/runtime-hook)；[Telegram](https://core.telegram.org/bots/faq)；[ComfyUI](https://docs.comfy.org/development/comfyui-server/comms_routes)；[GsCore](https://github.com/Genshin-bots/gsuid_core)。
18. 本地天枢当前 API 与 V2 设计基线，读取于 2026-09-11。[API](C:/YOKI/Codex/Tianshu-AI-Core/docs/api.md)；[V2](C:/YOKI/Codex/Tianshu-AI-Core/docs/architecture-v2.md)。此讨论不修改天枢权威合同，也未验证新增 NoneBot 接入。
