## 2026-10-14 历史644视频NFO与Emby导演头像已补全

TS-135 Platform `fcac745` 已推送部署；644视频关联780份NFO（778更新+2补建），简介、UP/导演和实际流信息补全。Emby逐路径核验全部780项及32位导演头像通过，原6受管条目保持；644视频身份和1597图片/字幕哈希、原下载台账均不变，未重下载。168BV用当前来源、3用明确历史来源；未知QN及原1条时长异常保留。Linux60+窄修3、执行器6、37库冷备、十服务/guard、59资源及Dockge通过。[交付记录](media-legacy-nfo-deployment-2026-10-14.md)。

## 2026-10-13 B站NFO与UP主导演头像已部署

TS-134 Platform `9a2dde3` 已推送部署；新下载/元数据刷新写简介、UP导演与UID/主页、每P实际流/QN/文件摘要，MID头像持久缓存并通过Emby人物API同步。旧任务冷缓存补源与三个任务、六NFO/六Emby条目实测通过；头像复用，视频hash/inode与原下载generation不变。Linux首轮141/补修16、37库冷备、十服务/guard、59资源及Dockge通过，settings/模型Key保持。旧644只读文件未改；高级下载格式策略保持。[交付记录](media-nfo-creators-deployment-2026-10-13.md)。

## 2026-10-13 已有B站媒体原位登记已部署

TS-131 Platform `33f0ce0` 已推送并部署；旧目录644视频/171BV原位登记并保存实际编码/音轨，643项通过身份/时长核验，1项本地386.878秒与B站5820秒不符，保留异常。重复执行幂等，原文件、既有任务和三个源不变，未新增下载。旧台账无实际QN，不冒称已全部可精确复用。复用原documents/schema2和Retention/DownloadCache，仅增import_roots配置。Linux57、37库冷备、十服务/guard、网页及Dockge通过。现有账号可读取4K/DV，FLAC→MP4存在已复现兼容问题，高级音轨未做完整下载保真验证。[交付记录](media-existing-import-deployment-2026-10-13.md)。

## 2026-10-13 存储磁盘四类占用已部署

TS-129/130 Platform `9a32650` 已推送并部署。订阅、Emby（排除订阅）、其他、未使用以四色及数值显示；五分钟后台缓存、扫描时间和真实未知状态接通。新增两个只读观察映射，正式UID10001实读14个Emby媒体根；Btrfs子卷归同容量池、allocated/ready且四项之和等于总容量，订阅与Emby独立inode复核一致。音乐根仅新增一条目录metadata ACL，原规则和文件ACL保持。Linux首轮49/补修40、网页18+8、真实本地HTTP2通过；37库冷备恢复、五核心TLS、十服务/guard、59资源及Dockge通过。见[验收记录](media-storage-breakdown-deployment-2026-10-13.md)。

## 2026-10-12 入库后缓存回收与媒体文件复用已部署

TS-127/128 Platform `5cc7876` 已推送并部署。入库核验成功后自动回收成功下载/输入/pkg，先持久登记媒体来源；新收藏夹目录真实复用BV1ibdVBKEMe/CID37584830724/64，source=library、generation1未增，三目录同inode/nlink4，18文件哈希与三条Emby HTTP206读取通过。旧两任务与新任务缓存均自动清理；三条旧失败信息已删除，去重/成品保留。网页“存储磁盘”显示实际约61.1TiB媒体卷，支持逐卷展示与删除记录。Linux98、网页114+12、实际本地HTTP8项通过；37库冷备恢复、五核心TLS、十服务/guard、Dockge和59资源通过。失败片段/未知文件与历史备份保留。[验收记录](media-retention-deployment-2026-10-12.md)。

## 2026-10-12 媒体硬链接存储去重已部署

TS-126 Platform `79ee0fd` 已推送并部署，保留供应商多模型a53993c。订阅/收藏夹独立目录的视频通过同卷内容池硬链接共享，NFO和封面独立；两份现有真实成品转换后同inode/nlink3，分配块由3.21MiB降至1.61MiB，12文件哈希及非视频inode保持。Emby双目录识别与两条HTTP206播放读取通过，无额外控制区条目；下载缓存generation1和任务不变。最终Linux55项全通过，37库冷备恢复、五核心TLS、十服务/guard、Dockge及59网页资源通过。volume2下载缓存仍独立保留，未清理历史成品。详见 [验收记录](media-hardlink-deployment-2026-10-12.md)。

## 2026-10-12 B站双来源与 Emby 真实链路已验收

Platform `169effa` 已推送并部署。使用现有B站账号和Emby连接，同一BV/P1/画质64经UP主和收藏夹两个真实订阅进入独立中文目录：一项下载、另一项复用，缓存generation1，两任务completed且Emby verified。12个文件实读哈希匹配；两套标题/作者/封面/分P识别、两条播放接口HTTP206读取通过。修复收藏夹可见分页、yt-dlp实际画质证据、ffmpeg定位/未合并片段和Emby同ID不同路径匹配；最终Linux56项通过。36库冷备恢复、五核心TLS、guard与Dockge通过，原配置及其余九容器保留。5个临时测试订阅均暂停，成品与3项早期失败记录保留。详见 [真实验收记录](media-live-validation-2026-10-12.md)。

## 2026-10-12 订阅独立目录与全局下载复用已部署

Platform `a339df6` 已快进推送 GitHub main 并部署 NAS。订阅/收藏夹各自设置整理子目录，按 BV/CID/实际画质全局复用有效下载，独立目录分别生成 NFO 和发布回执；并发只下载一次。媒体 schema 2 和共享媒体冷备适配已接通，原 Emby 连接、账号与 `/media/Bilibili` 挂载保留。Linux 镜像43项、恢复工具Linux17项、网页桌面/手机72项及补充10项通过；35库冷备恢复、五核心TLS就绪、十容器/guard及59网页资源核验通过，Dockge同步。未执行真实B站视频下载或Emby扫库。[部署记录](media-global-reuse-deployment-2026-10-12.md)。

## 2026-10-12 本地与 NAS 媒体存储本地交付

用户确认首批仅本地/NAS 已挂载目录。Platform `9667373c1800f7cb75b4f300efde2619e89c89ad` 已在隔离检出集成存储/目录网页管理、本地发布恢复与仅监控订阅；复用媒体队列及 NFO，不要求另接 AssetLibrary。后端31项、前端76项及最终相关增量20项通过；固定集成实际 HTTP/临时文件系统联合2/2通过。**尚未推送或部署**，NAS/B站真实账号与 Emby 读取未实机验收。基线包含未部署日程候选，发布须与另线最新产品协调。见[交付记录](media-storage-delivery-2026-10-12.md)。根原修改不动；下方生产记录为各自当时状态。

## 2026-10-11 自主日程完整链路本地候选

Companion `95894dad`、Platform `be98a630` 在隔离检出完成日程/活动/原件读取/日记草稿和网页控制链路修复，新增独立 `life-autonomy/v1`，旧合同未改。正常八阶段及次日、重启、公平执行、原件→认识→检查点→日记→发布授权读取已通过隔离真实 TLS 联验，模型上游为合成。完整组件793通过、16跳过、1项确定基线夹具失败；最终素材与权限定向另验证。**尚未推送或部署，不等于线上全天实测**；生产仍如下条。精确候选、验证和边界见[交付记录](life-autonomy-delivery-2026-10-11.md)。根原历史修改保持不动。

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
