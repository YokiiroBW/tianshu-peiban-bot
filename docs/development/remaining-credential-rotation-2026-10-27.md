# 2026-10-27 剩余复用凭据轮换与单份备份：执行记录

本记录以实际执行收据为准，精确路径和 SHA256 见[结构化收据](remaining-credential-rotation-2026-10-27.json)。原始失败、阶段状态和实际中断保留；不含密钥、Env 或备份正文。

主要密码/token 轮换、Chat 签名切换、最新单份备份保留和十个冷副本目标清理均已完成；线上新 key 与真实 registry 对今天备份的再次全流兼容验证也已通过，用时 684.686 秒（最终 S09 收据）。SnowLuma VNC 按用户后续要求恢复原值，是明确的 `user_kept_original` 例外。

## 当前凭据与消费者状态

| 范围 | 实际结果 | 证据与限制 |
|---|---|---|
| MySQL `root@%`、`root@localhost`、`yoki@%` | 新值 fresh auth 接受，旧值均错误 1045；三个 secondary 均 DISCARD；权限不变 | M01。先 retain 迁消费者再 discard；server 未重启，未改 schema/业务数据。 |
| music-tag-web / Blossom | 实际 Env 与新值一致，原 image/挂载/网络/其他配置保持；音乐原 MySQLdb fresh SELECT 1 通过；Blossom 原 OpenJDK 8 + Connector/J 8.0.33 只读连接与 SELECT 1 通过 | M02–M04。网页 200、连接池数 0 没有被当作数据库阳性；未读业务行或启动额外应用上下文。 |
| Chat 管理与 OneBot 入站 token | 新管理 HTTP 200、旧 401；新入站 upgrade 101、旧 403 | 最新 C01。零事件帧/QQ发送。四 producer 曾由原 owner 保存并热应用配置（C02）；最新连接指标仅 aggregate=2，不能据此证明两个或四个唯一 producer。 |
| Easy-VDL | 新密码及认证 verify 接受，旧密码拒绝；旧共同值签出的 JWT 被拒 | U01。真实 Env override 生效；未调用下载。 |
| 两个 Homepage | 两者新密码/新会话接受、旧密码拒绝；重建清除旧内存会话 | U02–U03。原 image/挂载/网络/无关 Env 保持；首个停止 137 原失败与复核续接保留。 |
| MoonTV | 新密码/会话接受，旧密码 cookie 被拒 | U04。未调用搜索、播放或 cron；原应用启动自身既有后台行为未改。 |
| SnowLuma VNC | **用户保留原值**：原值恢复并接受，临时新值拒绝；持久 Env 恢复 | U07–U08。反向精确 patch/recreate；通用 receipt 的 new/old 指反向计划，不能再写“原 VNC 密码拒绝”。有效长度按协议为 8 字节；未读桌面或输入。 |
| 停用 TinyMediaManager | 持久配置和停止容器 Env 更新，仍停止，原 image/挂载/网络保持 | U06。real_auth_verified=false；未为验收启动，下次原入口将使用新 Env 生成 VNC 密码文件。 |

Chat 认证元数据盘点未发现匹配旧共同值的 managed token/session hash/user password，managed users 当时为 0，未任意撤销独立身份（C03）。上一轮共享 PG/Redis 已撤销旧值，本轮未重新实施或重复验收。不得笼统写“所有旧值都已消失”：Snow VNC 是用户保留例外，历史备份 key 仅保存在受控内容指纹信任清单中。

## Chat 源码、双运行实例和签名切换

最终源码 `eb81e936b2675022ee135f101477b008ab26db34` 正常推送，远端 main 复读一致，无 force push（C05）。镜像 `sha256:3b6464d2300717a3fb28afc70a79ae6e084e157ab6f62de1f38127ab9d1601b3` 的 88 个源码文件核验通过，依赖未变（C06）。最新 app 和 backup-worker 都实际运行此 image、healthy，原挂载/其他配置保持（C04、B06）；早期构建工件 production_deployed=false 只代表当时阶段。

APP_SECRET_KEY 已实际切换。registry 有两个明确内容 pin，旧 key ID 仅为 `pre-rotation-20261027`，无任意旧 key fallback、无新历史 key（S07）。两个真实运行实例各执行 22 个布尔检查，全通过：新 key 匹配预期且不同于旧 key；新 JSON/gzip/v4 输出与 cursor 可验；旧 key 不能验新输出；旧 key 签出的未登记内容拒绝；旧 cursor 拒绝；真实 registry 路径/key/pin 可用；临时文件清理（S04）。探针未访问业务数据库、写业务数据或发送 QQ。

旧完整 10/26 archive 在换 key 前全流验证 checksum/signature 通过，642.009 秒（S01）；今天 archive 已由独立 follower 全流验证，见下节。`actual_history.py` 使用线上当前新 key 与真实 registry，再次完整只读验证今天的实际 archive：checksum/signature/pin 全部通过，指纹与原跟随验证一致，用时 684.686 秒；实际当前 key 与两个运行实例身份保持一致。最终收据 S09 已纳入结构化记录。S06 为此前 running 阶段，不代表最终结果。

历史来源信任不是独立的暴露前防篡改证明：依据是既有受控目录、原 owner job 元数据、文件身份和本轮签名/内容验证；未取得在旧凭据暴露前独立保存的完整内容 hash。清单是用户授权的明确内容信任决定，不能将后计算的 hash 描述为旧文件历史上绝未被改动的证明。

## 最新只保留一份，冗余副本已清理

今天固定 job `9b695c8d0229444426e3b6fa82af45f5` 已 succeeded，最终 `auto-backup-20261027T030002Z-356a79d6.cacb` 为 **124,789,003,301 字节**。follower 正常退出 0，1709.788 秒完成，gzip/checksum/signature/eligible 全 true，FIFO 字节与 archive 一致，最终 dev/inode/size/mtime 与原 job 对上（B04）。内容指纹 `8765b20d980a2780383d1205561e49ebd5df8636541badd57098e6c76b0046bd` 已原子加入 registry（S07）。跟随验证与换 key 后的新策略全流验证均已通过；没有在生产执行 restore。

有效数据库设置 `auto_backup_keep_latest=1`。登记后只删除身份匹配的前一份 10/26 archive（123,762,461,273 字节）；API 复核完整数 1、latest 为今天、active=0、keep_latest 来源 database（B05）。最终只读状态再确认 **1 complete、0 incomplete**，app/worker 均健康（B06）。旧 job 携带的 keep_latest=7 快照已结束，不再作为未完成项。

| 用户授权的删除阶段 | 实际结果 | 保留与解释 |
|---|---|---|
| live 初轮旧备份 | 17 文件，792,127,376,042 逻辑字节 | 当时保留 10/26 完整 latest 和今日工作文件（B01）。 |
| 两 cold-data/backup 内旧 archive 副本 | 36 文件，1,831,779,674,630 逻辑字节 | 当时仅删精确 archive 清单，未递归整个 stage（B02）。 |
| 今天完成后的 live retention | 1 文件，123,762,461,273 逻辑字节 | 保留今天唯一完整备份（B05）。 |
| 两已知部署 capture 剩余冗余数据 | 10 个精确目标全部 success，529,818,398,678 逻辑字节 | cold storage/backup、隔离 scratch storage/PG、native dump；逐目标核最新 guard、无容器挂载引用及原元数据；小证明保留（B10–B19）。 |

后一个阶段覆盖用户后续授权删除的原生 dump 与冷备大数据，因此不能沿用早期“大文件仍保留”状态。最终十目标全部 absent（B06）；capture/restore/验证小收据是操作证明，**不再表示对应大文件仍可恢复**。逻辑字节可能含 reflink，不能相加宣称物理释放量。本批未对生产数据库或 live 媒体 restore/替换。

## 验证和实际中断

最终 `eb81e936…` Linux 备份/签名组 **83 passed、2 deselected**；排除大 peak-memory 基准和另一历史 PostgreSQL backup-export case。原生隔离 PG 启动/迁移组 **29 passed**，包括真实 REPEATABLE READ/READ ONLY reader 持有期间完整 lifespan 进入；未变部署文件复用 Windows **16 passed**（T01–T02）。这些测试没有使用生产数据库。

原不利结果保留：第一次因 export-ignore 缺 tests，0 tests/returncode4；第二次 harness 非空管理员 token 与无认证测试冲突及缺 Git 资源，85 passed/11 failed；修正后才有后续结果（T03–T05）。独立审查发现历史 key lone-surrogate 会破坏当前 v4 编码，已修正验证。运行探针首次遗漏 docker run -i 导致 stdin JSONDecodeError，修正后隔离 22 检查通过（T06）；不隐去首次失败，也不将 harness 失败描述成产品回归。

生产不是无中断维护：

- Chat 初次重建健康超时，旧启动重复迁移等待 DROP INDEX，被正常只读备份 reader 阻挡；修复按 migration journal 跳过已完成版本。根仅取消确证遗留的旧启动 DDL，没有取消 backup reader/业务语句或直接改 schema/数据；取消后业务等待归零（I01、I03–I05、T02）。曾有积压，不承诺零事件丢失。
- Homepage 停止 137，复核后续接；SnowLuma 先轮换、后按用户要求恢复原 VNC，各阶段收据保留（I02、U02、U05、U07–U08）。
- Core 后续 full 一次失败仅 Platform runtime=failed，其他 8 项 ok；未改服务后单次 TLS ready 恢复200，原完整 profile 再验 **full_runtime_acceptance_verified**（I06–I08）。不能改写为旧 PG 探针整体超时，具体后台 loop/异常原因未取得。

## 仍须保留的限制

1. Snow 恢复原 VNC 后当前 3 个 QQ、9091/Core ready；相较最早 5 个，另外 2 个没有重新登录成功证据。恢复收据 all_previous=true 仅对应反向操作前的 3 个，不代表最早 5 个全部恢复；需用户登录，不列完整账号。
2. TMM 保持 stopped，真实 VNC AUTH 未测；大 peak-memory/独立历史 PG export case 未跑，没有本批生产 restore。
3. 零模型调用、零 QQ/OneBot 事件测试发送；没有下载、播放、照片/笔记/聊天正文读取来扩大验收。正常认证可能写认证元数据，不应宣称所有认证状态都未变。
4. 原始阶段工件及失败索引保留，最终安全修复摘要已转 managed persistent 存储；位置与 hash 见结构化记录。


完整运行复验为 13 服务、25 逻辑源、3 guard、9091 ready；最终 QQ 元数据仍为 3/5 在线，缺少的两账号由用户在客户端完成登录。配置只作最小修改；PG/Redis/MySQL server 未因本轮重启。另一记忆开发聊天在维护收口后取得串行发布窗口，必须从最新配置最小 patch，不覆盖本轮新凭据；其冻结的小型工具、target-release 和 upgrader 元数据保持。
