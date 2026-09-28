# CONNECT-A 已有能力端口覆盖矩阵（2026-09-27 初审及固定提交补记）

## 审计范围与快照

原矩阵只读核对产品源码、现有 API、网页入口、部署记录及其验证边界；未访问 NAS、真实业务数据、凭据或设备。本文件末尾的“固定提交补记”更新后续实现和测试证据。本表中“源码有”不等于“生产已配置”，容器 healthy 也不等于业务闭环通过。

| 检出 | HEAD |
| --- | --- |
| Platform | `1d51d888ff77594a1f6fbb1c7e0d4fdf92285ff8` |
| Companion | `31677983798ba27b24d57925feab4774c2eec30f` |
| Memory | `9a3b2bed6aebff9f0677f2c62e979859769e0c9c` |
| Model Gateway | `e4f112f02d28a1ded134126feed33f15e003ec20` |
| AssetLibrary | `d7b43eca94136e90303cce585f2b96c80223f3af` |
| Chat Audit | `e6d8e2156cff45cb22e6e8b17f27b33acf3d64b4` |

生产快照取自 `docs/development/provider-deployment-2026-09-27.{md,json}`：本次 9 个运行容器包括 Platform、Companion、Memory、Gateway 四个产品服务及五个观测服务。产品镜像修订依次为上表前四项。产品镜像声明的应用端口是 Platform `8443`、Companion `8765`、Memory `8130`、Gateway `8443`；部署记录确认的用户入口是 `http://192.168.31.210:18446`，没有记录这四个内部端口各自的 NAS 映射。AssetLibrary NAS Compose 要求部署方明确指定 HTTPS 绑定地址和端口；Chat Audit 默认 `8000`。这两个独立产品不在本次 9 容器更新证据内，不能据此声称它们未安装或当前离线。

## 后端、HTTP、网页与部署覆盖

| 能力 / 所属产品 | 实际 HTTP / 应用端口 | 网页或客户端 | 部署配置与验证 | 缺口与边界 |
| --- | --- | --- | --- | --- |
| Platform 登录、对话壳与静态网页 | 同源 `/api/web/session`、`/login`、`/logout`、`/messages`、`/snapshot`、`/cancel`；Platform 容器 `8443`，外部入口 `18446` | Platform 已提供登录、会话、对话 UI | Platform 运行且健康；现场证据只确认入口显示登录页（`session_state=sign_in`） | 没有用户登录反馈；不能据此声称已完成已认证网页对话或 Memory 消息交付 |
| 模型供应商及模型路由 | Platform `/api/web/providers/*`、`/api/web/models/*`；内部配置快照 `/internal/v1/model-config/snapshot`；Gateway Chat Completions `/v1/chat/completions`；Gateway 容器 `8443` | 供应商与模型管理页面已存在 | NAS 已配置真实供应商及默认模型；真实供应商短回复通过；合成消息探针经过 Companion → Platform → Gateway → 真实供应商，确认非空回复、模型绑定和 succeeded 回执 | 全链消息、记忆与网页实际登录对话没有验收；Gateway 原生 `/v1/messages`、`/v1/embeddings` 明确不支持。模型用量的只读报告为本地 CLI，不是网页管理页 |
| Companion 对话引擎 | Companion 内部 `/internal/v1/conversation/{ingest,ingest-actors,cancel,web-snapshot,direct-command}`、`/internal/v1/capability/execute`；Platform 同源网页路由如上；Companion 容器 `8765` | Companion 对话页已存在 | Companion 容器健康；合成对话运行链通过 | 真实用户的 Core→Memory 消息读取、记忆处理、渠道投递未由本次部署探针证明；不能用替身通过替代真实三方链路 |
| 角色人格目录、版本、历史与管理 | Companion `/internal/v1/persona/manage`；Platform `/api/web/personas/{catalog,history,revision,compare}` | 人格与世界页面已存在 | 本地候选合同可运行；Candidate `persona-management/candidate-v1-r2` 尚未发布，生产模式拒绝 candidate | **生产阻断**：未发布合同及 producer 适配必须完成；真实 HTTPS/Platform/Chromium 联合仍 pending。不可显示为生产已连接，也不能用 rehearsal 结果替代 |
| 角色生活与已发布日记 | Companion 已有 `/internal/v1/life-read/{actors,snapshot,diaries,revision}`，只读；Companion 容器 `8765` | 基线网页只有本地程序生成的小屋；没有真实生活或日记读取页 | Companion 已部署；本次部署记录未证明 `life_readers`、剧情授权或真实只读链路已配置 | 后端只读能力存在但尚未接 Platform/Web。本轮 B API 文档提议 Platform `/api/web/life/{state,actors,snapshot,diaries,revision}`；须等待实现、真实授权与对端业务读验收。小屋几何和美术不代表生活引擎已连接 |
| Memory 对话、身份与画像上下文 | Memory `/internal/v1/identity/{resolve,register,link}`、`/internal/v1/memory/{select,revise,turn-commits}`、`/internal/v1/memory/profiles/select`、`/internal/v1/memory/source-sync/check`；Memory 容器 `8130` | 基线 Platform 无 Memory 浏览页；Companion 内部调用不是用户页面 | Memory 容器健康并运行原镜像；生产部署记录没有验证这些业务端口的调用 | `profiles/select` 是生成对话上下文的有界选择，不是人物/画像目录查询。`identity/link` 在产品运行说明中明确返回 503，证明流程未实现。现有服务 Bearer 不能被当作终端用户身份或同意 |
| Memory 用户浏览及明确写操作 | 已有 CLI `user-action` → `LocalUserApplication`；没有浏览器用户认证 HTTP 端口 | 基线没有人物、画像、共同记忆与修改确认 UI | CLI 的显式用户操作需本机私有身份凭据、配置和已迁移用户授权数据；NAS 部署证据未证明这些已配置 | **需 Memory 产品最小补缺**：增加受认证的终端用户 query/command HTTP adapter，复用 Memory 应用层/`LocalUserApplication`，不让 Platform 直读数据库。只提供有范围的分页人物/画像/记忆查询；命令复用 `confirm_revision`、`approve_profile`、`publish_profile`、`revoke_profile` 的显式确认规则。必须绑定已认证会话到稳定账号、actor 与精确范围；不接受请求体自报身份/范围，不以 Platform 服务身份代表用户，不提供通用 operation 代理。账号关联在证明流程实现前继续显示未实现 |
| Memory 项目知识、项目资料与研究笔记 | 独立 `knowledge_cli serve --client … --port …`；业务 `POST /local/v1/project-knowledge/action`，另有 `/health`。端口必须显式指定；文档中的 `18135` 只是示例，不是 NAS 现状 | 基线没有资料/研究/项目目录页面 | 服务运行时已有固定 client、逐操作权限、Bearer、严格 Host；`server_runtime.resolve_binding` 支持非 loopback 时强制 TLS 和显式 Host allowlist。生产部署清单没有该独立 knowledge 进程 | **部署缺口而非已确认的产品 API 缺口**：需要部署知识进程、经审查的私有绑定/证书、固定身份、项目白名单与逐操作权限、对应资料库 schema/migration 以及业务读取验收。其 HTTP allowlist 可查询资料和研究笔记，但不允许目录 scan/apply、导入、Git 观察或经验晋升。B 目标浏览器读取端口为 Platform `/api/web/knowledge/{state,query,documents,document,notes}`，仍需固定提交验收 |
| AssetLibrary 资产产品 | AssetLibrary Core 通过 HTTPS AssetLink；Platform 适配器使用已登记对端的 `POST /assetlink/v1/control`。NAS Compose 端口由 `ASSETLIBRARY_HTTPS_PORT` 显式指定；证据 Compose 的 `5080→8080` 只是隔离试用设置 | AssetLibrary 独立 Web、Windows、Android 客户端；Platform 另有只读资产浏览页 `/api/web/assets/{state,connection,libraries,browse,search,entry}` | AssetLibrary 是独立产品；本次 Tianshu 更新未包含其服务。Platform 资产页须服务端登记 AssetLink 身份、授权连接与只读范围；本次部署记录未证明已登记或实际读取 | 不直连 AssetLibrary 数据库/文件系统。Tianshu 资产页目前只读；资产发布、移动、下载等不能冒充已从 Platform 闭环。独立 AssetLibrary 自身也标明完整 Alpha 尚 blocked |
| Home Assistant 家庭设备 | Platform 容器 `8443`；对端调用 Home Assistant REST `/api/states/{entity_id}` 和 `/api/services/{domain}/{service}` | Platform `/api/web/home/{view,refresh,unlock,lock,control}` 及家庭设备页面存在 | 连接、实体和动作模板由 Platform 服务端部署配置登记；控制还需独立解锁。本次部署材料没有 HA 连接、授权或业务读数证据 | 这是可连接的外部服务，不是内置设备引擎。需管理员提供正式 endpoint、令牌、允许实体与动作模板后才可显示未配置/未授权/不可用的准确状态；没有真实设备验收前不能宣称控制已通过 |
| Platform 任务中心 | `/api/web/tasks/{view,detail}`；Platform 容器 `8443` | 设置页任务中心存在 | 运行时只从 Platform 自有模型发布记录和家庭控制记录投影实际任务 | 不包含项目开发、资源下载、Memory 写作等来源；相应任务合同与生产者未接入。未知状态不得推断为已完成 |
| 研究资料、订阅下载与媒体处理 | `services/platform/media` 有类型/规则/元数据等库内模块；没有已实现的网页业务 API/接入端口 | `resources` 的研究资料和订阅下载入口目前落到 `UnavailablePage`；只有资产子页为实现页面 | 没有这类功能进入本次 9 容器部署证据 | 本轮明确不扩建未实现的订阅/下载业务引擎。MoviePilot、下载器、阅读库等若后续接入，须按各自独立产品授权/配置；当前页面不应以“待配置”暗示这些引擎已存在 |
| 项目工作区、任务文档与交接 | Memory 项目知识 CLI/HTTP 有受限查询和接续能力；Platform 本身没有项目管理 HTTP API | `projects` 路由目前为 `UnavailablePage` | 知识 HTTP 进程未进入本次部署；无 Platform 项目配置/任务来源 | 不把仓库/文件路径或本地工作树误当远程产品能力。当前已有 Knowledge 查询只能覆盖 HTTP allowlist 中的只读操作；项目目录写操作与编码体任务未接入 |
| Chat Audit 独立产品 | 默认 HTTP `8000`：自有 `/api/auth/*`、`/api/rooms`、`/api/messages`、`/api/search`、管理/导入/审计 API；OneBot WebSocket `/onebot/v11/ws` | 自有 Web 控制台和采集器 | 单独产品、单独数据库和令牌；不在本次 Tianshu 9 容器部署材料内 | Memory 文档明确 Chat Audit、跨产品账号证明尚未接入；不得让 Memory/Platform 直接查询其数据库。接入需经正式来源权限/归档接口与对端授权，不应把独立产品显示成 Tianshu 已有内置能力 |

## 前端现状（改动前基线）

Platform `apps/web/src/app/App.tsx` 只把工作台、Companion 对话、人设、小屋、家庭设备、只读资产页和设置接到页面组件。Memory 全模块、项目、资料研究、订阅下载以及家庭容器/节点/身体子页都走统一 `UnavailablePage`。Settings 中的连接列表是接入条件说明，不是连接状态探测。任务页只展示 Platform 自有台账。小屋页面明确写明角色动画、行走、换装等没有实现；本审计将其作为既有小屋美术/展示范围，不纳入本轮实现。

本轮 B 的接口草案 `worktrees/CONNECT-B/tianshu-platform/docs/handoffs/CONNECT-B-API.md` 是其工作树里的目标契约，不代表对应端口已合并或生产可用。CONNECT-U 应以该绝对路径读取最终版本，并将每种连接状态区分为真实已读、已配置未验、未配置、未授权、不可用、空数据、未实现。

## 需要总控决策与最小补缺

1. **先完成已有闭环**：B/U 对 Memory Knowledge 与 Companion Life 只读接入 Platform，不触碰对端数据库；等固定提交后分别验网页到已授权对端的真实读数。没有登录/业务数据时只报告限制，不用 mock 成功覆盖。
2. **单独派发 Memory 用户 API**：Memory 产品需要受认证用户查询/命令入口；已新建 CONNECT-M 专项卡 `docs/development/connect-memory-scope-2026-09-27.md`（根提交 `2792108`）。必须复用现有 Store、Workflow 和 LocalUserApplication，保留明确确认、精确身份与可见范围、版本预期值、幂等与撤销审计，不增加第二套记忆业务逻辑。
3. **独立处理知识服务部署**：当前知识 HTTP 产品代码已有安全 TLS/Host 运行模式；由总控在后续受控部署任务中提供隔离配置、权限、证书和库 schema，再做真实业务读取验收。不能在当前产品运行容器里假称知识端口已在监听。
4. **人格管理发布阻断**：candidate-v1-r2 需由合同所有者发布并由 Companion producer 适配；生产 HTTPS/Platform/Chromium 联合通过后才可以取消页面生产阻断状态。
5. **保持外部边界**：AssetLibrary、Chat Audit、Home Assistant 以及未来 MoviePilot/下载器各自保留产品身份、数据库与授权。当前只读 AssetLink/HA 客户端有条件配置；没有配置和业务读数时不标作已接入。
6. **不将目标架构当交付**：`tianshu-capability-map.architecture.json` 与 V2 I01–I18 是目标/拟议合同，不是生产 API 目录；矩阵只列源码端口与实际部署证据。

## 后续复核状态

这份初审记录的是 CONNECT-B / CONNECT-U / CONNECT-M 固定提交前的源码与部署基线。以下补记反映固定实现和隔离验证；不改变生产部署状态。

## 固定提交补记（以本节为准，2026-09-27 至 28）

固定复核的主要版本：Platform B `c0f828a1fdf3015727997fb2fefbf165d9a3e6a3`（Persona SHA pin；业务联测基线 `9551871796f57d3369396023caf2b013a239c3a0`）、Memory M 浏览器/API `02df5ba8c041a7a6eb8b705c5f264e10543032ec`、Memory Knowledge HTTP 扩展 `bd0123bc7e242dc5a767347602f23957af9b2c33`、Companion 人格生产者 `31677983798ba27b24d57925feab4774c2eec30f`、Web U `565ad93b4e868b2b10a3bd441eec9de52e006da1`。

| 能力 | 固定实现与隔离验证 | 仍未证明 |
| --- | --- | --- |
| 人格目录、历史、修订和比较 | B `/api/web/personas/{catalog,history,revision,compare}` 绑定登记 HTTPS reader、固定主体名单和经过字节校验的 contract。CONNECT-A 用当前 Companion producer、B 955、U faa5c6 dist 做了 real Companion HTTPS + real Platform HTTPS + Chromium 联测：1/1 通过；浏览器读到允许的两个人格并呈现修订、四字段比较，未见名单外主体或上游凭据泄露。根 manifest 后由 `f705475` 正式发布，SHA-256 `72ae9ee2…13c0e2d`；B `c0f828a` 固定同一 SHA，正式包 `load_published` 定向测试 3/3 通过。细节见 `docs/reviews/connect-a-fixed-integrations-review-2026-09-27.md`。 | 之前 Chromium 联测用 test-only published package 副本；B 新测试证明正式包加载与篡改拒绝，不等于已对正式包做 Chromium 或部署端到端验收。 |
| Memory 本人记忆浏览 | B 的 overview 与 M `memory_group_count` / `counts_truncated` 形状已一致；真实 Memory HTTPS 进程和 Platform 登录/同源读路径测试 1/1 通过，覆盖非空 overview、subjects、records。后续真实 Chromium 页面也读取了本机集成服务。B 当前网页入口只读；无用户端网页命令/写入入口。 | `test_memory_joint` 使用真实 Platform HTTPS `/internal/v1/origins/resolve` endpoint（由同一 Platform 实例提供），但 issuer 凭据、身份/来源事实、授权数据、数据库与记录均为合成；不代表 NAS reader 配置或真实账号读取。该后端套件没有启动 Chromium。 |
| Memory 项目知识 | M Knowledge HTTP 服务新增四项逐操作 HTTP allowlist；B 固定项目与 checkout 闭集，`experience_query` 仍独立需要 review 授权，接续包只作为会话内有界句柄保存。真实 Memory Knowledge HTTPS CLI + Platform 路由测试 4/4 通过；U `565ad93` 的 Chromium 场景对真实本地 Platform/Knowledge HTTPS 服务读取 catalogue、lessons、notes 与 continuation。 | 这些 Chromium fixture 使用合成数据；独立 Knowledge 进程、私有 TLS/Host、正式项目/checkout 白名单和资料库迁移尚未部署验证。 |
| Companion 生活与日记 | B 固定 Companion HTTPS reader 的 actors、snapshot、diaries、revision 只读路由。B 记录的 `test_life_joint` 1/1 通过：真实 Companion HTTPS、Platform HTTP 登录、合成行；U `565ad93` 的真实 Chromium Life 场景也读取本机集成服务。 | 真实 `life_readers`、剧情授权、证书和日记未验；浏览器和后端联合数据仍为合成。 |
| AssetLink 与 Home Assistant 外部连接设置 | B 增加固定两类 peer 的管理员编辑/测试流程，外发限制到登记类型、固定路径和受限地址策略；secret/CA 加密，测试回执绑定配置版本。`test_web_external` 4/4 通过，对端是合成 TLS/HTTP 服务。 | 没有真实 AssetLink 或 HA 读取。Windows ACL 需要运维单独配置；代码里的 `chmod` 不构成 ACL 证据。U 连接修订竞争浏览器测试用 mock API。 |
| U 网页接口页面 | U 固定包 typecheck/build 通过；`integration-pages`、`scope-transitions`、`external-connections` 的桌面和移动 Chromium 项目合计 22 项通过。U `565ad93` 的独立浏览器运行对 Knowledge/Life 不拦截同源 API，读取本机真实集成服务。 | 原 22 项规格仍是 mock UI 证据；Knowledge/Life 与 Memory live Chromium fixture 均为合成数据。外部 AssetLink/HA 页面仍只有 mock API 规格；Persona live Chromium 使用 test-only published package 副本。 |

B 的更严格交接引用：`.runtime/connect-a-b-9551871/checkout/docs/handoffs/CONNECT-B.md` 及 `CONNECT-B-{API,KNOWLEDGE,LIFE,EXTERNAL}.md`。上述测试均为本机隔离 fixture，不会升级为 NAS 业务状态。独立 AssetLibrary 与 Chat Audit 仍须按各自服务/授权单独审查；此次没有检查真实服务。
