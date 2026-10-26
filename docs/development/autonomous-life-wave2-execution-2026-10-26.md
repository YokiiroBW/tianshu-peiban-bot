# 自主生活内容发现与群聊接入实施

2026-10-27 发布收口：五个核心服务、Chat 与独立 OneBot 的固定版本已实际部署，运行源码/镜像、原挂载与健康已核；新 actor 域与用户域独立，25 个逻辑源/3 个 guard 的冷备恢复已闭合。AL08/AL11 运行基础上线，AL12 尚未实现，普通候选继续静默；真实群聊、真实搜索和外站账号动作未验。后端运行验收已通过，外部 QQ 尚未登录，完整运行验收尚未通过；等待登录后复核。专属 token 新凭据真实 HTTP 阳性为 0/3；完整状态按逐 principal 收据记录，不以组件通过代替 HTTP 阳性。 共享凭据暴露仍 open，未改共享口令或 Chat 签名密钥。 实际结果见[联合发布](autonomous-life-memory-release-2026-10-27.md)；下文为实施时截面，保留原边界与验证历史。

用户已授权继续 AL08、AL11，并与“记忆系统隔离测试接续”双向协调写入边界。此批只实施平台与其共享适配器，不写 TS313 的 Memory 或 TS314 的 Companion。功能稳定后集中审查和验证。

## 基线与分派

实际发布平台为 `506a82062166cef457974ef9b95789010e3435d0`；TS316 在 `209df023f223027880d597191ac3a49dcca1459c` 已合 TS310 能力桥 `90c0d44`，不能重复合入。TS316 已最终本地交付 clean 候选 `3e2cc412c676450a90dc0cbd3c3387d1c945e5e2`，共享装配路径已释放。冻结合同目录为 `.runtime/memory-extensions-20261026/contracts-v7`，保留旧版本并核对最终消费 pin。

2026-10-27 协调方先释放 AL08 非重叠路径，随后精确追加媒体只读分页和最终服务装配。已在原 allocation 锁内通过新鲜任务板、依赖和容量校验登记并更新 [TS-318](TS-318-AL08-content-discovery.md)，从该 clean SHA 创建 `worktrees/TS-318/tianshu-platform`、`codex/ts-318-content-discovery`。根原 index 保持，其他任务行保留。TS318 已最终提交 `6d8dbaf29979c210c027f36a8333b5a605f7c3e6` 并独立验收，72 项通过、1 项未配置 PG 跳过；六项实际审查缺陷修复闭合。状态仅为 local_verified_not_integrated，不推送部署。精确证据见[第二批交付](autonomous-life-wave2-delivery-2026-10-26.json)。

本次按明确释放实施路径单写：TS318 的独立实现、媒体分页和 service/actor_bridge 总装均由同一实现者串行完成，集中后由协调者及另一子代理独立审查。TS316/TS314 的原工作树不改。AL11 新命名空间及适配器单写权已经明确委托，[TS319](TS-319-AL11-participation-contracts.md) 候选提交 `188b52a` 已独立验收；[TS320](TS-320-AL11-participation-adapters.md) 与 [TS321](TS-321-AL11-platform-participation.md) 已在独立产品工作树并行实施。前者只写 Companion integrations，后者从 TS318 最终历史写 Platform；不改 Companion src 或 Memory。精确白名单以各卡为准，本文后续整体范围不构成越界授权。

## AL08 内容发现

沿现有插件安装、实例配置、Vault、角色绑定和执行台账，实现内置内容发现插件及一个窄只读 Host 端口。Platform 已有媒体 owner 负责目录，插件不读媒体数据库或调用管理员网页会话。

搜索适配一种真实协议后端，由实例配置服务地址、Vault 配置凭据，复用 `http.client`。请求只提供查询意图，不替换账号、服务地址或凭据。无配置或来源失败时返回具体状态，同时保留本地目录候选；搜索摘要与正文分别标明。

媒体发现复用 B站订阅成员及分P、音乐 recording/source、书籍与漫画 catalog/version。禁止为发现调用订阅扫描、格式探测、下载或修改用户阅读进度。目录向角色仅投影标题、作者、类型、取得时间、原 owner 稳定定位、候选版本和实际可读性，不返回路径、短期播放地址或管理记录。

去重使用原内容身份；网页规范 URL，同标题不能证明转载。候选元数据摘要不冒充正文 hash。已读经历仍由 AL03/04 唯一拥有；本批可消费已有内容排除键及保存选择/跳过状态，但不得把选择或搜索命中记为真正看过。

新增端口按实例实际注入的提供者登记，所有原消费者从同一实际可用端口集合读取，不能全局声明可用而宿主没有 handler。角色沿原 invocation/principal/binding 推导，现 authorizer 在排队及每次 Host 调用继续核验。

TS318 已登记并验收的实现范围：

- `services/platform/content_discovery/`
- `services/platform/plugins/builtin/content-discovery/`
- `services/platform/plugins/{manager,ports,port_schemas,execution,actor_bridge}.py`
- `services/platform/service.py` 的薄装配
- 必要原 media catalog 的窄只读能力，逐路径向协调者登记
- 对应 `test_content_discovery*.py`、相关旧插件/媒体测试及产品说明和新卡交接

不新增 server/auth 入口，不另建媒体、账号、正文或阅读记忆存储。界面复用现插件配置和角色绑定页。

集中验收包含真实插件子进程经角色桥搜索/列目录、单来源失败与替代、跨角色与私有库范围、URL/原身份去重、版本变化、选择与已读区分、同意图回执、重启、去敏与响应预算，外加实际受影响的旧插件/目录回归。真实搜索服务、真实账号、PG、Knowledge 消费分别记，不以本地 HTTP 夹具代替真实接通。

## AL11 普通群参与与出站

现平台 `decision()` 区分观察、回复许可与触发；适配器对非 @ 群消息却写入 `eligible=0/claim_resolved=1`，poll 仅返回最终 `reply_claimed`。仅放宽平台触发条件不能实现安全参与。

先明确候选、语义决定、宿主唯一回复 owner 和投递四个阶段。普通许可群可产生候选；observe_only 保持静默；明确 @、关键词与命令沿原优先路径。AL12 决策消费者尚未接入时，普通候选不能自动变成必回。当前授权/来源/角色、候选有效期与原 claim 每次使用均需核对。

必须由同一实施卡唯一作者维护 canonical 共享适配器和 NoneBot/AstrBot 分发副本。宿主已将消息交其他插件后，迟到语义决定不能追认回复 owner；不能为了等决定阻断所有普通消息。具体宿主处理完成证据、候选/决定 wire 形状与 claim TTL 在开工卡冻结，无法证明的宿主能力明确不可用。

固定宿主版本核对：NoneBot 2.5.0 可在其他 matcher 实际运行前记录已接管，并在事件处理收尾后产生受控候选；其 event_postprocessor 本身只证明 matcher 分发结束，不能单独证明所有并行后处理或脱离任务结束。AstrBot 4.27.3 的公开插件钩子不提供整条 pipeline 完成证明，现阶段普通候选必须保留 ownership_unknown，不能自动签发语义 claim。完整自动支持仍需明确的宿主完成能力；不通过低优先级猜测或猴子补丁伪造。

原始依据为固定版本 [NoneBot 事件分发](https://github.com/nonebot/nonebot2/blob/v2.5.0/nonebot/message.py)、[AstrBot RespondStage](https://github.com/AstrBotDevs/AstrBot/blob/v4.27.3/astrbot/core/pipeline/respond/stage.py) 与 [PipelineScheduler](https://github.com/AstrBotDevs/AstrBot/blob/v4.27.3/astrbot/core/pipeline/scheduler.py)。这是源码核对，不是新增宿主 hook 的实测通过。

复用现 BotDelivery 台账、发送回执和恢复身份，不能另建出站队列。普通参与、显式回复和重试必须收敛到同一消息去重及唯一 owner；过期 claim 不续授，unknown 不换键重发。观察事件是否入记忆继续由既有观察链维护。

正式路径在适配器审查后精确登记，预期为平台 `bot_observation/bot_delivery/bots/server` 的必要薄扩展、独立 participation 领域、单一 shared adapter 源及其两个发行副本和相应测试。Memory/Companion 与原来源权威不在本卡。

TS319 最终候选为 `observation-participation/candidate-v1`，manifest LF SHA256 `347b60a1dde6967adf559597e5bd7dfaa948f5b268d158d04ea3e3ad78b7c826`，提交 `188b52a6cc8fe8736bf569d117bcf7517cfc1ddc`。规范包沿原 manifest 机制，适用 10 项结构检查（新状态 9 项、未变日历 1 项复用），30 正例/48 反例及 15 个仅形状场景；独立复核通过，不代表实际 CAS/SDK 验收。原提案曾假定候选已有 person/entry，实际核链后已修正为 observed/registered/resolved，并区分已解析身份与本次 source_confirmation。两消费者只读使用复制的完整 `.runtime/autonomous-life-20261026/contracts-wave2`，原 extension-v7 和其他包未改；新包仍非生产冻结。

验收覆盖许可群普通候选与静默决定、observe_only、@/关键词/命令优先、不抢其他插件、迟到/过期/重复决定、断线及重启、已执行/未知发送不重复；区分平台+适配器实际联合、Companion AL12 消费与真实 QQ。共享合同须经当前根合同 owner 确认后新命名空间发布，不能修改正在使用的用户记忆协议。

2026-10-27 的本地协议准备位于 `.runtime/autonomous-life-20261026/participation-proposal/`，修订 2 共七种内部 DTO，尚未发布为正式合同。独立审查已修正三处缺口：真实受理派发前登记、原回复创建事务同时绑定，并在领取排序前排除未绑定参与回复；领取同参回执查询先于 free/CAS，历史 grant 与当前有效性分别返回；NoneBot 异常/取消/缺失完成证明保持未知，其他 matcher 实际进入即永久占用。一次修订自检为 24 正例、29 反例、24 次 wire 校验、10 个运行场景形状及 12 条状态规格；这些仅证明提案形状/规格自洽，产品、SDK hook、CAS 与真实出站均未运行。首轮原结果单独保留，独立复核仅检查本次变化，没有重复未变化检查。AL11 产品写权仍未释放。

进一步固定源码核对发现 NoneBot `message.py` 的 permission/rule 异常会被吞为 false（v2.5.0 的 392–410 行），命令 trie 异常也仅记日志（554–560 行）；lifecycle hook 和生成器依赖收尾均不能提供这些失败结果。因此首版仅支持由现 `integrations/nonebot/tianshu_nonebot/onebot.py` 构造、只装天枢且处理器集合受控的独立宿主，配置中的 adapter_kind 字符串本身不算证据。未知处理器、动态变更、未登记任务一律 unknown。

在原 `_observe`、`_claim_observation_reply`、`_scope` 的本次执行中分别记录成功收据：候选确实持久接受、显式观察回复正常判断未接管、旧精确路由正常确认无 owner。现 observer 忽略捕获返回且故意返回 false；原 `capture()==False` 同时涵盖无路由、多路由和队列容量失败，不能作为空闲证明。应扩展原内部结果/证明记录，不新建 RPC 或重跑过滤。收据关联同一事件记录，因为各 matcher 得到 state.copy；只有全部受控前后路径正常闭合且三种成功收据齐全、无其他 matcher 入场并仍满足当前代际/策略/期限，才可发布 free。依赖命令 trie 或不能给出正常收据的新处理器不在首版证明范围内。[固定分发源码](https://github.com/nonebot/nonebot2/blob/v2.5.0/nonebot/message.py#L392)与[插件公开接口](https://github.com/nonebot/nonebot2/blob/v2.5.0/nonebot/plugin/__init__.py#L151)为依据，尚未做 SDK 运行验收。

## 交付

两卡各自提交产品代码与 `docs/handoffs/新编号.md`，记录精确基线、命令和实际失败/跳过/未覆盖。主协调者检查完整差异、独立审查及合成顺序。无真实模型、外站/QQ发送、生产数据库迁移、推送或部署；已发布核心与本批本地候选分别记录。
