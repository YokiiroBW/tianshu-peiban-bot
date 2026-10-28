# 完整异步联动验证与 R13/R14 发布

本阶段承接[整体链路优化](chain-optimization-2026-10-28.md)。用户明确暂不测试真实 QQ 收发，网页登录稍后自行处理；其余陪伴、记忆、日程、音乐继续验证，配置和数据保留。下面分别记录真实 Linux 安装包的隔离联动与正式服务验收，二者不混称。

## 发现并修复的问题

Platform 原 `observation_processing.current` 在没有观察映射时按账号找唯一会话；同一机器人账号有两个正常会话就全部判不可处理，导致 Companion 自动记忆 outbox 停住。现在按已验证的原生会话 `{kind,id}` 精确选择，保留账号整体的 managed 判定及未知、重复、禁用、pending、角色不符拒绝。实际失败已先在原版本复现；新真实 HTTP、观察映射和角色回归通过。

Memory 的 PostgreSQL 静态初始化 schema 缺少 `turn_inputs.event`。导入已启用片段记忆但该表为空的 SQLite 源时，segment marker 会复制成功，缺列却未被发现；真实 `/memory/candidates/materials` 更新该列时报错并返回 503。非空源还会在导入阶段失败。隔离 PG17 的原 server log 明确记录了 `column "event" does not exist`，不是延长超时可解决的问题。

修复给新建/导入 schema 补 nullable event 列；兼容规则统一归 `segment_migration`。业务装配与既有 database 探针在同一只读连接核验，失败关闭已开 Store；未启用 segment 的 Knowledge 旧 schema 继续兼容。Store 保留原维护入口可达性；显式 `migrate-segments` 对已启用且仅缺该列的 PG 先走原事务锁、guard 检查、独占完整逻辑备份与 guard 副本，再仅补列，不填历史事件、不重装 marker/trigger、不改原 revision。普通请求和启动不执行修复 DDL。

正式 R11 原库只读核验：Memory 已有 event 且 segment=1，Knowledge 无 event 且未启 segment，两者 binding active、marker 原字节不变。因此本次正式发布无需表结构修复、业务数据库恢复或迁移。隔离测试中的补列操作不代表改过线上数据库。

| 产物 | 精确身份 |
| --- | --- |
| Platform 运行提交 | `177e2eb25a104a9d58bfb71f53040baa481ddbf6`；交接 HEAD `4ef6edb922e14b66d5a252e70f518c4bc70ff907` |
| Memory/Knowledge 运行提交 | `8d01f9d9d39fba33b6ca8dca9d2599730c9c7e1e`；交接 HEAD `34d3175a07f853613680d76bf815611489a9edec` |
| Companion / Gateway | 继续使用 `225f62a9c201acfc283bc029562f118f16d5e524` / `0f491be3573af9257703b7871aaa746598d66b9f` |
| application | `cf52d6246d78469d5aea0ccbb995d0baf78b2248`，archive SHA256 `e464fefd6214b44005ce083dac3bc157001d09785fa6710c41007541a08f4990` |
| R13 Linux amd64 镜像 | `sha256:e3e7fd1b504f954889c4d834e6df9c46d4cf562c3f878b23528ebe858185ba75`，3388844388 bytes |
| Memory schema / wheel SHA256 | `513fa2432934174479fad5a35d128b35921553158fbe076132dd58ef33a902f8` / `bae6944020d1b924abba0bb434906d24ece129b02a11df8936c8b628d2f98fac` |
| R13 runtime lock / release receipt SHA256 | `3b01226b8f6be93590cc4efa021e01ca9d811cfe4721a9a9731f07c672748291` / `b3e0e54431bf2fd082cadad0dda5b33cf6fdadefb1a8ff9cb5522640c1fa5694` |

R13 以原五个精确基础镜像、完整 Dockerfile、全新 venv 和离线 hash 安装构建，没有向运行容器热覆盖。相对 R11 仅两个产品 wheel 变化，其余 64 个 wheel、56 个 application runtime 文件、71 个前端文件原字节相同；公开 61 依赖图未重新解析。Platform wheel 仅 selector 与 RECORD 变化；Memory wheel 仅 app、runtime_probes、segment_migration、postgres_schema.sql 与 RECORD 变化。R12 为实际验证过但未正式启用的中间候选。

## NAS 安装包完整联动

最终 R13 镜像在 230 上分别用 SQLite 和专属 PG17/Unix socket 执行完整 BusinessHost。每个 backend 都是两次全新解释器、六个真实 owner 线程，测试和合成合同只读挂载，没有产品源码 overlay、生产配置或生产数据库挂载。

实际链路为本地 OneBot WebSocket peer→原 SDK→Platform 登记/准入→Companion→Memory 原材料回执→保留原 120 秒静默窗口的 schema6 来源归并与 committed 回执→按会话召回→Gateway→本地录制 provider→Platform expression→原 SDK 发送。每个 backend 各 10 次真实 SDK 协议发送、15 次录制 provider 请求；这些是本地协议端点，不是真实 QQ 或付费模型。

两个会话都取得真实记忆回执；同会话显式前序依赖在前一回复提交后继续，另一房间的回复及到期提醒可推进，忙碌房间提醒延后。带来源的主动联系真实使用召回的个人上下文和 Gateway 回执；普通提醒使用已登记模板，不能冒称模板提醒也调用了模型。正常关闭后以原隔离状态重启：待办执行一次，unknown 仅原先一次发送且不重发，取消前未到期的日程零发送，原记录保留。PG 实际 backend 和各 namespace binding active 均断言；不是 SQLite 回退。

Memory PG 针对性回归共 11 项通过、零跳过：空/非空片段源导入、真实材料重复受理、缺列业务/ready 拒绝、原 CLI 先备份修复、备份冲突/guard 错配拒绝、未启 segment 的 Knowledge 兼容。原首次 1 passed/10 setup error 是 TLS 工具输出目录错误；改回 checkout `.runtime` 后只重跑受影响的 10 项，原 1 项复用。Platform 新 HTTP 与原观察/角色共 26 节点、5 subtests 通过；首次遗漏角色合同 fixture 的单项失败只补原合同后定向重跑，其余 25 项复用。

音乐在实际 R11 安装包上走通原 Cookie/CSRF、NetEase 搜索/歌单协议、本地 CDN、原后台下载 worker、真实 ffmpeg/ffprobe、封面和 portable tags、content ID、单次传输入库、重复复用、重复成员顺序及缺失成员保留、JSON/M3U8 导出、Navidrome partial→synced 与有序回读。NetEase/CDN/Navidrome 为本地协议端点，未访问真实音乐账号或外部发布。此前跳过的两项 NCM 测试已显式使用镜像现有固定解码器运行，2 passed/0 skipped，无新下载；二进制及对应源码归档 SHA256 分别为 `6d71732f14665207103aca5ff2334c7d869c0aea4b7fc05fc4b54e734825b749` / `ad41ded12eeb45ba047662d0ff8ce415950d4010e681fbfec7927abab21e46b1`。R13 实际安装文件再次核验音乐六模块、Platform service、解码器及源码均原字节相同，复用这些结果。现有产品没有音乐写回 Memory 的独立业务协议，不凭空增加或宣称该路径。

原生工具保留为 `tests/deployment/single_container/native_schedule_check.py`（SHA256 `3dc196a83094cb3a6cbe421856beabdcd4f6fcc5145d5f846f3b98eeb7c55db7`）与 `native_music_check.py`（`1c286c4b6f7448e521be70a7a0e8726f2d37507adb67c3a5cf5596fb108443b3`），均显式运行，不进入普通单测发现。PG URL 必须明确指向专属隔离 socket 数据库。重启测试是正常关闭再启动，不是 kill/crash；日程登记使用原 domain 管理入口，未声称浏览器管理链路。身份读取和 Path 参数的两次工具适配错误保留为失败证据，没有放宽 storage guard。

## 历史日志与资源测量方法

R11 在更新前已取得 45 秒两窗严格证明：基线 5 文件的 46485626 bytes 已扫描，9734 个基线 ledger 身份及其中 246 个 pending 均按真实 Loki hash 核验；窗口 B 历史 pending 与旧/未知实例 pending 均为 0。guard ready、查询无新增失败、scan budget/scan-behind/完整性错误为 0，Vector 原 mTLS/ACK 指标可用。实时新 tail、7 个新实例 pending 和未空的缓冲单列，不冒称完全空闲。

该证明之后、无隔离测试运行的 120 秒 R11 窗口：working set 中位 1179516928 bytes（1.0985 GiB），CPU 单核口径 161.6647%（8 核总量 20.2081%），pids 中位 272。数值高于先前部分窗口，如实保留；短时单进程 profile 不能抵消该测量，不能声称追赶结束就必然降低 CPU 或证明同负载性能改善。

R13 日志阶段发现的瓶颈及后续 R14 实机追赶证明见下文，最终资源结果已补入末节；R13三次未通过的追赶记录仍保留。诊断工具 `production_log_history_check.py`（本次实际执行的冻结版本 SHA256 `56332b59a5c24fc3827f3cfc882e2d4c9d1106037238ddb28b97ec81f7399a58`）在原运行容器 UID10001 下只读获取每个窗口的一致 WAL 快照、源 offset/identity、正常退役证明及 Vector 指标，不构造新 app/Ledger、不写 ACK、不清日志。只有三个 history/old pending 门槛通过后才启动正式 120 秒资源采样；测试容器先正常清理，采样不发送合成请求。

## 日志稳定性修复与 R14 实机验证

R13 的业务联动结果保留，后续日志验收另外发现了观测链路的实际瓶颈。完整 30 天、512 个身份的原查询在 10 秒期限失败，缩至单个身份的同长度查询也曾超时；只查最近 1 小时的 512 个身份则在 5.236 秒完成、512 个 hash 匹配且无缺失冲突。诊断 profile 取得了单个身份长查询的 721 splits / 721 shards；该次独立诊断允许更长等待，但生产查询的 10 秒期限未改，不能把诊断成功算作生产验收通过。对应回执为 `.runtime/chain-optimization-20261028/r13-reconcile-query-diagnostic.remote.json`、`r13-reconcile-stats-one.remote.json`、`r13-reconcile-stats-hour.remote.json`、`r13-reconcile-long-profile.remote.json`。

现场连续库存从 1323 增至 1326 个 delete requests，全部为 received，最早与最晚创建时间跨约 9 小时。原 monitor 每次退役前缀增大就 POST 一个新的 `sequence<=through` 请求，未提交意图虽按同一 service/instance 合并，已提交请求仍会在 Loki 中形成大量重叠过滤器。Loki 3.7.8 默认取消期为 24 小时，处理还额外等待 1 分钟；因此这些 received 状态本身不是 compactor 卡死的证据。该版本默认 BoltDB 查询路径还会返回已 processed 的请求；SQLite 的查询路径才会在完成及索引传播等待后排除旧过滤器。[版本默认值](https://github.com/grafana/loki/blob/v3.7.8/pkg/compactor/config.go#L58)、[BoltDB 路径](https://github.com/grafana/loki/blob/v3.7.8/pkg/compactor/deletion/delete_requests_store_boltdb.go#L165)、[SQLite 路径](https://github.com/grafana/loki/blob/v3.7.8/pkg/compactor/deletion/delete_requests_store_sqlite.go#L72)。

主协调者已在仍运行的 R13 上完成一次有界合并。冻结计划 SHA256 为 `f82bcf44d2f9bf5ee7f7f1e3f2d8220aabf8aafaa53f194a141ef4d422fb34a9`：只接受严格机生成的同 service/instance 前缀 selector，先只读核对 `retired_streams` 水位覆盖 maxThrough，且当前 live events 没有该退役前缀。每组新请求先取得 204，再 GET 核实时间与序号范围完整覆盖，才取消计划中捕获的旧 request IDs；未知结果不推进，也不持续替换已经成熟的请求。新矩形扩大出的范围仍属于同一权威退役前缀，不改成可能漏掉晚到副本的 delta 下界。

实际回执为 1336 个旧请求、创建 16 个 covering replacements、取消 1315 个旧 captured IDs，操作后库存 40。业务数据库和 ledger 都未写入，`retained_source_events_deleted=0`。合并后同为 512 身份批量、完整 30 天范围的查询在 2.834396 秒完成，返回 512、hash 匹配 512、missing/conflicts 均为 0；这不是缩短查询窗口或绕过 hash 的结果。两次选择来自各自现场快照，不声称身份集合与负载完全相同。计划、执行与只读查询证据分别为 `.runtime/chain-optimization-20261028/r13-retention-coalesce-plan.remote.json`、`r13-retention-coalesce-apply.remote.json`、`r13-reconcile-after-coalesce-r1.remote.json`。单次改善不替代最终历史追赶和资源测量。

R14 将持续修复集中在三个 application runtime 文件：`deploy/observability/configs.py`、`ledger_schema.py`、`monitor.py`，代码提交 `48eee53d6ba97efae5c44385cb53a894ffa0c215`。观测 ledger 升级到 schema 4，为删除 outbox 增加 `submitted_at`，每个 stream 将五分钟内新增退役意图持久合并；失败不推进提交水位或时间。10000 条可见上限、原哈希核对、完整 30 天查询范围与 10 秒期限保持。查询改用 24 小时 split，减少长窗口的分片调度开销，不能把 split 设置误写为只查 24 小时。

Loki 配置采用 SQLite primary / BoltDB backup 和 cancel period 1m。上游原生路径只在 SQLite 空库时从 BoltDB 复制全部 shards/cacheGen，随后按同一 request ID 双写新增、取消与完成；这是 Loki 删除请求存储迁移，与业务 PostgreSQL 迁移无关。双写不是跨库原子事务，BoltDB backup 是活动镜像；回切 BoltDB 时上游会清理 SQLite 供再次导入，因此仍要保留完整冷备，不能把镜像视为不可变备份。[上游迁移与双写](https://github.com/grafana/loki/blob/v3.7.8/pkg/compactor/deletion/delete_requests_store.go#L71)。cancel 1m 后还需额外 1 分钟、retention cycle、索引传播和 querier 缓存等待，不声称一分钟内物理删除或过滤器归零；有效过滤器应查 `for_querytime_filtering=true`，默认列表仍保留 processed 审计行。

R14 Linux amd64 镜像已用完整 Dockerfile、离线依赖与固定基础镜像完成构建，身份为 `sha256:dd0cd89618a04a281717fc51aa81c10e3e552d8b2a8a5f0ef244141afb952611`，3388845737 bytes。离线构建时的回执为 `linux_image_built_not_activated`；后续已完成正式部署，不能把该历史构建状态当成当前状态。实际 Linux 观测相关测试为 59 passed / 31 subtests passed，回执 `.runtime/chain-optimization-20261028/linux-build-r14.json` 与 `r14-linux-observability-status.remote.json`。

独立原生工具保留于 `tests/deployment/observability/native_delete_store_check.py`，SHA256 为 `4917887b2a98d91607d68e77bb04c039e62e4d53cbb83b9300aa1a07d5f825a6`，使用 R14 实际镜像中的 Loki 3.7.8、配置生成器与 loopback mTLS，49.969 秒内完成 BoltDB→SQLite primary/BoltDB backup→BoltDB rollback 三次原生进程运行，三次均正常终止、returncode=0。原 4 个请求的 ID/query/start/end 四核心字段在迁移后保持；新增 POST 与取消 DELETE 双写在回切后仍可核对，4 条保有日志保持。SQLite quick_check 为 ok。没有缩短原 compactor 周期，没有直接预造 processed 状态；该工具明确未验证 physical chunk reclamation 或 processed filter retirement。证据为 `.runtime/chain-optimization-20261028/r14-loki-native-r1-start.json` 与 `r14-loki-native-r1-status.remote.json`。

R14 业务结果复用已有实际字节证明：从 R13、R14 两个不可变镜像独立读取 66 份发行 wheel、71 个前端文件、53 个未改 application runtime 文件，以及 5 个发行包实际安装的 623 个 payload 文件和 4 个 console wrappers，逐项相同；仅上列 3 个观测 runtime 文件变化。检查未启动业务装配、未挂生产配置。故原 R13 SQLite/PG 完整原生、产品与音乐验证按相同业务字节复用，不新增一份 R14 业务执行记录。R1 因未纳入 pip 生成 wrapper 的 RECORD 条目误拒；R2 明确核对 wrapper 后通过，原拒绝记录保留。证据为 `.runtime/chain-optimization-20261028/r14-unchanged-business-r2.remote.json` 及同名执行脚本。

R14 正式容器随后进行只读确认：guard ledger 为 schema 4，Loki 生效 split 为 `1d`、cancel period 为 `1m0s`；重启前捕获的 108 个 delete requests 的五核心 API 字段全部保留，当前 111 个包含正常新增，SQLite quick_check 为 ok。没有把请求保留或 API 204 当成物理删除、processed 过滤器已经退场的证明。最新 512 个已确认身份的完整 30 天查询用时 2.810613 秒，512 returned/matching、missing/conflicts=0，实际 31 splits；选择集合与旧查询不同，不称为 controlled A/B。证据为 `.runtime/chain-optimization-20261028/r14-production-loki-migration-r2.remote.json` 与 `r14-reconcile-confirmed-profile-r1.remote.json`。

## R14 历史追赶证明与资源采样起点

正式容器内的只读两窗证明 `.runtime/chain-optimization-20261028/phase2-log-catchup-r14-r1.remote.json` 已完成，证明 SHA256 为 `72b0872b7a5ae821687561ebba9a9a03aa0d13278413d594c120d9a269dac5c6`。实际两窗相隔60.084秒，`comparison.interval_seconds=30`为monitor配置周期；三个门槛 `historical_valid_source_frontier_complete`、`history_frontier_consumed`、`old_pending_resolved` 及全部 16 个 conditions 都为 true，两者分别记录，不把配置周期当作采样间隔。

基线 9807 个 ledger 身份中，9295 个 verified，512 个沿原已确认前缀正常退役；原 472 个 pending 全部经真实 Loki hash 核验变为 verified。基线 9 个源文件、37644258 bytes 均已消费；窗口 B 的历史 pending=0、旧/未知实例 pending=0，query failures 无新增、最新 scan budget=0、完整性错误为 0。实时当前实例仍有 9 个 pending，Vector 缓冲不为空，`completely_idle_at_second_window=false`；这里只证明历史基线完成，没有声称系统完全空闲。源 offset 仅代表扫描进度，完成结论结合了 hash、正常退役与原 source receipt，不单凭 offset 或 Vector sent counters 推定 ACK。

通过上述门槛后，主协调者在 `started_at=1793158103.9722042` 启动 120 秒资源采样，期间没有隔离测试容器或合成请求。启动回执 `.runtime/chain-optimization-20261028/phase2-start-post-catchup-resource-r14.remote.json` 绑定同一证明 SHA。120秒采样已完成，数值见末节；没有宣称同负载性能或内存优于旧版本。

## 正式发布与保留边界

R14 已正式部署，容器身份 `cd2a9b217f063a7115ee9eec7901b5a01225fdd7629c1c2d31b728ec763bacba`，镜像为上列 `dd0cd896…`，启动回执 `started_at=1793157827.2332773`，guard generation 为 `/srv/tianshu-tooling/unified-capacity-20261028-r7`。1126 个配置文件只变更 Loki 的四个字段与 Gateway 原同 scope 的 origin lease 引用，没有新增权限；Compose 仅更新不可变镜像，37 个 bind mounts 与 25 个 logical bindings 不变。六个业务 module、五个观测进程及漫画角色均 ready，首轮10次带原鉴权的readiness均HTTP 200。资源采样后已完成跨300秒复核及最终guard生命周期验证；原ready、模块、13个旧容器cold/restart=no和新守护均符合原约定。

本次停写备份按实际源分别记录：guard 3 个条目、3510272 bytes、2 个 SQLite；Loki 本地 95 个条目、2889329 bytes；NAS delete-request 对象 2 个条目、18852 bytes。各副本均核对 source unchanged 并以 disabled restore 验证，没有复制 NAS 全部 chunk，不能称为全部 Loki 数据的同一瞬时 cold snapshot。原完整业务 PG 冷备与 R10 完整 physical 备份继续保留；本次没有业务 PG restore/migration、没有清理日志。证据为 `.runtime/chain-optimization-20261028/r14-cold-progress-c.remote.json`。

启动阶段失败保留原边界：TS-361 首轮在 health=starting 时被既有门禁拒绝，ready 后 r2 通过；actor 首次引用拒绝态旧回执属于工具输入错误，换用正确 r2 输入后独立 namespace/guard 验证通过；Loki 生产迁移脚本 r1 在本地因 null byte 解析失败，修正工具后的 r2 完成原生生产检查。这些不是被隐藏或重分类为通过的产品失败，原始拒绝/解析记录仍保留。相关正式回执包括 `.runtime/chain-optimization-20261028/start-production-r14.remote.json`、`production-config-r14.remote.json`、`production-storage-r14-r2.remote.json`、`production-actor-r14-r2.remote.json`；最终资源、跨租期、guard生命周期及聚合通过回执已补入。

真实 QQ 收发和正式网页登录按用户要求延后；实际付费模型、真实 NetEase/Navidrome 账号及外部发布未冒称通过。原根混合修改、`.archify/`、各产品分支及旧发布证据保留，不 reset/clean/stash，未推送远程或合入产品 main。


## 追赶完成后的资源与最终状态

R14 在三项历史完成门槛及全部16个条件通过后，于 Unix 时间1793158103.9722042启动独立120秒cgroup v2采样，无隔离测试容器、无合成请求。起点引用的追赶回执SHA256为`72b0872b7a5ae821687561ebba9a9a03aa0d13278413d594c120d9a269dac5c6`；资源回执SHA256为`2c3f7c1e6f20dfaf5ddffa1b638cd1cf615910739af65f8de7c5990df26afb43`。

| 实测项目 | R14 正式120秒窗口 |
| --- | --- |
| working set 中位 | 1560240128 bytes，1.4531 GiB；范围1.1601–1.5140 GiB |
| anonymous 中位 | 1289879552 bytes，1.2013 GiB |
| memory.current 中位 | 1968041984 bytes，包含未从working set扣除的文件缓存 |
| CPU | 单核口径91.0240%，8核总量11.3780% |
| pids 中位 | 263 |
| 合成请求 / OOM | 0 / 0 |

内存没有低于前一R11窗口的1.0985GiB，不能宣称所有资源都下降或给同负载节省比例。采样后只读进程核对显示主要占用为Loki、统一Python业务宿主与原Grafana及数据源进程；未为降低数值删掉既有引擎或功能。容器原10GiB限制保持，memory.events的high/max/oom/oom_kill均0，检查时PSI三组平均值0；单次及短窗口不能据此宣称长期无泄漏。

采样后又执行一次原冻结诊断，两窗实际相隔60.099秒（monitor配置周期30秒），三项门槛及全部16条件再次通过：9651个A窗口账本事实中9139 verified、512正常退役，A窗口163个pending全部verified；8文件13011084bytes被消费，另1个26459406bytes旧Platform段凭原收据与退役水位证明已正常回收。B窗口历史及旧/未知实例pending仍0，新当前实例pending158单列；两个繁忙源的未读尾部净减少各约8KB，未把新实时tail视作历史追赶。原clear cutoff不变，无新query/integrity/Vector错误。对应`phase2-log-catchup-r14-r2.json`。

原汇总工具第一次额外要求某个瞬间`scan_budget_exhausted=0`而拒绝，未产生正式通过回执；该点同时为ready200、monitor_success1、query_failures0、当前capacity拒绝0、账本9928小于10000。有界refill可耗尽其读取预算，不能据此把正常背压冒称产品故障。最终汇总保留原采样起点证明和时间顺序，另要求采样后这次完整两窗的原16条件全部通过；仍核对当前ready、真实query/gap/retention/capacity错误为0，不改产品就绪语义或冻结诊断。

正式两轮共20次并发readiness均200且每次原9项检查完整为ok，跨300秒租期后Gateway与Host仍ready；HTTP18446、原CA与hostname验证的TLS18443匿名401、OneBot监听18765、五来源真实ACK、71个前端文件及原生只读链路查询均通过。真实QQ收发与正式网页登录按用户要求暂不测；实际NetEase/Navidrome账号、付费模型及abrupt kill也不混称通过。双图入口继续为[任务与设置→链路图](http://192.168.31.230:18446/#/settings/12)。

当前运行application为`48eee53d6ba97efae5c44385cb53a894ffa0c215`，R14镜像`sha256:dd0cd89618a04a281717fc51aa81c10e3e552d8b2a8a5f0ef244141afb952611`，3388845737bytes；产品实现仍为上文P177/M8d01/C225f/G0f49，后续文档/工具提交不是该镜像的运行源。根原混合修改及`.archify/`保留，没有reset/clean/stash、推送产品main或再次清日志。

最终汇总`production-release-r14-r2.remote.json`已取得`production_r14_accepted_with_explicit_limits`。它保留R1起点SHA与采样时间顺序，核对采样后的R2为同owner/image/原冻结脚本与完整16条件，最后以新编号guard回执复核最近成功、原120秒投递积压阈值、query/gap/retention/capacity错误；未覆盖或改写首次拒绝记录。

入库整理时仅去掉日志诊断工具未使用的except变量绑定，AST除该绑定外逐项相同；仓库文件SHA256为`c2645cf0d100e2cb50aa5b79929696464e80c8a71283ac59321ab64652c8acd8`，真实NAS回执仍引用未改的56332版本，不把新字节冒称已实机执行。其他三个原生工具保留实际执行原字节，四工具Ruff/AST通过。
