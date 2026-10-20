# PG / Redis 可选存储本地交付

本地代码已实现，Platform、Companion、Memory / Knowledge、Gateway 已串行合入各自 `projects/` 协调检出。默认仍为 SQLite；容器环境变量或配置填写并检测成功后，PG 与 Redis 可独立启用。媒体及原件继续保存在所属文件库，数据库只保留业务数据、元数据、索引和可靠文件引用。

**没有推送、部署、迁移生产数据或操作 NAS。真实 Redis 和 Docker 运行未验收，默认完整套件也有已记录的旧失败。** 这份交付是代码与本地 PG 验证，不是全系统生产验收。

## 已实现

- 统一 `storage_backends.postgresql_url / redis_url / namespace`，支持非空 `TIANSHU_POSTGRES_URL / TIANSHU_REDIS_URL / TIANSHU_STORAGE_NAMESPACE` 覆盖。保留原 `database_path` 等本地路径锚点。
- 原业务库与必要分库全部采用产品自有原生 PG schema / JSON / FTS / 事务适配；迁移逐表核对，保留版本、CAS、授权、来源检查点、发送及恢复状态。独立日志与容量守护 SQLite 保持原职责。
- 首次 PG 不可用可留在 SQLite。选择 PG 后断线、删配置、丢绑定或丢业务表均不能回写旧 SQLite，也不能建空表冒充恢复；旧镜像回退同样受当前 PG 绑定保护。
- 下载章节、TTS 输入、Knowledge 原件/全文/语义块/上传、可靠发送媒体 base64、大创作素材及待写歌词已按领域文件化。视频、音乐、EPUB、漫画、音频的路径、挂载及硬链接不移动。
- Redis 首批只接音乐元数据、天气和媒体分类缓存；保留原 TTL、刷新、来源版本隔离和实时授权。失联旁路回源，不承载权限、可靠队列或持久事实。
- 私有配置接入新包与 resident，另提供可选 PG / Redis 容器配置。原生冷备实际导出 PG 并恢复至新临时库，核对全行/序列与 guard，同时保存原件和控制文件；自定义外置 Knowledge 文件根也纳入副本。

## 精确候选

| 组件 | 最终提交 / 状态 |
| --- | --- |
| Platform TS-231 | `14c2d1a33579f0c8e7b41da6422b1b6115b2419e`，已本地快进 |
| Memory / Knowledge TS-232 | `f496c25ea79a9f47c4a53d4090294a5d2d0949e3`，已本地快进 |
| Companion / 渠道 TS-233 | `11b3761b43c164a5be87214abbc4458560ff9449`，已本地快进 |
| Gateway TS-234 | `d7336e013e0b194ed87717af5c288be89c18db41`，已本地快进 |
| 共享包 TS-235 | 源 `119ebd6`，根接入 `4ebbc00`；固定0.1.0 wheel |
| 部署及恢复 TS-236 | `0b22768b727d7d0ec5743de9f80bc2d50a7c2dc0`，`codex/ts-236-storage-integration`，独立完整候选 |

共享 wheel SHA256 为 `e7c8ef910a43a5e2d1f426948fe19e4ea3942e1ff0618a35ecfae03bfedc1482`。已逐份核对各产品与部署 **Git 原始 blob**，并从提交导出安装。共享包拥有连接/绑定/文件落盘，各原 repository 继续拥有业务规则，没有增加第二套业务数据库抽象。

根当前分支仍有其他批次混合修改，部署工具 HEAD 也早于当前已发布基础设施。TS236从 `0864ae8` 创建并保留完整部署候选，没有整批覆盖根混合文件。后续部署应使用上述候选分支，不能拿旧根工具替换发布中的守护/媒体代码。

配置说明位于本地候选 `worktrees/TS-236/workspace/deploy/tianshu/STORAGE.md`，原生备份与客户端要求为同检出 `ops/recovery/POSTGRES.md`；完整交接为 `docs/handoffs/TS-236.md`。各产品目录内还有对应 `docs/handoffs/TS-231/232/233/234.md` 与存储使用文档。

## 验证与限制

真实 PostgreSQL 16.15 使用本轮独立 loopback 合成库，现已正常停止，只保留本地证据。共享包24项通过；Memory最终专项28项通过（27真实PG），Platform专项109通过/2跳过，Companion和Gateway的原生PG/恢复通路均有实测，具体命令与重叠用例见各卡。协调合入后又独立通过Platform22项、Memory5项与Companion4个选例。

冷备最终26项通过（新增7项，其中6真实PG；原19项），覆盖实际dump/临时恢复、序列和全行摘要、当前guard、外置原件、禁用文件准备/缓存代次与旧备份保留。部署全目录208项通过，补隔离环境PyYAML后对应1项通过；剩1项旧 `parent_cold` AST夹具失败已在0864原树复现，另1项需额外固定产品环境而跳过。回退返修30项通过；协调者在最终源码上独立重跑存储接线15项、仓库外恢复输入1项通过。

产品完整默认回归并非全绿：Platform1457通过/20失败/77错误/290跳过，Memory1442通过/6失败/21跳过，Companion978通过/3失败/18跳过；各失败族的合同/Windows/联合环境纠正、后续复跑和旧基线证据均保留在对应交接，Gateway也保留旧状态码失败与时序波动。未降低断言、改合同摘要或混入Q13/Q14来掩盖失败。

工作区 `scripts/workspace.py check` 在旧 TS-016 检出触发 Windows 所有者不同的 Git `dubious ownership` 拒绝，未完成全工作区检查；没有为本批修改全局 `safe.directory` 或修复无关旧检出。本批任务记录、产品提交和wheel对象已独立核对。

同负载本地SQL微测中，PG逐条提交吞吐更高，SQLite的小型索引查询更快。该微测不含NAS、模型、实际产品检索和真实Redis，不保证整体提速。原始三次测量为 `.runtime/storage-sql-timing-20261020.json`，数值见TS236交接。

真实Redis目前只有替身/模拟验证；下载独立测试程序曾被自动审批拒绝且未提供具体原因，没有绕过。隔离NAS测试容器仍等待独立授权或测试地址。没有Docker运行时，因此未构建/运行镜像、未验收NAS挂载网络/信号及真实产品恢复。PG客户端安装、恢复账户权限、当前配置与候选产品镜像需在正式发布时逐一确认；resident配置接线自身不会替所有产品升级镜像。

现有NAS版本、Gemini配置、自动记忆、1500ms收集、媒体库、Q13/Q14候选与其他服务均未由本批改动。机器记录见[交付JSON](storage-backends-delivery-2026-10-20.json)。
