# 天枢可选 PostgreSQL / Redis：数据归属与接入方案

日期：2026-10-20（北京时间）。状态：**本地代码已实现并集成产品候选，真实 PG 已验证；真实 Redis / 容器与 NAS 尚未验收或部署**。最初的数据归属方案保留如下，当前精确提交、实测与剩余边界见[本地交付](storage-backends-delivery-2026-10-20.md)及[机器记录](storage-backends-delivery-2026-10-20.json)。

本轮按用户最新要求先确定数据归属。默认继续 SQLite；PostgreSQL（PG）作为可选业务主库，Redis（RS）作为可选缓存。填写容器环境变量或配置文件后，在启动时自动校验、迁移和选择后端。媒体和原件保存在文件库，PG/Redis 不承接整份媒体文件。

本方案覆盖 Platform、Companion、Memory、独立 Knowledge 和 Model Gateway 的自有业务存储。它们目前位于四个独立代码仓库。独立 AssetLibrary、Chat Audit Core 继续按原接口提供文件/聊天归档，不在这次改造中迁移其数据库或接管数据。Loki、Prometheus、Vector、Grafana 保持各自原生存储；容量守护和日志完整性小账本保持独立的本地 SQLite，避免数据库故障同时破坏守护证据。

## 1. 三类存储各自负责什么

| 存储 | 保存内容 | 生命周期与权威性 |
| --- | --- | --- |
| SQLite / PostgreSQL 二选一的业务数据库 | 配置项、人物与角色记录、记忆、关系、可编辑文字、版本、授权、任务/回执、文件索引和检索索引 | 持久业务事实；同一模块始终只有一个当前写入后端 |
| 现有文件库及明确的模块文件目录 | 视频、音乐、书籍、漫画、图片、音频、资料原件、完整大正文、下载暂存、插件包、原始日志和备份 | 保留文件交付方式、已有路径/挂载/硬链接语义；数据库通过文件引用关联 |
| Redis | 已有的可重建热点缓存，后续按实测扩展 | 有有效期，可整批失去后回源重建；业务事实不依赖 Redis 恢复 |

“配置进数据库”指网页/API 修改的结构化配置项。容器启动 JSON/YAML、Compose、环境变量引用、证书、密钥和独立恢复检查点仍是文件或既有秘密存储，服务不应为了找到数据库密码而先连接数据库。

保留现有权限、账号和产品归属。PG 的 schema/用户隔离只负责数据库访问边界，不能代替当前业务授权；其他产品仍通过既有服务接口读取。

## 2. 应进入业务数据库的数据

下表对默认 SQLite 和可选 PG 同样适用，不为 PG 再建一套业务规则。

| 模块 | 数据库内保存 | 文件库内保存 / 不重复保存 |
| --- | --- | --- |
| 平台基础与管理 | 网页配置项、模型/提供商目录及版本、功能绑定、连接元数据、凭据引用或既有加密记录、角色部署配置、权限与撤销状态、接入来源/映射、幂等记录、任务投影 | 启动配置、TLS/主加密密钥沿用既有文件；不复制 Memory 的人物关系权威账本 |
| 视频/音乐/书籍/漫画 | 订阅、来源、目录元数据、下载/发布任务、状态、进度、重试、预算/租约、发布记录、播放/阅读进度、文件路径/ID、大小、哈希和整理结果 | 视频/音频/EPUB/CBZ/图片、NFO、交付歌词、下载分片、完整下载章节正文 |
| 记忆 | 人物与账号关联、画像原子项、稳定事实、关系/好感、冻结衰减状态、来源和版本、抑制/遗忘/消费记录、owner 水位、冲突、检索结构 | 原始媒体和资料文件不附在记忆 JSON/BLOB 中；原始聊天长期归档仍属于 Chat Audit |
| 资料库 Knowledge | 项目/资料登记、文档版本、来源、访问范围、文件哈希/MIME/大小、可检索片段、引用坐标、研究笔记/决策、入库任务 | 原始 PDF/DOCX/HTML/图片/音视频/附件、整份抽取全文、上传暂存内容放资料文件目录 |
| 陪伴与角色生活 | 人格版本、世界/房间/生活状态、日程、日记及修订、角色原创作品正文及版本、会话恢复必需的文本记录、轮次、短期上下文事实、outbox、发送/对账状态、文件引用 | 图片/音视频/附件和大体积模型素材快照为文件；不再在发送队列中持久保存媒体 base64 |
| 渠道适配器 | NoneBot Journal/RPC 的入站/出站去重、绑定、请求、事件、投递/观察回执与恢复游标 | 保留渠道端自己的本地存储默认；该服务可单独配置 PG，不强制全部渠道联网依赖中央 PG。渠道凭据仍按秘密管理，不写 Redis |
| 网关 | 请求/轮次身份、版本摘要、撤销记录、执行与终态回执、用量/延迟和准入统计 | 延续现有不保存模型消息正文、工具参数、凭据值的边界 |

文字内容按用途区分：用户需要直接编辑、修订、查询的日记、记忆、研究笔记和原创章节属于数据库业务记录；下载整本书的章节原文、上传文件及完整抽取全文属于文件内容。不能仅因为都是字符串就采用相同存储方式。文件化不新增业务大小门槛，不缩减原有导入、编辑、阅读和发送能力。

现有 FTS5、JSON 查询、触发器、CAS、唯一约束、幂等和恢复水位，都需要对应 PG 的真实实现和联合验证。接入 PG 不改变“关系不授予实际权限”等业务语义。

## 3. 已发现需要先从数据库拆出的大内容

这些是源码中已确认的当前存储方式，不能把现有 SQLite 表整表搬到 PG 后就宣布符合用户要求。

| 现有位置 | 当前内容 | 改造目标 |
| --- | --- | --- |
| Platform `media.documents` 的 `book_chapter.content` | 下载章节完整正文，单章允许到 2 MiB | 章节正文文件化；SQL 保留章节 ID、顺序/版本、正文哈希、字节数和文件引用 |
| Platform `book_audio_job.text` | 听书任务复制的正文快照 | 保存不可变输入文件引用和文本哈希；语音/合并音频仍在现有听书目录 |
| Platform `.bots.sqlite` 的 `replies.request` | 已物化图片/音频/视频的 base64 发送请求 | 文件库保留不可变载荷，SQL 留请求元数据、引用、内容/请求摘要与发送对账状态；发送边界临时物化 |
| Companion `replies.body.v2_request` | 含媒体 base64 的完整发送请求 | 同上，保留原请求可重建能力与摘要；不改变对外发送合同，不重发结果未知的请求 |
| Memory / Knowledge `knowledge_versions.raw`、`knowledge_content_uploads.raw` | 文档原始字节、上传暂存 BLOB | 原件和上传暂存文件化，SQL 保存上传状态、版本、哈希、来源与文件引用 |
| Knowledge `knowledge_versions.text` 及可能很大的完整语义块 | 整份抽取全文，部分片段实际可到 MiB 级 | 全文文件化；SQL 只留有界检索片段和坐标。完整依赖组/证据边界继续保留，可通过文件坐标重建 |
| Companion 创作任务的完整素材快照 | 大体积模型输入材料 | 不可变素材文件化；创作正文和人工修订仍保留 SQL TEXT |
| Platform 歌词修复 `pending.writes[].content` | 写文件前的恢复正文，终态已移除 | 恢复载荷改为不可变暂存文件；SQL 留前后哈希、目标引用和恢复状态 |

数据库仍可使用 JSON/JSONB 保存结构化小文档。目标是清除无边界的大对象和整份文件复制，不机械拆散每一段短文字，也不为了凑“小表”破坏原有业务内聚。

### 文件引用与一致性

- 复用现有媒体目标库、暂存、封面/预览目录和挂载。原来没有文件目录的资料原件/恢复载荷，在所属模块的数据/文件根下设明确子目录；不集中迁到一个新的全局目录。
- 新引用保存稳定文件 ID 或库内相对位置、SHA256、字节数、MIME、版本/来源。授权来自所属业务记录，知道哈希不等于取得文件访问权。
- 先写同卷临时文件，完成持久化并原子改名，再提交 SQL 引用。SQL 提交失败留下的文件可在核对引用后清理；SQL 不提交指向未落盘文件的 ready 记录。
- 听书输入、消息媒体、待完成歌词写入等恢复材料保持不可变。只有任务结算及既有保留条件满足后才能清理，不能随 Redis TTL 或普通缓存回收一起删除。
- 迁移既有内联内容时，逐对象验证新文件哈希和读取等价后再替换引用。未完成的旧记录在迁移期间仍可正常读取，完成本批迁移后移除临时兼容路径。
- 复用 Platform 已有内容候选文件的哈希命名、临时写入、fsync、rename 和目录同步模式，以及 Companion 已有 `image-originals` 的原件保存能力；共享文件辅助代码只负责落盘与完整性，各领域仍拥有保留/删除规则。

## 4. 继续保存在文件库的内容与位置

| 文件类别 | 位置规则 |
| --- | --- |
| 视频、音乐、书籍 EPUB、漫画 CBZ、听书音频及交付 sidecar | 保留当前用户配置的默认成品库，原路径和硬链接关系保持 |
| 下载分片、暂存、媒体输入 | 保留 `/volume1/Download/Tianshu/staging` 及现有容器内部路径 |
| 资料原件、下载章节正文、可靠发送载荷、创作素材快照 | 所属模块的明确文件目录；随其引用、保留和备份策略管理，不塞进 PG/Redis |
| 封面、头像、预览和真正的小热点文件缓存 | 维持现有 SSD 路径；即时内存生成的缩图/帧不强行新增永久文件副本 |
| 原始运行日志、Loki 块、Vector 持久缓冲 | 保留已分层的机械盘目录及原生格式 |
| 漫画扩展安装包、历史媒体维护材料 | 保留现有机械盘缓存目录 |
| 启动配置、证书、秘密引用和恢复控制文件 | 保留现有私有配置/状态目录和权限 |
| SQL 冷备、文件清单、发布与恢复材料 | 继续按既有备份根 `/volume1/tianshu-v2-resident-updates` 管理 |

文件目录不能再隐式依赖“数据库字符串一定是本地文件路径”。保留 `database_path` 作为 SQLite 默认及本地状态路径锚点；PG URL 使用独立字段，既有 `Path(database_path)` 派生出的暂存/头像等路径需在本次接入时明确归属，避免切换后改动文件库位置。

## 5. Redis 的首批范围

首批复用当前已有的三类缓存，保持原新鲜度和业务规则；其他服务可识别 Redis 配置，但没有合适缓存对象时不必为了“已接入”额外写数据。

| 缓存 | 缓存内容 | 有效期建议 | 键维度与失效 |
| --- | --- | --- | --- |
| 音乐公开元数据查询/补全 | 小型歌曲、专辑、歌手等派生元数据 JSON | 沿用现有普通查询 3600 秒、补全 86400 秒；强刷绕过缓存 | 部署空间、服务、提供商/账号与 revision、请求方法/参数摘要；凭据关联只取摘要。账号/来源版本变化立即换键 |
| 天气 | 标准化天气查询结果及短失败状态 | 成功共享副本新鲜期 300 秒、失败退避 60 秒；原本地 last-good 保留语义不变 | 位置/坐标与供应商、配置 revision、语言/单位及请求参数摘要；保持请求前后实时访问校验，修改配置后不复用旧结果 |
| 媒体存储分类统计 | 已扫描得到的大小、分类计数、扫描时刻 | 沿用 300 秒 | 节点、实际卷/挂载身份、文件根与媒体配置范围摘要；配置变化失效，手动刷新走现有强刷；界面保留采集时间 |

统一键采用 `tianshu:<部署空间>:<后端/恢复代次>:<服务>:<用途>:<格式版本>:<范围摘要>:<来源版本>:<参数摘要>`。切换 SQL 后端或恢复数据时推进缓存代次，避免命中另一数据源的旧投影。键不写入原文、位置明文、密码、Cookie 或 token。缓存值只保存该用途所需的小型结果，不复制整份文件或模型上下文。账户歌单名/说明等仍属于账户派生数据，按所属服务和账号范围使用，不把它们当全局公开缓存。

天气 Redis 只接共享的新鲜查询副本和短重试状态，继续保留当前进程的 last-good/stale 回退，不把 300 秒过期误当成删除全部最后成功观测的条件。返回给角色的授权/位置绑定仍在每次请求中组装。磁盘分类缓存不包括容量守护结论；当前可用空间仍现场读取。

共享缓存保存 UTC `fetched_at/expires_at`，不跨进程序列化 `monotonic()`；`SET` 与 TTL 同时完成，按原结果剩余寿命设置，不因命中或跨层回填续命。保持现有强刷行为和本地 single-flight；Pub/Sub 如用于辅助失效，不承担可靠撤权、恢复或调度。

写后失效和版本换键在提交业务事实之后执行。Redis 失效失败时，该实例绕过相关缓存直到恢复；旧键只能在原有效期内存在，不能把旧缓存当成已经更新的业务状态。不可变版本键优先用于需要严格避免旧值的对象。

### 后续可选项，按实测再接入

| 项目 | 可缓存内容 | 使用条件 |
| --- | --- | --- |
| Memory 派生 embedding 向量 | 已有向量缓存的共享副本 | 仍先筛选当前授权候选，键包含 embedding 空间/模型版本、内容摘要及必要范围；不缓存授权后的最终召回结果，保留来源/范围二次核验。有效期和内存上限按命中率测量确定 |
| 网页汇总 | 有界任务数量、汇总统计等派生投影 | 只在确认查询成本后增加短 TTL；请求身份/范围与数据 revision 进入键，任务详情和操作前检查仍读 SQL |
| 提供商模型目录 | 供应商返回的模型 ID 列表 | 可在后续采用约 300 秒 TTL；连接/协议/revision 进入键，连接测试始终绕过，列表存在不代表实际模型能力已通过 |

不将现有所有进程内缓存一律替换为 Redis。本轮不引入 Redis 作为可靠任务队列、跨进程锁中心或全局模型槽位调度器。

### 不进入 Redis 的权威数据

- 账号、角色配置的唯一副本、人物关系/好感账本、长期记忆、日记/作品版本、原始聊天、知识原件和媒体文件。
- 权限/授权结论、来源核验结果的跨检查点复用、撤销状态、suppression/消费水位、source-guard 检查点。
- 订阅/下载任务的唯一状态、outbox、发送 pending/claimed/unknown 回执、幂等唯一凭证和恢复载荷。
- 音乐小时/日预算、冷却、owner 租约等当前与媒体账本同事务的规则。
- 完整聊天回复、完整提示词、API Key、Cookie、token、TLS 私钥和解密后的账户密码。

Companion 的短期上下文仍由持久轮次/来源/回复重建。网关已有配置缓存、CurrentCache、同检查点的 VerificationCheckpoint 保持现有期限与撤销/来源校验，不用 Redis 延长有效期或跨轮次复用。

Redis 首批按可丢弃缓存管理。新建专用实例可从 `maxmemory=256mb`、容器上限约 512 MiB、`allkeys-lru` 起步，所有业务缓存键均设置 TTL；数值为可调整初值，不是门槛。无需依赖 Redis 的 RDB/AOF 恢复业务。若使用已有共享 Redis，只使用天枢自己的键前缀，不修改其全局设置，不执行 `FLUSHDB`/`FLUSHALL`。

## 6. 配置和自动选择行为

以下统一字段已在本地候选实现。四个代码仓库共用同一份固定共享包；产品安装与私有配置说明见各产品存储文档及 TS-236 部署候选。

```json
{
  "database_path": "/原有本地数据目录/service.sqlite",
  "storage_backends": {
    "postgresql_url": "",
    "redis_url": "",
    "namespace": "tianshu"
  }
}
```

容器可填写 `TIANSHU_POSTGRES_URL`、`TIANSHU_REDIS_URL`、`TIANSHU_STORAGE_NAMESPACE`；非空环境变量优先于配置文件。支持 PostgreSQL URL 及 `redis://` / `rediss://`。不要求额外的启用开关、确认弹窗或先在网页执行一次操作。

| PG 配置 | Redis 配置 | 初次启动的结果 |
| --- | --- | --- |
| 未填写 | 未填写 | 原 SQLite + 现有本地缓存，文件库不变 |
| 校验通过且数据接续完成 | 未填写或校验失败 | PG + 现有本地缓存 |
| 未填写或初次校验失败 | 校验通过 | SQLite + 白名单 Redis 缓存 |
| 两者均通过且 PG 数据接续完成 | 通过 | PG + 白名单 Redis 缓存 |

PG 测试包含连接认证、目标 schema 和事务写读回滚权限；Redis 测试包含 PING 及天枢测试键的写入、读取、TTL、删除。测试不打印完整 URL 或密码，也不通过一条 `SELECT 1` 就宣告业务 schema 已可用。

首次 PG 配置不可用且尚未产生 PG 写入时，继续原 SQLite 并明确报告未启用原因。连接成功后，空目标自动初始化；有本地数据时自动执行一次受控迁移和校验，再持久记录当前后端。目标已有其他部署的数据时不覆盖，继续给出明确的冲突状态。

**PG 激活后不可因断线、移除配置或迁移失败而自动回写旧 SQLite。** 按当前后端报告不可用并自动尝试恢复 PG；回到 SQLite 需要从当前 PG 完成一致性导出和校验，防止分叉。Redis 中断则旁路缓存、回源当前 SQL/服务，恢复后自动重新填充。

## 7. PG 组织、迁移和备份

1. 可共用一台 PG 实例/一个 `tianshu` database；按 Platform、Companion、Memory、Knowledge、Gateway 的现有部署边界分 schema/用户。各逻辑库可能都有 `metadata/receipts/audit` 等同名表，按部署服务与逻辑库映射到明确 schema，避免仅拼接到一个 schema 造成冲突；不让服务直接写别人的表。
2. 公共包只拥有配置解析、连接/池、后端选择、事务/异常接口、连接测试和脱敏状态。业务 SQL 与迁移继续在原 repository/领域模块中维护。构建固定共享包版本，显式管理连接池和关闭生命周期。
3. 接入顺序为“大对象文件化 → 原生 PG schema/查询/事务适配 → 隔离迁移演练 → 启动选择与真实后端验证 → 部署打包”。FTS5/PG 全文检索、JSON 类型、rowid/序列、触发器和并发 CAS 分别验证；不额外强制引入向量扩展。
4. 同一服务的主库和必要分库作为一次激活集合；迁移期间停止该服务写入，验证记录、版本、约束、文件引用、回执和恢复水位后再切换。中断时保留迁移进度和原 SQLite，不双写。
5. Memory 独立 source-guard 目前依赖 SQLite 写入串行化；PG 适配必须保留防旧恢复语义、单写/序号和文件检查点一致性，不能直接套用原 `BEGIN IMMEDIATE` 流程。Companion 的单 owner/单运行时约束也不会因为换 PG 自动解除。
   Platform 管理员 `.sealed` 的丢失检测、历史连接 revision 分库和各模块原加密密钥一起纳入清单。小型加密凭据 BLOB 是可保留的结构化秘密记录，并不等同媒体文件；保留原密文和原 key，缺 key 时不能生成新 key 掩盖问题。
6. 默认 SQLite 继续使用现有冷备方式。PG 使用一致性逻辑备份并演练恢复；同时保留角色/schema/版本信息、独立检查点、配置控制文件及文件引用清单。新增外置原件/恢复文件必须加入相应备份清单。
7. 外部媒体库仍沿用其独立备份边界，SQL 备份只包含元数据/引用，不会把视频或整本资料打进数据库。恢复验收同时核对 SQL 记录和实际文件的哈希/可读性。
8. 当前已有其他聊天占用 Q12 生产发布窗口。实施阶段另读最新提交、挂载和恢复代次，串行安排发布，保持现有守护与文件库；本方案不执行 NAS 切换。

## 8. 实施任务与验收范围

| 功能块 | 交付内容 | 主要验收 |
| --- | --- | --- |
| TS-230 数据归属规划 | 本方案及跨产品盘点；其余产品卡暂不派发 | 明确 SQL/文件/Redis 白名单和迁移前置项 |
| TS-235 共享存储基础 | 公共连接能力、统一配置/测试/状态、后端绑定与可选缓存接口、固定共享包版本 | 四种组合；无配置默认保持；初次失败、重启、激活后断线；配置不泄密 |
| TS-231 Platform | 主库及各功能分库的 PG 支持；下载章节/听书/发送载荷文件化；首批三类 Redis 缓存 | 现有媒体发布/阅读/听书和 pending/unknown 恢复不退化；文件路径/硬链接保持；缓存失效 |
| TS-232 Memory / Knowledge | 记忆/关系/来源/知识 PG；原件/全文/上传暂存文件化 | schema/FTS/权限/来源/抑制/消费/source-guard 及真实文件引用联合验证 |
| TS-233 Companion 与渠道适配器 | 人格/生活/创作/恢复状态 PG；大素材/发送媒体引用化；渠道账本可独立配置后端 | 修订/CAS、日记可见性、轮次/outbox、重启对账和短期上下文恢复；渠道断连恢复 |
| TS-234 Gateway | 配置/请求/撤销/回执/用量账本 PG；保留现有本地调度及缓存语义 | 请求身份隔离、终态不重放、配置撤销、统计和重启中断态 |
| TS-236 串行集成与部署打包 | 文件化和后端接线的完整组合、可选 PG/Redis 容器或外部地址、备份适配和升级/恢复演练 | 真实 PG/Redis；SQLite 回归；连接故障和缓存丢失；性能按同负载前后测量 |

验证沿用各产品既定方式，存储/迁移共享变化按产品 AGENTS 跑完整组件套件。真实 PG 的事务、约束、锁竞争与检索必须在真实 PG 上执行；SQLite、模拟 Redis 或单纯配置解析不能替代。性能只报告实测 SQL/锁等待、缓存命中率、检索和端到端耗时，不预先承诺提速比例。

实施依赖为 TS-230 规划 → TS-235 稳定共享接口 → TS-231/232/233/234 分仓并行 → TS-236 串行集成。各产品独立任务分支/worktree，产品协调检出和根任务板由协调者维护；共享接口稳定后再分派产品写者，不恢复已完成历史卡。

## 9. 本轮证据与边界

本节记录最初规划阶段：当时只读盘点源码、现有部署文档和官方资料，没有读取真实数据库内容、凭据或消息，没有访问 NAS，没有创建 PG/Redis 生产容器，没有调用模型或发送 QQ。用户随后明确“按这个开始做”，同聊天 `gpt-6.1-sol / max` 子代理已完成分仓实现，当前状态以上方本地交付为准。

主要代码证据（路径相对主工作区）：

- `docs/architecture/tianshu-system-architecture-v2.md:105`：产品数据归属和 PG schema 边界。
- `docs/development/TS-215-tiered-storage.md:7`：NAS 冷热分层和现有成品库保持。
- `projects/tianshu-platform/services/platform/storage.py:25`：主业务库；各功能仍有独立 repository，不能只改此处。
- `projects/tianshu-platform/services/platform/media/books/catalog.py:572`：下载章节完整正文。
- `projects/tianshu-platform/services/platform/media/books/audio/ledger.py:32`：听书任务文字快照。
- `projects/tianshu-platform/services/platform/bot_delivery.py:368`：媒体发送载荷及持久化。
- `projects/tianshu-platform/services/platform/media/music/metadata_cache.py:17`、`projects/tianshu-platform/services/platform/media/music/providers.py:312`：音乐缓存及有效期。
- `projects/tianshu-platform/services/platform/web_weather.py:250`：天气配置失效、缓存和读后复核。
- `projects/tianshu-platform/services/platform/media/storage_breakdown.py:99`：存储分类配置范围和后台单次刷新。
- `projects/tianshu-platform/services/platform/media/import_metadata_files.py:119`：既有原子文件候选写入模式。
- `projects/tianshu-memory/src/tianshu_memory/knowledge_originals.py:29`：Knowledge 原件、全文和分块写入。
- `projects/tianshu-memory/src/tianshu_memory/source_recovery.py:15`、`:54`：独立来源检查点与提交顺序。
- `projects/tianshu-companion/src/tianshu_companion/delivery.py:109`：持久发送请求中的媒体。
- `projects/tianshu-companion/src/tianshu_companion/store.py:108`：单 owner 与数据库初始化。
- `projects/tianshu-companion/integrations/nonebot/tianshu_nonebot/journal.py:41`、`rpc.py:226`：渠道独立账本。
- `projects/tianshu-model-gateway/src/tianshu_gateway/diagnostics.py:1`、`:250`：网关账本和不持久消息正文的边界。
- `deploy/tianshu/configuration.py:323`：现有打包显式限制 SQLite，需要一起接入新后端。
- `deploy/observability/monitor.py:1`：独立日志哈希/完整性账本。

官方依据（2026-10-20 核对）：

- [PostgreSQL schemas](https://www.postgresql.org/docs/current/ddl-schemas.html)：命名空间与访问控制。
- [PostgreSQL 并发控制](https://www.postgresql.org/docs/current/mvcc-intro.html)：并发和隔离需结合事务设计。
- [pg_dump](https://www.postgresql.org/docs/current/app-pgdump.html)：逻辑备份范围和一致性。
- [Redis cache-aside](https://redis.io/docs/latest/develop/use-cases/cache-aside/)：回源、TTL 与写后失效。
- [Redis key eviction](https://redis.io/docs/latest/develop/reference/eviction/)：缓存内存上限和可驱逐策略。
- [Redis persistence](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)：RDB/AOF 的用途与限制。

以上源码定位保留规划时点。后续实现已经完成本地产品接入与部署候选，真实 PG / SQLite / 文件验收及旧失败均见交付记录；不能将本地结果称作 NAS 或真实 Redis 验收。
