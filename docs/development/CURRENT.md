## 2026-10-18 TS191 网文长篇、正文检查与追更已部署验收

Platform运行 `da3539333ff3b799d1e177a4f594f7b6b8105972` / 镜像 `ea2f1d4169c78051ad3ff24e3d52aaa803fb72d43fdc383f2fa39e2d9a3c94c6`，交付文档ce4cd0c已推送。三网文源已接入并每日更新；正式网页《夜无疆》书海阁9页目录835章完整下载、逐段EPUB/宿主哈希及玄幻奇幻分类通过。第821章临时失败自动等待120秒后复用缓存完成。真实每小时扫描11:49:53成功且无新章；隔离历史833→835仅请求2新章、0旧章重抓，不冒称作者自然更新。107项NAS/0跳过、2项HTTP网页联合、38库冷备恢复、5TLS/guard、64资源及Dockge通过。9类视频音乐配置、原任务及非目标容器/Books与Ebooks挂载保留；原音乐队列仍有渠道拒绝/校验失败，未扩展修复。TS191 done；速读谷/得奇仅试读与分页已证实，整本稳定性未证实，书海阁更新落后。见[交付](books-serial-2026-10-18.md)和[精确记录](books-serial-2026-10-18.json)。

## 2026-10-18 TS189 三真实书源与分类书库已部署验收

Platform运行 `1c274e89801dc6af8e4a813aafff64684cbdf8b9` / 镜像 `2020ee9688321bf72759178d012f571ee23b528256c24eb73dc6614e5a4a30ff`，文档补充7706360已推送。正式网页中华典藏、殆知阁、维基文库聚合29条，三源版本分别完整3/1/1章，实际预览、元数据选择、订阅与EPUB落盘通过，CRC/OPF/正文/宿主哈希一致。Documents/电子书 → /media/Ebooks，类别/作者/书名分类；原5本内容与文件权限保持，旧Books挂载保留。90项NAS相关测试/0跳过、隔离真实三源重复复用、38库冷备及离线恢复、5TLS/guard/64资源/Dockge2快照通过。6视频订阅、3音乐账户、43歌单/43关联、1956音频/9视频任务及稳定配置保持，非目标容器保持，无数据库回滚。仅普通HTTP回退及脚本子集，完整WebView未实现。已结束本次生产写入，后续发布需保留两个书籍挂载并读取当前快照。详见[交付](books-compatibility-2026-10-18.md)与[机器记录](books-compatibility-2026-10-18.json)。

## 2026-10-18 书籍订阅与内置源仓库已部署验收

TS183/184 随联合 Platform `c843944` 已推送部署 NAS，镜像 `68805c0`；TS188 为唯一生产写者，容器 UI 同版本交付。38库冷备/离线恢复验证、单份完整冷备保留、10服务/5核心TLS/guard、64静态资源和Dockge2快照通过。6视频订阅、3音乐账户、43歌单/43Navidrome关联及1956音频/9视频任务身份与既有设置保持；原失败/等待任务未清除。书库 `/volume1/Documents/天枢书库` 映射 `/media/Books`。已登录实页验证仓库列表/搜索/直接订阅、后台导入/立即更新/停用保持，以及公版TXT两章预览和EPUB实际落盘、CRC/OPF/正文/哈希。中华典藏所选规则需要未支持的网页交互，已停用搜索并保留每日规则订阅；未证实真实远程正文成功。浏览器下载事件未取得，NAS成品独立验证通过。隔离Linux86项/0跳过，容器前端10项和工具适用58项通过；工具1项初始环境失败换既有正确解释器后通过。详见[发布与限制](books-deployment-2026-10-17.md)及[机器记录](books-deployment-2026-10-17.json)。

## 2026-10-17 冷备清理、连接恢复及真实网页验收完成

已删除47份旧冷备及多余校验副本，唯一保留37库完整冷备；占用160.99→2.40 GiB，回收158.59 GiB，可用约220 GiB。全NAS inspect短暂超时导致的守护停机已修复，67项NAS Linux验证通过；10服务/5TLS/guard ready，387非目标保持，无数据回滚。公共备份/发布/回退现自动在新备份验证后只留1份，17项NAS快照保留及46项共享恢复回归通过，源码已推送、公共3文件已部署。以后固定输入从当前Git的 `python -B -m deploy.infrastructure.recovery_input --output ABS_NEW_JSON` 生成，不复用历史私有旧输入。用户已自行登录；主NAS登记、后台采集、397容器四页、资源/详情/限定日志实测通过，TS182完成。3音乐账号/43歌单/43Navidrome关联及Dockge2快照保持。OpenWrt按用户要求暂缓；不把Compose部分结果/未实现的Dockge控制说成已完成。见[恢复与保留记录](infrastructure-recovery-retention-2026-10-17.json)。

## 2026-10-17 基础设施已部署，网页验收待用户登录

平台425e2b7已保留音乐1488ceb/32ce50e完整历史并推送部署，镜像263c6e2；根发布工具95c9377已验收推送。37库冷备/离线恢复验证，5核心TLS、10服务和guard ready；原367非目标容器保留，并发AssetLibrary新增2容器另记。发布时挂载顺序误报导致天枢保护停机，已修复并恢复，未回滚数据库；原scope网关续签与守护新代保留证据。重启tmpfs权限继承问题通过空宿主挂载点0700修复，无再次重启。真实NAS379容器、81运行容器第二轮CPU、69Compose partial原因和限定日志通过；43音乐订阅/43Navidrome链接/周期保持，Dockge2快照和62网页资源通过。TS180/181完成，TS182真实网页交互等待用户在已打开页面登录；未获取密码/伪造会话，OpenWrt按用户要求暂缓。见[发布与验收记录](infrastructure-deployment-2026-10-17.json)。

## 2026-10-14 上下文连续性与独立 OneBot 已推送部署

Memory `2bcdae5`、Companion `8faa31a`、Platform `337caf1` 与增量合同已推送 main 并部署 NAS。普通新增记忆保留仍有效的最近对话，更正/遗忘/撤权继续失效；结构化引用与 @ 信息贯通。37 库冷备及离线恢复校验、五核心 TLS、guard、Dockge 与配置保留通过。独立 OneBot 服务已启动，尚无真实 QQ 连接；原 AstrBot/NoneBot 宿主未升级。部署代码的隔离管理员/普通用户 OpenCode DeepSeek 自然记忆及重启接话复测通过，6 次调用后累计 1140/1140。未重跑百人基准；全量历史失败仍保留。[部署与验证记录](context-onebot-deployment-2026-10-14.json)。

## 2026-10-12 记忆更新已部署，自动整理保留关闭

Memory `165307f`、Companion `4e4fc83` 已推送 GitHub main 并部署 NAS；视频订阅 Platform `7d2403c` 已上线并复核59个资源。五核心TLS就绪、35库冷备/恢复校验、十容器/guard及Dockge同步通过，所有原settings保持。自动记忆整理原开关仍为false，开启及日程/日记候选的发布范围等待用户选择；未冒称真实模型/QQ验收。[部署记录](memory-subscriptions-deployment-2026-10-12.md)。

## 2026-10-11 订阅概览与模型功能分工已部署

Platform `7d2403c`、Companion `fbb378b` 已推送 GitHub main 并部署NAS；订阅概览/竖向导航/去掉冗余标题，以及六类功能模型选择上线。聊天与日常日记已接通；自动辅助委派/两阶段回复仍未实现，媒体目标仍为0。五核心TLS就绪、十容器/guard、59网页资源、34库冷备、Dockge同步通过；原模型/角色/管理员/天气和所有settings保持。[部署详情](subscriptions-models-release-2026-10-11.md)。

## 2026-10-10 视频订阅入口与运行基础已部署

Platform `619c944` 已合并视频订阅候选与当前线上 `cbd592b`，推送 GitHub main 并部署 NAS。入口 `/#/subscriptions`；账号/解析与媒体运行基础启用，**targets=0，完整下载发布尚未配置**。AssetLibrary `7742d1ed` 仍为独立本地候选，未覆盖其生产主线。69 项界面及69项实际后端/投递检查通过，另2条件跳过；初次合同换行与模块名错误保留记录。NAS五核心TLS就绪、十容器/guard、60静态资源、33库冷备、Dockge及原用户配置保留通过。[交付详情](features-release-2026-10-10.md)。根历史修改及旧小屋样片保留。

## 2026-10-08 延迟图片与发送授权修复已部署

Platform `cbd592b`、Companion `537ea03`、bot-delivery/v2.1合同`f3c4789`已推送main并部署NAS。修复短租期origin续接、关系缓存年龄误拦截及两种机器人发送入口64KiB限制。原图片任务已由真实QQ消息ID确认sent，未重新生图。十容器/五核心/guard、33库冷备、57静态资源、Dockge与既有用户配置保持均核验；全量历史失败边界及一次发布目录恢复见[交付记录](delivery-origin-deployment-2026-10-08.md)。

# 当前交接

2026-10-07 同日补修：用户管理员开关的生产 web operator 缺少 `qq.admin.manage` 导致 403；已获用户授权补齐并重建 Platform。运行容器权限配置、唯一配置差异、五核心就绪及原 QQ 管理员授权保留均核验通过。详见同日用户管理员部署记录。

## 2026-10-07 用户详情管理员开关已提交部署

Platform `70909d64b870072dfeecfbef1310d243adf4fe5a` 已推送 main 并部署 NAS。用户详情姓名旁直接设置/撤销管理员，原独立设置表单已移除；复用既有权限与接口。16 项浏览器回归及 2 项隔离真实 HTTPS 保存/撤销用例通过，固定 Linux 镜像检查通过。五核心 TLS 就绪、十容器、guard、57 个静态资源、Dockge 同步均核验；33 SQLite 冷备通过，管理员授权、用户与角色配置保持。未修改真实管理员或发送 QQ/模型/GPU 测试。详见 [部署交付](user-admin-deployment-2026-10-07.md)。

## 2026-10-07 天气与时段视觉更新已部署

Platform `0a3e50c97a9403611e79995b1c74a9b31d06c490` 已推送 main 并部署，镜像 `sha256:c9b922ab68aab9d4fe098ebe4dbd63c5f0aee04208d8e80f3a58c7b45f139e6e`。生活页移除天气归因网址和设置/服务商链接，原配置表单与归因集中移到「任务与设置 → 位置与天气」`#/settings/9`。天气图标按条件及显示当地昼夜变化；时钟旁增加深夜、清晨、早晨、中午、下午、傍晚、夜晚七态 SVG，保留数字时间/日期/时区，日程推进时区不变。未添加依赖或重复配置路径。

14 项定向桌面/小屏交互、类型/构建/格式检查通过，协调者已查看夜间桌面/小屏及设置截图。NAS 五核心 TLS live/ready 200、十容器运行、guard ready；57 网页资源逐哈希匹配且 no-store，设置第9页资源与 Dockge 同步验证通过。原天气密文配置、角色和用户状态与本次新冷备一致，未解密凭据、未改真实天气设置；其他九容器 ID 保留、Gateway 未重新签发。33 SQLite 冷备验证通过，路径 `/volume2/tianshu-v2-resident-updates/weather-period-ui-release-20261006-release/cold-snapshot`。回执 `.runtime/weather-period-ui-release-20261006/deployment-result.json`；产品交接 `docs/handoffs/WEATHER-CLOCK-2026-10-06.md`。生产验证未创建网页登录会话或主动调用天气、模型、GPU、QQ；交互验证为隔离合成数据。

## 2026-10-06 网页角色占位选项已统一移除并部署

Platform `099cbf573399562c816e337904915915408842b2` 已推送 GitHub main，NAS 镜像 `sha256:91b80701d70ab5375326e21be2b6fac0018230dd3f83b1c1af162f734e2a05c6`。统一在角色目录归属过滤未被用户采用的 `actor:household`，覆盖对话、生活/图片、小屋、人物/记忆、技能及 BOT 目录；无角色选择保持空白，不发送占位业务请求。真实角色、权限、历史账本不变；技能角色列表请求量修为契约允许的 50。

21 项定向后端、6 项桌面/小屏交互、类型/构建/格式检查通过。NAS 五核心 TLS live/ready 200、guard ready，56 网页资产匹配且 no-store，Dockge 已同步；除 Platform 外其余九个容器 ID 保留，Gateway 沿用原 origin，无重新签发。本次 33 个 SQLite 新冷备在 `/volume2/tianshu-v2-resident-updates/actor-directory-ui-release-20261006-release/cold-snapshot`。实际配置生活读者 TLS 读取加部署版目录规则/只读角色元数据核验，生活及记忆各保留澄汐一项，占位消失；这不是已登录生产浏览器交互验收。无模型/GPU/QQ 测试调用。回执 `.runtime/actor-directory-ui-release-20261006/`，产品交接 `docs/handoffs/ROLE-CHOICES-2026-10-06.md`。

## 2026-10-06 可更新角色技能已推送 GitHub 并部署 NAS

Companion `e0e7f98`、Platform `870e6fa` 与合同 `3c38a69` 已推送 main 并部署。五核心 TLS live/ready 200、十容器运行、capacity guard ready；澄汐技能目录读取成功，生图 available，GSCore not_configured，媒体扩展 unsupported；56 个网页资源匹配，Dockge 配置已同步。用户、角色、模型与 ComfyUI 设置保留，33 个 SQLite 冷备验证通过。部署前旧守护停机导致 Gateway 五分钟 origin 到期，已通过同条目受支持流程重新签发恢复，未改权限或恢复旧账本。未主动调用真实 QQ/模型/GPU/GSCore。入口「任务与设置 → 角色技能」，精确版本与限制见[部署交付](skills-v1-deployment-2026-10-06.md)。本条覆盖下方暂停部署状态。

## 2026-10-06 可更新角色技能已本地完成，部署暂停

Companion 最终候选 `e0e7f98`、Platform `870e6fa`；统一技能注册、逐角色启停与目录更新、生图重归属、GSCore 白名单 HTTP 适配和网页技能管理已完成定向验收。合同 `skills/v1` 已冻结，实际生产者三种响应通过 Platform 消费校验。GSCore 28765 已只读检查，现役接口关闭且 HTTP 实现有消费者/终态缺口，**尚未真实联通**；媒体聚合只留注册接口。用户要求暂停部署，本批未合入产品 main、未推送、未部署、未调用真实模型/GPU/QQ。精确版本、验证与限制见 [本地交付](skills-v1-delivery-2026-10-06.md)。本条不改变下方已部署版本。

## 2026-10-05 ComfyUI 深度接入已部署 NAS

Companion `41393d9`、Platform `e385df7` 已部署。Companion 已接入现有 frontend 出站网络，澄汐已绑定真实 8188 工作流，默认 `1024 × 1536`；真实 Gateway 中文辅助编译、隔离 GPU 成图、五核心 TLS 就绪、capacity guard 和 56 个 HTTP 网页资产均已核验。精确版本、证据及使用限制见 [部署交付](comfy-deep-deployment-delivery-2026-10-05.md)。本条覆盖下方“真实生图后端尚待配置”的历史状态；自然语言对话调用生图的后续修复仍单独验收。

## 2026-10-05 陪伴完整迭代已合入 GitHub 并部署 NAS

最终 Memory/Knowledge `097ccfa`、Gateway `ea7f592`、Companion `91d82dd`、Platform `c87c53b`、NoneBot 0.5.0；容量守护修复 `edc254b`。五核心 TLS 就绪、原用户配置保留、完整冷备/迁移/恢复、56 网页资源和 Dockge 同步已核验。主协调规划/集成/验收，产品由 GPT-6.1-sol/xhigh 子智能体实现，无新窗口。原工作区修改保留。

入口 http://192.168.31.210:18446 。[部署记录](companion-complete-deployment-2026-10-05.md)与[13 项覆盖](companion-complete-execution-2026-10-04.json)为本轮最终状态。真实生图后端尚待配置；本轮未主动发送真实 QQ/模型测试请求，受保护网页的交互证据来自隔离浏览器联调。以下保留历史交接，不覆盖本条。

2026-10-04 **OpenCode 会话修复已部署 NAS**：platform `6a2ed3d` / gateway `d1865da` / companion `a5725d0`。修复 HTTP 400 MissingSessionID，加入 Zen / Go 预设及可选测试错误码。NAS 五核心就绪、53 网页资源匹配、Dockge 同步；部署后一次真实短测试 HTTP 200、回复验真通过。详见 [部署记录](opencode-session-deployment-2026-10-04.md)。


2026-10-04 **工作区全宽布局已部署 NAS**：Platform `a9e466f`。共享页面和 QQ 管理身份页移除居中宽度上限，保留既有边距与手机布局。1920/3840 三页检查无横向溢出；NAS 五核心就绪、53 个网页资源匹配，Dockge 同步。详见 [部署记录](fluid-layout-deployment-2026-10-04.md)。


2026-10-04 **统一用户档案已合入 GitHub 并部署 NAS**：Platform `c1d717c`。用户目录集中展开共享画像/记忆、收到的消息、回复权限、关系与好感；无画像的已发现 QQ 联系人也能进入目录。NAS 五核心就绪、53 网页资源和真实 QQ 身份投影核验通过，Dockge 同步；未自动修改真实回复名单或发送消息。详见 [部署记录](user-hub-deployment-2026-10-04.md)。


2026-10-03 **生活页概念图落地与和风天气已合入GitHub并部署NAS**：Platform `480df16c`，时间轴摘要/悬停预览/完整详情、后台只读刷新、逐角色真实位置和当地时钟。天气使用既有加密目录，用户需在页面填写和风Host/API Key后选择城市；未执行真实和风账号联验。NAS十服务/五核心/guard及41网页资源核验通过，Dockge同步，其余产品镜像和配置保持。详见[部署记录](life-visual-deployment-2026-10-03.md)。


2026-10-03 **质量修复与角色独立日常已合入、推送 GitHub 并部署 NAS**，入口 http://192.168.31.210:18446。十个核心/日志服务运行、五核心 TLS 就绪、实际生活读链、NoneBot/Chat Audit 和前端 38 文件哈希检查通过；Memory 来源索引显式迁移完成，角色/人格/模型选择及 QQ 策略保留，Dockge 快照已同步。执行中容器枚举竞态及一次验收参数错误均已处理并记录；精确提交、镜像、备份、验证边界见 [部署记录](quality-life-deployment-2026-10-03.md)。本条覆盖下方本轮“未合入/未部署”状态，不改变其他任务或历史验证事实。

2026-10-03 **质量修复与角色独立日常 15 项已本地开发完成并提交，未推送/部署**。按用户最新授权，三个子智能体在同一对话并行完成，无新窗口；本条覆盖下方历史“仅 DSH 实施”等执行偏好。使用已核对 rc.5 来源的 `worktrees/quality-life-20261003/` 候选；精确产品提交、公共协议、逐项覆盖、既有失败和未执行范围见 [本轮交付](quality-life-delivery-2026-10-03.md)、[固定清单](quality-life-delivery-2026-10-03.json) 与 [已完成队列](quality-life-development-queue-2026-10-03.md)。历史发布授权与状态不表示本轮已合入 main 或更新 NAS；原工作区修改保留。

2026-10-01 发布准备：用户已授权先推送本人 GitHub、核验远端版本，再更新既有 NAS。隔离发布分支为 `release/2026.10.01-rc.1`；保留原产品 main 与协调目录既有未提交修改，不直接以 main 替换生产。角色关系正式合同为 `contracts/role-relationship/v1` 1.0.0，schema LF SHA256 `e96397bac2b6ad8ff9d23c023d7d3c5ba0701734b27053a05b9d0f65a7ff8ee6`。按 Memory → Companion → Platform 串行绑定、验证与提交，发布清单记录最终 SHA、实际检查、既有失败/跳过及部署门禁。以下是历史交接，不表示本轮发布已完成。

2026-09-30 记忆角色选择已集成部署NAS：Platform95ca75c、Memoryda14cfd（087f9c9仅handoff），线上Companion9863800保持；入口 /#/memory/0。顶端角色选择，概览/投影/记录/游标按actor隔离，切换清空，后台恢复先核验；Luna返修通过。真实Chromium联合1/10.037s、缓存UI2/2、合入后HTTPS1/4.318s、NAS十服务guard/五核心ready/授权Memory只读/LAN资源/匿名门禁/归档日志均通过，原账号人格保留。capacity-state为memory-role-20260930，Dockge同步。产品mainPdffedb2/M9e0c39f保留独立未部署QQ身份，禁止main直接作线上版本。完整证据memory-role-deployment-2026-09-30.md/json；无生产网页登录/角色写入/真实QQ模型消息。

验证节奏：用户要求完整功能块稳定后集中验证，必要共享边界/故障定位及时检查；不逐小改动测试，不重复未变化的成功检查。

执行设置：用户最新要求全部产品实现交DSH；Codex只架构/分派/验收/排错，不再创建Codex实现任务。保留DSH配置模型，旧Astra偏好仅为历史Codex任务。

2026-09-14 · 本轮本地L0已通过并集成a472825；TS-014网页登录/对话控制台、TS-071生活/日记核心并行，TS-080研究/项目记忆开始接续。真实账号/L1/生产另验。

已完成 V2 总稿、100 项需求映射、18 项拟议能力目录，以及契约、陪伴/记忆、平台/UI 三条并行规划审查。当前进入本地实现与验证，生产部署不在本批范围内。

目录采用根协调仓库 + 六个独立开发项目 + 旧代码参考快照。已有原目录保留；未提交修改另存覆盖包，不自动移植。协调检出保留已审查基线，各任务实现与测试进度以工作树及交接为准。

六个开发项目和十二份参考源码已独立检出，三份旧工作树覆盖包保存完毕。七个工具测试通过；六个原本地源码目录 HEAD/工作树状态复核未变。根协调 Git 的初始化基线为 `1a57325`，未推送远端。

当前接管：DSH模型已由用户配置为deepseek-official/deepseek-flash/max且routable=true，现有根工作区确实存在。阻断已变为会话文件session.jsonl.zstd路径缺失及standard预设列表为空；继续指令accepted后会话仍因ENOENT结束，新会话无法创建。保留用户模型/工作区，不重配或建立空日志覆盖。TS070/042/015执行卡和独立worktree已准备，实际尚未并行运行。诊断在.runtime/dsh-diagnostics，记录见dsh-dispatch-2026-09-14.json。

已集成平台c687398（资产后端及网页分批验收），网关b3b101f（37项），陪伴f101c6d（含生活/网页/内部图像，最后图像35项复核），记忆77fa8d7（开发448全套、协调62资料专项），归档e6d8e21（91项、1个Linux工厂待验跳过）。见reviews中的各任务验收记录。text-dialogue/v1与profile-memory/v1已发布；画像API与Core消费者均已合入，本地真实来源/批准链及本轮L0已通过，生产接入另验。窗口关联见parallel-run-2026-09-14.json。

工作区工具在主协调目录运行。任务检出中的 .runtime/workspace-context.json 指向主工作区，方便读取最新任务状态及本地视觉参考。平台构建已验证；真实模型、渠道与设备尚未联合验收。

入口：[开发流程](parallel-development-plan.md) · [任务板](tasks.json) · [契约审查](workstreams/contracts-review.md)。
