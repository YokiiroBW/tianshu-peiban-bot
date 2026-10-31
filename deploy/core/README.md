# 天枢单产品部署

本目录替代旧 `deploy/tianshu`、`deploy/observability`、`ops/resident_install`、
`ops/resident_capacity` 和 `ops/recovery` 的产品实现。2026-11-01 已完成集中验收、实际 Linux 镜像构建、备份恢复、日志缓冲补传及必要复验；全新生产实例和 PostgreSQL 已建立，作者配置已导入并读回核对。新实例在临时端口 28446 完成核对后已正常停止待切换，旧服务仍在运行，正式切换和旧业务数据删除尚待 QQ 对端恢复正常管理登录。下文为单写发布者的运行说明；本批实况见 [配置及切换记录](../../docs/development/coherent-refactor-configuration-2026-11-01.md)。

## 运行组成与目录

一个应用镜像运行 Linux PID 1 监督器、interactive、model-broker、worker，以及 Vector、Loki、
Prometheus、Grafana。监督器先完成唯一迁移总注册，成功后才启动三个业务角色；任一关键进程或
日志读取器退出都会终止容器，交给 Docker 重启策略。业务角色使用 UID/GID 10001；PID 1 只负责
固定子进程、身份切换、日志和停止。受管插件、Chromium 和漫画文件服务仍在这个容器内。

Compose 在 `cap_drop: ALL` 后只添加 SETUID/SETGID（切换业务身份）、CHOWN（数据属主）、
FOWNER（PID 1 对已属 UID10001 的数据目录恢复750）、DAC_OVERRIDE（打开私有业务日志）和
KILL（结束其他 UID 的受管子进程）。CHOWN/DAC_OVERRIDE 都不能代替 FOWNER 的异主 chmod 权限。
新日志与控制 socket 先在 PID 1 仍拥有时设置私有权限，再交业务 UID，不增加 privileged/SYS_ADMIN。

新应用目录由 `bootstrap.py` 显式指定，必须尚不存在。它包含：

| 路径 | 职责 |
| --- | --- |
| `.env` | owner-only DSN、唯一 master key、Broker/频道 owner/初装/观测秘密 |
| `installation.json` | 源 SHA、不可变镜像、专属数据库、审核后的外部挂载 |
| `config/` | 只读配置、证书、观测配置 |
| `data/files` | 唯一内容寻址字节库；权限由 Sources/Artifacts 拥有 |
| `data/state`、`channels`、`plugins`、`capabilities` | 对应领域的持久文件 |
| `data/logs`、`data/observability` | 完整 JSONL、Vector 检查点/磁盘缓冲、Loki/指标 |
| `guard/` | 新容器精确身份、容量观察与锁存状态 |

外部共享媒体只按发布者已审查的精确绑定添加，不能把旧 37 项挂载原样复制。
`installation.json.external_binds` 每项为
`{source:绝对宿主路径,target:绝对容器路径,read_only:bool,owned:bool}`。
`owned:true` 只用于天枢专属数据目录，并计入容量上限；共享媒体明确 `owned:false`，
不递归 chown、不备份其全部字节、不将其他服务当成本产品停止。

## 镜像与依赖选择

`images.example.json` 的八个镜像必须解析为 **linux/amd64 的实际 registry digest**，然后才能传入
`build.py`。占位符会被拒绝。uv 固定 0.13.4，与产生本批 `uv.lock` 的工具一致。
Python 与 PG client 使用 Debian bookworm，PG client 为 17；Vector 选择 Debian GNU 版本。
观测组件复用 R67 只读核实的二进制版本：Vector 0.58.0 GNU、Loki 3.7.8 netgo、
Prometheus 3.13.3 netgo,builtinassets、Grafana 13.2.2。
版本证据来自发布者的 `observability-running-versions.json`，不把旧未验证候选表当成运行证据。
冻结 `single_container/image.py` 使用的四个二进制目录也原样保留。
这证明选择依据；新镜像中的动态库、实际启动与配置兼容仍由 CR19 构建验证。

根锁定 Playwright 1.64.1；Docker 构建安装该包匹配的 Chromium 与系统依赖，生产不下载浏览器。
`seccomp-chromium.json` 来自官方 [v1.64.1 profile](https://github.com/microsoft/playwright/blob/v1.64.1/utils/docker/seccomp_profile.json)，
SHA256 `cc3e61cabda6bbc1e53e54d27ba4d55a9d3be829b6dd1a596f4a7b31b1cc7849`，许可证随目录提供。
按 [Playwright 官方容器说明](https://playwright.dev/python/docs/docker) 为非 root Chromium 保留
clone/setns/unshare 用户命名空间；不使用 privileged、SYS_ADMIN、unconfined 或关闭浏览器 sandbox。

镜像内 `/opt/tianshu/image-manifest/` 记录源码 SHA、完整 Python 包版本、网页摘要、浏览器修订、
OS 包与实际 ffmpeg/git/PG/Vector/Loki 版本。依赖与网页在构建阶段安装，不挂载旧产品源码。

Python 构建先按根/SDK manifest 与 `uv.lock` 安装第三方依赖并保存快照，再安装本次产品与 SDK。
运行镜像先用该依赖快照安装 Chromium/tokenizer，之后才覆盖完整产品环境、网页和部署源码。
源码 SHA 的 ARG/OCI label 与 `product.json` 放在最后；只变源码或 SHA 不再使前面的 OS、浏览器和
tokenizer 安装层失效。调整缓存布局的首次构建仍需生成新层，实际缓存命中由发布构建确认。

## 当前两宿主发布流程

已只读核实 `.230` 约 31.5 GiB 自由空间，容量底线 20 GiB，因此只有约 11 GiB 构建余量；
NAS volume2 约 361 GiB 自由。优先在 **NAS 上明确批准的天枢专属构建目录**构建，传最终归档到
`.230`，不扩大原容量限制，也不清理未知 Docker 对象。以下所有路径、提交与 digest 都由发布者填写。
宿主操作工具需 Python 3.12+；备份/初始化另需根锁文件中的 psycopg/cryptography，使用隔离环境。

1. 完整集成并完成 CR19 后，在 NAS 上提供该精确提交的 Git checkout 和八项 digest JSON。
   `owned-root` 必须是已确认的天枢构建专属目录，`output` 必须是其内尚不存在的子目录：

   ```sh
   python3 deploy/core/build.py --checkout /APPROVED/checkout --source-sha EXACT_40_CHAR_SHA --owned-root /APPROVED/build-root --output /APPROVED/build-root/EXACT_40_CHAR_SHA --images /APPROVED/images.resolved.json
   ```

   该工具只从精确 Git 提交归档产品构建输入，产生 `image.tar`、`source.tar` 与 `release.json`。
   传输后先按 manifest 核 SHA256，再 `docker load`，核实际 image ID 与 OCI revision。
   构建产物是发布材料，不能放进 `.230` 应用数据目录长期占用 20 GiB 产品限额。

2. NAS 单独部署 `compose.postgres.yaml`，使用新专属路径
   `/volume2/Dockers/tianshu-core-postgres/data`、私网绑定 `192.168.31.210:25018`、
   DB/user `tianshu_core`。`TIANSHU_PG_IMAGE` 必须是 PG17 + pgvector 0.8+ 的已核实 digest；
   `secrets/database_password` 权限 0600。实例初始化执行 `postgres-init.sql`。
   **不在现用 `xinghai`/25008 共享实例安装扩展、删表或复用旧 schema。**

3. `.230` 当前实际入口是 `http://192.168.31.230:18446`，新装初始继续保留它。
   从本目录使用隔离依赖环境执行初始化，密码只从 owner-only 文件读取：

   ```sh
   python bootstrap.py --root /APPROVED/new-tianshu-core --origin http://192.168.31.230:18446 --mode http --image sha256:ACTUAL_IMAGE_ID --source-sha EXACT_40_CHAR_SHA --database-host 192.168.31.210 --database-port 25018 --database-password-file /APPROVED/database-password
   ```

   bootstrap 生成本机域名/IP 的一年期自签名证书；可信机构证书可由发布者安装到 config 并在
   `http.access.certificates` 以名称登记。初装 token 只在 `.env`，工具不会回显。
   实际反向代理使用 `--mode proxy` 与用户外部地址，上游走 HTTP；直接 HTTP 使用 `--mode http`。

4. 按真实媒体/插件输入补 config 和精确外部 binds，保证其 Unix UID/GID 与既有权限相符。
   如使用多个 compose 文件，在 `installation.json.config_files` 写明相对本部署根的完整列表。
   创建容器但先不启动，在 **该部署目录**运行：

   ```sh
   docker compose create tianshu
   python /CHECKOUT/deploy/core/freeze_guard.py --root /APPROVED/new-tianshu-core --container tianshu-core
   ```

   freeze 从实际镜像、源 SHA、完整只读标记/挂载/命令/标签冻结身份；与审核 manifest 不符则失败。
   每次容器 recreate 后必须重新冻结新 ID，不能放松旧 guard 来接纳任意容器。

5. 将本目录 `tianshu-core-capacity.service.in` 的 `@DEPLOYMENT_ROOT@` 替换为精确无空格部署路径，
   安装为新的 `tianshu-core-capacity.service` 并 daemon-reload/enable/start。
   旧 `tianshu-unified-capacity.service` 在旧容器停止前保持原样。
   先对新根执行 `python capacity.py --config guard/capacity.json`，再启动应用容器。
   原 guard 退出和旧数据清除，只由协调者依已核实的旧 25 schema、6 epoch 前缀及容器清单串行执行；
   本目录没有猜测旧容器/Redis/数据库路径的清理命令。

6. 应用直接保留 HTTP 18446、HTTPS 18443、OneBot 18765；Grafana 19490 与 Prometheus 19491
   仅宿主回环映射。Broker 8071、Loki 13100、Vector API/metrics 18686/18687 只在容器回环。
   `/health/ready` 看三个真实角色、任务、Actions owner 与 Channels owner；不以 SELECT 1 当作整机成功。
   前端来自镜像内 `web/dist`，同源 SPA 与 API，无旧网页 API fallback。

## 容量、日志与停止

容量守护仍每 5 秒观察 `/var/lib/docker`，自由空间底线与部署上限都为 **20 GiB**，TERM 为 120 秒。
它按新 root/专属外部路径增量核已分配字节，加实际镜像大小；不每轮扫描所有 NAS inode。
Docker 挂载只在新容器 ID 出现时解析一次，保留 R67 对路径解析开销的修复。
镜像、命令、全部 bind、只读标记或 Compose 身份不符时拒绝认领；已核实的本产品发生容量违规或
`foreign_deployment_mount` 则只将本产品 restart=no 并停止，锁存等待处理，不停止其他服务。
排除真实原因后 `--clear` 会完成一次有界全量计量再清除锁存；恢复 restart 策略与启动由发布者执行。

普通完整 stdout/stderr 与应用事件均持久化为 schema 2.0.0 JSONL，不采样、不另建 PG 日志账本。
监督器逐完整行脱敏（含跨读取块的秘密），落盘/fsync 后输出 Docker；Vector 保存检查点与 512 MiB
磁盘缓冲，Loki 不可用时重试/补传。非法 schema 进入独立 quarantine。原始 JSONL 不自动删除，
由实际保留期/可检索性与容量守护共同管理；不要把 Docker 的 30 MiB ring 当完整日志。
Loki TSDB/v13 保留 30 天，Prometheus 保留 15 天，Grafana 禁用匿名与自动更新。

停止先关闭 interactive/worker 并排空，再关闭 Broker/观测；监督器对超时进程组 KILL。
Linux PID 1/subreaper 最终回收被领养的浏览器/插件后代，不允许容器退出后留下受管进程。
宿主其他容器与进程不属于该清理路径。

漫画文件插件有两项固定受管服务，不接受任意命令。启用时在 `supervisor.comics` 填：

```json
{"socket":"/run/tianshu/comics/control.sock",
 "inputs":{"uid":10002,"gid":10002,"settings":"/config/comics/inputs.json"},
 "publisher":{"uid":10003,"gid":10003,"settings":"/config/comics/publisher.json"}}
```

inputs 文件为 `{directory:容器绝对输入根,socket:"/run/tianshu/comics/inputs/service.sock",client_uid:10001}`；
publisher 为 `{targets:{"comic-main":容器绝对成品根},socket:"/run/tianshu/comics/publisher/service.sock",client_uid:10001}`。
配置与目录先有相应 helper 的读写权限，监督器不接管共享媒体权限。
能力 `plugins.comic_files.runtime_socket` 指向上面固定 control socket；defaults 的 import/publisher socket
指向实际固定服务。唯一 PluginHost 经 Unix SO_PEERCRED 与 owner UUID 控制启停；冲突 409。

## 备份与恢复

`backup.py` 先核新 guard 的精确容器身份且应用已停止，随后对**专属** DB 做 pg_dump custom，
流式封存 config/data、唯一 master key 与安装 manifest。密码经临时 0600 pgpass 文件传给工具，
不放命令行。只记录迁移、vector 版本与 attempt 状态计数，不输出聊天或凭据。
`installation.json.config_files` 中实际审查过的所有 compose 文件也封存，保留叠加顺序；
freeze/backup 共用相同逐路径组件验证，拒绝越出部署根、符号链接、缺失或重复配置。
还原在数据库恢复前核归档必须包含全部已登记文件；原根内绝对配置引用改为新根相对引用，
然后重新 freeze 新容器。执行这组工具时须保留同目录的 `installation.py` 公共安装清单实现。

```sh
python backup.py backup --root /APPROVED/new-tianshu-core --destination /APPROVED/external-backups/NEW_ID
python backup.py restore --bundle /APPROVED/external-backups/NEW_ID --root /APPROVED/empty-restore-root --dsn-file /APPROVED/new-empty-db.dsn --manifest-sha256 EXACT_MANIFEST_SHA256
```

目标必须是全新目录与空的 `tianshu_core` 专属 DB；脚本拒绝共享数据库身份、路径逃逸、链接、
设备文件、摘要不符与未完成备份。还原保留原 master key，不重生成三套密钥，更新新 DSN；
Source/Action/模型账本、usage、unknown/provider handle 随 DB 原样保存，不自动重试付费任务。
共享外部媒体只保存精确引用清单，不声称已备份其字节。恢复后重新核外部路径并 create/freeze 新 guard，
再启动；不要复制旧容器 ID。演练与实际恢复尚待 CR19，不能以源码存在宣称恢复成功。
