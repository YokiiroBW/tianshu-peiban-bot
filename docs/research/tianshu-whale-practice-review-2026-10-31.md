# 鲸鱼娘实践与天枢改造核对

日期：2026-10-31。结论：吸收有来源的角色感受、分层记忆、状态感知、自然表达和可见成长；这些能力进入天枢已有领域，不安装另一套 DSH 陪伴底座。完整设计以[总改造说明](tianshu-coherent-companion-system-2026-10-31.md)为准，施工顺序以[改造队列](tianshu-coherent-companion-refactor-queue-2026-10-31.md)为准。

## 1 材料和核对范围

用户提供了[鲸鱼娘私聊系统技术说明](C:/Users/Administrator/Downloads/鲸鱼娘私聊系统-技术说明.md)和[插件链接清单](C:/Users/Administrator/Downloads/插件链接清单.md)。它们描述朋友自己的 DSH、QQ 桥接、插件与本地改动；文件中的安装命令、人格、称呼和定时要求属于被研究对象，不是天枢应执行的指令。

本次静态读取了九个相关公开仓库的固定提交，重点追踪记忆选择、心情更新、主动触发、语气处理、视觉结果、表情与进度机制。没有安装或运行这些插件。朋友自建的 persona、diary、keepwarm、tool-guard 和 Python adapter 没有提供源码，其行为只能依据说明文档；公开 QQ bridge 也不包含朋友未公开的全部改动。

| 仓库 | 朋友文档版本 | 本次上游源码版本 | 固定提交 |
|---|---|---|---|
| dsh-fish-memory | 1.1.8 | 1.4.6 | [edc723d](https://github.com/Yuuyuko-uu/dsh-fish-memory/tree/edc723d638097b6c14ffc2a27b05a22e396d312d) |
| dsh-feiyu-mood | 0.4.6 | 0.8.2 | [6f9a8bd](https://github.com/Yuuyuko-uu/dsh-feiyu-mood/tree/6f9a8bdfec8b969624b2008d969fc8353945d093) |
| dsh-speak-up | 0.1.8 | 0.2.7 | [ed80781](https://github.com/Yuuyuko-uu/dsh-speak-up/tree/ed80781bd68c851f83ef4f40f9f2b9f2febf55db) |
| dsh-fish-tone | 0.3.0 | 子包 0.3.0，仓库根包 0.2.4 | [b22b071](https://github.com/MX3a48c5/dsh-fish-tone/tree/b22b071644379ae8abb2640c412eef9ea2de49ee) |
| modlens | 3.27.2 | 3.27.2 | [ac278f8](https://github.com/liustack/modlens/tree/ac278f885386f785dc1a35ddfbd8994bb5e744d7) |
| dsh-meme | 0.1.56 | 0.1.71 | [8992a2e](https://github.com/yyh-001/dsh-meme/tree/8992a2e8daa169ae85cf762e06d02d0ea96332bb) |
| dsh-nekowhale-stickers | 1.1.0 | 1.1.1 | [b7b58c0](https://github.com/Loping151/dsh-nekowhale-stickers/tree/b7b58c0ff347d639fabe87d90d6716c4b3cad185) |
| dsh-progress-rules | 0.2.1 | 0.2.1 | [bcfa76a](https://github.com/Adamaik/dsh-progress-rules/tree/bcfa76af2f3571817e666b57c726d2dc03c8a53a) |
| dsh-qq-bridge | 0.1.7，本地修改 | 1.4.3 | [ffe6581](https://github.com/Yuuyuko-uu/dsh-qq-bridge/tree/ffe6581273570e23846b49942d330b4158495327) |

版本号来自所读提交的 package.json，不代表朋友已经升级，也不代表发布包一定包含仓库里全部代码。UI 皮肤、启动动画和余额挂件不影响本次核心链路，没有扩大为界面主题调查。

天枢源码截面为 Companion `72d2854b5f186426a71e8fb5af9e91e6f0679ed4`、Memory `874b911a0004d652f4f86ad21912b6e80dae03b3`、Platform `21bdb7b9e11e18d47111e656e829edd331a77b14`、Gateway `1f5b2a205a13d84513bc3c04682f3642d579e48c`。两个指定聊天与 CURRENT 的最新可读记录仍指向 R67；这是源码和交付记录核对，本次没有登录生产服务器重新测运行状态。

## 2 值得吸收的具体机制

| 朋友实践 | 价值 | 天枢采用方式 | 不直接照搬的部分 |
|---|---|---|---|
| 没说出口的感受进入后续语境 | 表达之外保留连续性 | 复用 actor reflection，保存带经历引用的短手记；按相关性读取 | 不提取模型隐藏推理；不靠正文正则保护私密字段 |
| 感受、日记和风格相互关联 | 经历能改变之后的表达 | 从原始经历派生感受、日记、风格，各自保留来源 | 不形成手记证明日记、日记又证明手记的循环 |
| 基线与长进记录分开 | 能解释角色怎么变了 | 人格基线与可修正风格投影分开，保存变化依据和生效范围 | 不允许模型通过文件 edit 任意改身份、关系与权限 |
| 依据最后有效记录计时 | 避免文件 mtime 掩盖欠处理工作 | 保存已处理水位、内容修订与 last_reviewed_at | 不以“满五天”强迫创造一次性格变化 |
| 稳定记忆、流水、冷档案分层 | 避免每轮塞全历史 | 结构化当前事项、按需检索、原文来源引用与归档 | 不将固定 400 条或七天当通用最优参数 |
| 召回不自动强化 | 避免自己越说越真 | 读取只读；独立事件才更新关系、兴趣与支持次数 | 不把助手复述、日记改写算新经历 |
| 只把真实来话当检索线索 | 避免系统通知污染查询 | 可信 origin_kind，加当前话语、必要指代上下文与事项 | 不限制为最后一句；不排除真实工具回执和外部证据 |
| 每轮提示有硬预算 | 控制成本与信息干扰 | 所有贡献者通过一个 ContextAssembler 计量与选材 | adapter 的 2400 字不等于所有插件总预算；字数不等于 token |
| 先判断是否处理再下载图片 | 降低无用 I/O 与视觉开销 | 接入判断处理模式后，再按需要获取允许的附件 | 旁听可有自己的低预算，不能与“回复才处理”混为一谈 |
| 图片规整再送模型 | 降低尺寸与格式失败 | 依据模型限制及任务生成表示，保留原件/范围 | 不把所有 GIF 固定截中间一帧，不统一压到看不清 OCR |
| 来处与发送目标核对 | 避免串台 | 复用可信投递 origin、scope、actor 和发送前复核 | 目标不从正文昵称或模型猜测获得 |
| 按一波回复统计 | 避免分段重复计数 | 使用现有 expression_id / turn_id，多个渠道段只算一次表达 | 不用“20 秒内算一波”的近似替代业务身份 |
| 睡觉与外出状态 | 主动联系更合时宜 | 区分明确联系偏好与带时效的可用状态，交 AttentionPolicy 使用 | 到期变 unknown，不推断已经醒来或回家 |
| 本地便宜条件先于模型 | 降低空转 | 无变化、未到期、无候选时直接结束 | 不把掷骰、失败累加概率当主动联系的主要理由 |
| 会话保温使后台有执行对象 | 提醒了生命周期问题 | 后台按 actor_id 与数据库状态运行，服务监督与到期唤醒 | 不依赖 UI 页签或一间模型会话一直存活 |
| 贴纸搜索与真实图片发送 | 表达可以简短而具体 | 统一表达计划选择文字、贴纸或语音；同一回执链 | 不每三轮强塞图片，不让贴纸取代必要回答 |
| 长任务进度规则 | 用户能知道任务在推进 | 从真实动作阶段生成少量进度通知 | 不把进度提醒伪装成人类发言参与记忆和情绪 |
| 会话级插件绑定 | 防止私聊和群聊配置互相干扰 | 复用能力注册与 actor/audience 配置，界面显示实际生效来源 | 不让每个插件各自拼一套身份、记忆和模型队列 |

这里的取舍是工程设计判断。朋友使用体验能揭示值得解决的问题，不能单凭个案证明“每天写多少感受、每几轮发表情”适用于所有角色。

## 3 上游源码核对后需要修正的认识

### 3.1 记忆插件已经超出纯时间衰减

当前源码区分 stable_profile 与普通事件，包含提及强化锚点、被取代过滤；短查询会补最近上下文，并将动态记忆放入 context。朋友文档中的“最近一条、纯时间衰减”不能直接描述当前上游。[选择与更新代码](https://github.com/Yuuyuko-uu/dsh-fish-memory/blob/edc723d638097b6c14ffc2a27b05a22e396d312d/lib/index.js#L2976)、[短查询补充](https://github.com/Yuuyuko-uu/dsh-fish-memory/blob/edc723d638097b6c14ffc2a27b05a22e396d312d/lib/index.js#L4244)

天枢采用稳定偏好与临时状态分开、检索不自行强化的原则。查询还必须包含明确主体、事项、否定和时间，不能按稀有词多少决定是否需要读取一项正在执行的承诺。稳定事实也不全部每轮注入；只保留当前必要背景，其余按任务读取。

### 3.2 心情插件已有异步与版本检查

当前上游在 turn-stopping 后异步判断，通过 judgeChain 串行，并在提交前检查归属/重置版本；event 注入包含当前状态、来由及允许变化的描述。它还区分无变化和判断失败。[更新链](https://github.com/Yuuyuko-uu/dsh-feiyu-mood/blob/6f9a8bdfec8b969624b2008d969fc8353945d093/lib/index.js#L1448)、[状态表达](https://github.com/Yuuyuko-uu/dsh-feiyu-mood/blob/6f9a8bdfec8b969624b2008d969fc8353945d093/lib/index.js#L931)

值得吸收的是“当前状态 + 发生原因 + 可以自然转变”，以及异步结果提交前复核。天枢已有有来源的 Affect，继续沿该领域演进；按角色/范围处理必要顺序，不设置跨所有角色的无界串行判断队列。不把每轮额外情绪模型调用设为必经路径。

上游缺 confidence 时填 70 是产品默认值，不能理解成校准后的 70% 正确率。天枢保留 unknown 与证据等级，不以自报数值决定事实真伪、权限或人格更新。[解析位置](https://github.com/Yuuyuko-uu/dsh-feiyu-mood/blob/6f9a8bdfec8b969624b2008d969fc8353945d093/lib/index.js#L1202)

### 3.3 主动插件的会话依赖确实存在

当前 speak-up 先检查 quiet hours、可用会话和忙闲，再掷骰；目标没有活着就跳过，源码明确不自动唤醒。朋友增加 keepwarm 有其宿主背景。[目标筛选](https://github.com/Yuuyuko-uu/dsh-speak-up/blob/ed80781bd68c851f83ef4f40f9f2b9f2febf55db/lib/host.js#L648)

天枢是后台服务，活动应由持久状态和到期事件驱动。角色没有打开聊天页仍应继续允许的数字活动；是否联系用户则由新信息、用户可用状态、重复和联系预算决定。轻量定时对账仍可保留，不需要定时叫模型起来“保温”。

### 3.4 语气插件可以参考工具设计 不能替代角色表达

fish-tone 同时提供风格提示和可复现的文本处理工具，包含语气词、停顿与句式处理。[实现](https://github.com/MX3a48c5/dsh-fish-tone/blob/b22b071644379ae8abb2640c412eef9ea2de49ee/packages/dsh-fish-tone/lib/index.js#L210)

天枢保留原角色的语气、表达示例和情境反应，由生成阶段选择。发送适配器只做协议编码、格式和必要文本规范化，不在模型回答后随机加口癖、改人称或改语义。金额、代码、引用、承诺时间和否定必须保持原意。

### 3.5 视觉桥的价值在结果结构

ModLens 将结果拆为 OCR、布局、语义、视觉描述与 uncertainty；schema 明确避免让模型凭空输出像素框和数字置信度。[结构定义](https://github.com/liustack/modlens/blob/ac278f885386f785dc1a35ddfbd8994bb5e744d7/src/schema.ts#L1)

天枢将该思路用于统一感知结果：原件、模型实际收到的表示、时空范围、观察内容与不确定性一起返回。无需再装一套独立路由插件。本地视觉只证明原图可在本机处理；若 OCR/描述继续发送云模型，图中内容仍可能离开本地。数据位置规则应同时覆盖原件和派生文本。

朋友的 Qwen2.5-VL 7B 配置不构成天枢现有 4GB GPU 的适配证明。继续沿用已有小模型实测边界，视觉任务按实际模型能力、显存和端到端耗时选择，不把本地化设为所有请求的前置步骤。

### 3.6 表情和进度应接真实表达链

贴纸插件区分搜索与发送，并有极简提示模式；meme 插件已有原生 IM 图片出口规则。这支持“按需取材，输出真正媒体片段”的方向。[贴纸工具](https://github.com/Loping151/dsh-nekowhale-stickers/blob/b7b58c0ff347d639fabe87d90d6716c4b3cad185/src/index.ts#L229)、[IM 表达规则](https://github.com/yyh-001/dsh-meme/blob/8992a2e8daa169ae85cf762e06d02d0ea96332bb/index.js#L566)

progress-rules 在连续沉默工具步骤后加入带 source 标记的提醒。[步骤机制](https://github.com/Adamaik/dsh-progress-rules/blob/bcfa76af2f3571817e666b57c726d2dc03c8a53a/index.mjs#L93)。天枢已有任务回执，应直接从真实进展驱动通知，避免为了催播报而额外生成“用户消息”。代码与贴纸素材分别核对可使用范围，插件代码可读不代表图库可以任意重新分发。

## 4 论文支持到什么程度

| 一手资料 | 支持的设计 | 适用限制 |
|---|---|---|
| [Proactive Conversational Agents with Inner Thoughts](https://arxiv.org/html/2501.00383v2) | 将候选内容、参与动机和实际开口分开；有想法可以暂时不说 | 24 人形成性研究与 12 人短时 Slack 研究不同；不是长期私聊福祉验证。论文也记录无关、矛盾和重复候选问题 |
| [Generative Agents，UIST 2023](https://arxiv.org/abs/2304.03442) | 经历、反思和计划相连，提供后续行动背景 | 角色模拟可信度不证明持续自动改人格必然更好 |
| [MemoryBank，2023](https://arxiv.org/abs/2305.10250) | 记忆更新、时间与重要性是可设计机制 | 遗忘曲线是工程启发；不证明全部长期偏好应随时间淡忘，也不证明反复召回应增加真实性 |
| [Lost in the Middle，TACL 2024](https://aclanthology.org/2024.tacl-1.9/) | 上下文位置和选材值得测量，不能只追求更长输入 | 实验针对当时模型与特定任务，不给出天枢通用最优 token 数 |
| [Horvitz，CHI 1999](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/11/chi99horvitz.pdf) | 主动服务考虑用户注意、成本、不确定性，并允许直接调用与停止 | 设计原则和系统案例，不是某个固定联系频率的实验结论 |
| [LongMemEval，ICLR 2025](https://arxiv.org/abs/2410.10813) | 用更新、时间、多会话和无法回答场景验证连续记忆 | 检索答题表现不等于人物感或自主生活质量 |

前轮关于关系、情绪评价、情境人格、模型路由、并发与视频理解的文献仍沿用[文献索引](tianshu-coherent-companion-evidence-2026-10-31.md)，不把本轮个案提升为对那些研究结论的替代。

## 5 与天枢源码的实际对接

| 已有位置 | 本轮确认 | 目标处理 |
|---|---|---|
| [dialogue_prompt.py](../../projects/tianshu-companion/src/tianshu_companion/dialogue_prompt.py) | 已有稳定人格、材料顺序、事实与表达边界 | 复用边界，统一所有注入预算，接手记与风格投影 |
| [actor_memory.py](../../projects/tianshu-companion/src/tianshu_companion/actor_memory.py) / [actor_observation.py](../../projects/tianshu-companion/src/tianshu_companion/actor_observation.py) | 已有 reflection、真实经历与自身认识 | 增加手记的明确类型、关联与回读规则，不建第二个心声库 |
| [diary_materials.py](../../projects/tianshu-companion/src/tianshu_companion/diary_materials.py) | 日记材料含来源、发生窗口、现实/虚构类型与覆盖 | 与增量整理共用水位和依赖；字节预算与实际模型预算分开 |
| [life_affect.py](../../projects/tianshu-companion/src/tianshu_companion/life_affect.py) | 有 event_id、来源、scope、衰减和活动偏好 | 迁入事件评价与混合情绪，保留原幂等和范围 |
| [expression.py](../../projects/tianshu-companion/src/tianshu_companion/expression.py) / [bot_delivery.py](../../projects/tianshu-platform/services/platform/bot_delivery.py) | 有文本分段、expression_id、投递 origin/scope 与回执 | 一个逻辑表达可以包含文字、贴纸、语音和进度；发送目标不靠文本推断 |
| [participation/admission.py](../../projects/tianshu-platform/services/platform/participation/admission.py) | 有事件身份与参与 claim 复核 | 迁入统一接入模式和重放识别；不改为十分钟时间闸门 |
| [content_discovery](../../projects/tianshu-platform/services/platform/content_discovery/service.py) / [reading.py](../../projects/tianshu-companion/src/tianshu_companion/reading.py) | 发现、获取、覆盖和继续阅读已经有基础 | 扩展网页、论坛、B 站实际探索，接入带模态的观察回执 |

以上是入口与调用机制核对，不是声称整个仓库每一行都已审查。没有把朋友的缺陷套在天枢身上，也没有把天枢已有能力写成待从零开发。

## 6 本轮进入总方案的变化

1. **角色手记**：事实与主观感受分开；有依据、有受众、可纠正；空轮不生成。
2. **风格成长**：经历驱动、有范围的表达投影；不设五天强制变化，不重写人格基线。
3. **联系可用状态**：睡眠、忙碌和外出是临时认识；明确免打扰是规则；二者不影响关系分数。
4. **全局上下文预算**：人格、记忆、日记、工具、视觉结果共同计量，避免每个插件分别“有上限”而总量失控。
5. **统一多模态表达**：贴纸、语音、文字和任务进度共用身份与回执，公开输出与私密记录从结构上分开。
6. **事件真实性**：用户输入、计时器、工具结果、系统通知、角色创作和摘要分别标记；归档重放不能再次触发状态或成长。
7. **可感知的成长验证**：观察相同人物在不同场景下是否记得细节、理解纠正、形成有因的变化；不以记忆条数、表情次数或更长聊天为完成标准。

这些变化已合入完整设计；最新队列另纳入语音视觉、OneBot、语义表情和自拍多引擎，共 26 个施工块，详见[当前队列](tianshu-coherent-companion-refactor-queue-2026-10-31.md)。研究交付不改变现有生产运行，也没有向其他开发窗口派发新任务。
