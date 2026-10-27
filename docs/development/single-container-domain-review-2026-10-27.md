# 单容器整体整合：第一方功能领域静态覆盖（2026-10-27）

本记录是 TS-333 的领域底图：从导航、实际页面分派、HTTP/CLI入口追踪到业务owner、后台任务及持久化/外部依赖，为单容器装配和后续有依据的精简提供范围依据。它不是逐函数全源码审计、全功能运行验收、安全扫描或外部供应商兼容证明。没有读取业务数据/凭据、操作设备、执行生产迁移、发送QQ或调用模型。

## 范围与证据深度

本次静态读取的第一方产品基线：Platform `f4bbdb23795e0f9d89345aed98c09906fb24031f`；Companion `677fcb1f1392c134084d6e2be0d61f1a49f757b4`；Memory（含Knowledge）`110a41bf851163901f7fc4a8f13cc19817285a3a`；Gateway `5ff98947f7eaf82384d359ca84ad581a5cf113f0`。读取对应产品的入口及相关目录，没有扫描全部历史文档，也没有展开独立AssetLibrary/Chat Audit内部实现。未提交构建目录不当作发布源码。

导航基准为 [modules.ts](../../projects/tianshu-platform/apps/web/src/app/modules.ts)，但功能存在性以 [App.tsx](../../projects/tianshu-platform/apps/web/src/app/App.tsx) 实际页面分派、[WebConsole](../../projects/tianshu-platform/services/platform/web_console.py) 路由与各owner装配共同判断。每个领域检查了入口/归属/依赖；仅对与重复运行、资源、数据所有权有关的关键实现继续深入，未执行每个action或穷举错误分支。

## 领域覆盖表

| 领域 | 实际入口 → owner | 数据、外部依赖与装配边界 |
|---|---|---|
| 网页、账号、访问与连接设置 | Auth/App → WebConsole、WebAccount、WebAccess、WebExternal | 静态dist、会话/账号与连接登记、Platform存储；私有/公开listener共用一个console，后台只在非public app注册 |
| 工作台 | WorkbenchPage → GettingStarted | 准备状态可读；角色近况、设备/容器和近期事项卡当前明确显示未读取，没有把导航占位当成真实聚合实现 |
| 小屋 | RoomPage → useRoomLife/useLifeResource → Platform life读口 → Companion Life | 生活姿态/活动和穿搭引用；服装原件经Content读取Memory；canvas/GPU主要在浏览器，不是独立NAS业务服务 |
| 文字对话 | companion/0 → WebDialogue/WebSender → Core ingest/snapshot/cancel | Companion turn与outbox、Memory材料、Gateway模型、Platform实际发送；接纳/发送回执/unknown不能合成一个成功标记 |
| QQ、群模式、旁听与身份 | settings/3及人物/群页面 → BotAdapters/BotObservation/Participation/QQAdmin → OneBot/Core | NapCat/登记适配器为外部接入；Memory身份/资料权威；Platform有入站、旁听、别名pump和角色恢复，不能因同容器重复启动 |
| 人格、世界、角色与技能 | companion/2、/3及settings/8 → WebPersonas/WebPersonaAuthor/RoleRuntime/WebSkills → Companion Personas/skills | 人格版本、角色状态与技能授权各有owner；网页是管理与投影，不另建人格账本 |
| 自主生活、日记、活动、相册 | companion/1、小屋 → WebLifeManagement/WebReader → Companion life/read/runtime | Life、proactive后台及Companion存储；发布/私有阅读、计划/经历分开；Memory保存自身经历投影而非接管生活调度 |
| 长篇创作与生图 | Core role_actions/capability、Life内容管理 → Writing/Images/ImageBackend | 章节canon/draft/review与生成attempt、Gateway、ComfyUI/图像供应商、原件引用；存在后台实现，不因此宣称每项都有独立完整网页入口 |
| 人物、群画像、账号、关系与记忆 | memory四分区 → WebMemory/MemoryLinks/GroupMemory/relationships → Memory identity/profiles/context/relationships/actor_memory | 个人与actor数据域、关系账本与实际权限保持分离；Chat是已登记历史peer，不在本轮接管其内部数据库 |
| 资料、原件与阅读 | resources/0及projects/0共用KnowledgePage；WebContent/Knowledge读口 → KnowledgeContent/KnowledgeApplication | PG元数据、文件原件/版本、读取记录、来源证明与已登记来源；同属Memory产品，项目资料与人物经历仍有不同责任 |
| 项目、研究笔记、经验与交接 | projects/1 → ExperiencePage/WebReader；Knowledge action/CLI/MCP → KnowledgeApplication | lessons/notes、目录、checkout continuation/workdir及文档目录；复用KnowledgeStorage租约，当前授权/来源检查不作长期结果缓存 |
| 资产接入 | resources/1 → WebAssets → AssetLibrary协议 | 只读/发布接入按现登记协议；独立产品数据、查重和内部运行不纳入本轮审计 |
| 视频、链接下载、下载中心与媒体库 | subscriptions/0、1、2、3、4、6、7 → WebMedia → Media/Subscriptions/Worker/Engine/Repository | B站、yt-dlp/ffmpeg、NAS媒体文件、发布合同与AssetLibrary/登记媒体库；保留可恢复job和发布receipt |
| 音乐 | subscriptions/5 → WebMusic → Music/Library/RequestControl/Imports/Navidrome/Lyrics/Maintenance | 网易云、QQ/酷狗桥、解码工具、NAS、Navidrome；已经复用Media repository、租约和共享cache，并非独立音乐服务 |
| 书籍与阅读 | subscriptions/8 → WebBooks → Books/Catalog/RuleEngine/Sources/Reading/Downloads | LegadoParser/QuickJS、网络来源、本地正文、媒体job与阅读进度；与人物记忆材料存储不混为一套数据库语义 |
| 听书 | books/audio子路由 → BookAudio | 串行worker、分段音频缓存/预算/receipt、edge/fish/local TTS；这是语音合成能力，不是Gateway文本生成的重复接口 |
| 漫画 | subscriptions/9 → WebComics → Comics/Sources/Imports/Extensions/Downloads | Suwayomi/Komga、原件/成品目录及UID助手；单容器插件化应复用现协议和权限/receipt，不重写漫画引擎 |
| 家庭设备 | home/0、settings/5 → Home/WebExternal → Home Assistant | 实体观测、模板控制和持久control ledger；设备控制权限与SSH容器控制权限仍独立 |
| 基础设施、容器与节点 | home/1、2、settings/11 → WebInfrastructure/Infrastructure → Executor | ops执行器子进程、SSH Linux/NAS/OpenWrt、容器清单/指标/日志与控制receipt；观测与控制按现owner运行，不复制设备执行器 |
| 身体与活动 | home/3 | App实际落UnavailablePage，不能根据导航名称声称已有可装配的完整后端 |
| 插件 | plugins → WebPlugins/PluginManager → Packages/Vault/Executions/Scheduler | 包、密钥域、实例/操作repository及每实例Python子进程；复用当前包/实例协议接入漫画，而非另造第二套插件宿主 |
| 任务中心 | settings/0 → Tasks | 已是模型、家庭、媒体、基础设施及插件现有ledger的只读投影，不应再建“统一任务数据库”制造第二权威 |
| 模型、检索、天气 | settings模型/记忆检索/天气页 → ProviderManagement/MemoryRetrieval/WebWeather → Gateway、Memory策略、天气peer | Gateway chat/native/receipt/cancel/usage及embedding/rerank；已有跨协议共同scheduler与单session；天气及TTS不强并入模型协议 |
| 观测、维护与CLI | 四产品正式CLI/health；Vector → guard → Loki；Prometheus → Grafana | 单镜像/容器仍承载成熟多进程；业务配置、原件、账本与各观测数据目录需完整纳入备份；CLI迁移/重建不当作每次启动动作 |

## 已存在的复用与应保留的边界

- Platform [service.py](../../projects/tianshu-platform/services/platform/service.py) 已在单owner中装配Media、Infrastructure、PluginManager；[server.py](../../projects/tianshu-platform/services/platform/server.py) 只在`not public`注册后台，并非每个listener各跑一套。研究资料与项目工作区复用KnowledgePage；Tasks复用业务ledger。这些位置不应再新增平行聚合系统。
- Memory [app.py](../../projects/tianshu-memory/src/tianshu_memory/app.py)、[knowledge.py](../../projects/tianshu-memory/src/tianshu_memory/knowledge.py) 和 [knowledge_storage.py](../../projects/tianshu-memory/src/tianshu_memory/knowledge_storage.py) 体现了共同产品内不同领域及有租约的存储owner。可合运行入口，但不意味着人物事实、项目文档、原件和actor记忆变成一个无范围的读写接口。
- Companion [app.py](../../projects/tianshu-companion/src/tianshu_companion/app.py) 的Knowledge客户端已有明确说明：来源回调可能重新进入同一Memory origin，因此需要独立admission。单容器/合运行时不能把该客户端和外层请求不加区分地合进同一槽位，重引入回调自等待。
- Gateway [server.py](../../projects/tianshu-model-gateway/src/tianshu_gateway/server.py) 已把chat/native放入同一供应商调度与连接池。统一部署不应再包一层会复制配额的通用调度器；同时保留各服务/身份独立授权、回执及恢复状态。
- 下载、图像、TTS和插件需要的成熟二进制/子进程是运行实现，不因容器收口就必须改为同一个解释器或同一种协议。AssetLibrary、Chat Audit和外部设备/供应商仍是协议边界，不能把本轮静态接入检查升格为这些系统的完整验收。

## 有具体成本证据的后续动作

以下是本次追踪实际发现的候选，不是数量门槛或默认实施授权，也没有未经量测的节省百分比。

1. **统一镜像的依赖闭包。** 四基线pyproject中，Gateway钉`tianshu-storage==0.1.0`，Platform/Companion/Memory使用`0.1.3`。共同环境需明确解决版本一致性并验证Gateway调用契约；不能依赖安装顺序覆盖，也不能把分容器旧依赖直接拼成可解析的单环境。
2. **复用有生命周期的HTTP连接。** Platform [transport.py](../../projects/tianshu-platform/services/platform/transport.py) 的核心/管理调用、[web_life_management.py](../../projects/tianshu-platform/services/platform/web_life_management.py)、[web_readers.py](../../projects/tianshu-platform/services/platform/web_readers.py) 多次请求各建SSLContext和ClientSession；Home每次观测也重新建客户端。Companion JsonService、Gateway和Media.Remote已有长期连接池范式。可在对应owner按origin/CA/登记revision复用连接并随生命周期关闭，继续逐请求取当前授权/凭据，不缓存授权结论或重复自动发送写命令。先量测握手/连接数与首包时间，再承诺收益。
3. **减少空闲轮询，沿现工作索引和wake/due演进。** Companion [app.py](../../projects/tianshu-companion/src/tianshu_companion/app.py) 固定50ms调用[Core.tick](../../projects/tianshu-companion/src/tianshu_companion/core.py)，空闲也开启事务读取工作索引；Media [scheduling.py](../../projects/tianshu-platform/services/platform/media/scheduling.py) 每10秒枚举整类订阅。可复用现work_index、wake和到期时间，将队列变更/最近due作为唤醒依据。必须保留跨群公平性、租期/重试与自然到期语义，不以停用后台功能或拉长所有轮询来冒充优化；当前只确认静态查询成本，未做运行profile。
4. **消除漫画列表的重复线性关联。** [Comics.view](../../projects/tianshu-platform/services/platform/media/comics/runtime.py) 对每个订阅`next(...)`扫描全漫画列表，形成`订阅数×漫画数`关联；[Books.view](../../projects/tianshu-platform/services/platform/media/books/runtime.py) 已用ID→条目字典。可在漫画owner就近构造一次lookup，保持原缺项/排序/计数行为；这是真实可见的局部重复计算，无需建万能媒体仓库或改数据库模式。

日志账本的重复扫描和摘要占用已在TS-331单独形成可量测实现及交接，见 [TS-331](../handoffs/TS-331.md)。其合成SQLite收益不代表Loki/Grafana或全容器总内存已下降；单容器发布、真实工具和数据恢复仍由统一集成/实机验收分别记录。

## 验证边界与下一步

本表覆盖导航入口及对应装配领域，不证明每个业务分支都实现完善。工作台聚合和身体活动的现状已明确标出；外部引擎、账户、真实QQ/模型、NAS文件权限/吞吐、单镜像依赖及完整备份恢复都需要各自实际验证。实施候选时沿原owner集中修改、进行受影响验证；已验证的未变内容复用结果，不把领域表变成每次提交的全量测试门禁。
