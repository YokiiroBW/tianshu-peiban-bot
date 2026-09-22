# DEP-F 本地合成部署生命周期

本层补足旧 DEP-C 的“调用者先停机”缺口。默认只输出计划，只有显式 `--execute`
才控制已绑定的**本机新隔离合成部署**。不是 NAS 运维入口，不接受远程 Docker，
不会创建真实账户、调用模型、迁移数据库或开启恢复服务。

## 输入与绑定

沿用 `python -B -m ops.recovery` 及原 `--root/--scope-id`：root 必须是
`init-sandbox --execute` 创建的 synthetic scope。部署目录必须是其 `deployments/`
下的直接子目录，具有原始 authority 标记、完整 `release-manifest.json`、
`recovery-inventory.json` 和已经存在的登记卷。不会把任意现有目录改成合成 scope。
首次登记独占创建 `.lifecycle/`；已存在就拒绝，绝不覆盖旧登记或清锁重建所有权。

新增 CLI：

```text
python -B -m ops.recovery --root <绝对scope目录> --scope-id <scope UUID> lifecycle-init --deployment-directory <绝对部署目录> --project tianshu-synthetic-<独立名称> --backend local-process --compose-file compose.json --compose-file observability/compose.yaml
```

以上默认 plan；检查后在**子命令前**加入 `--execute`。返回 `binding_sha256`，
需由调用者另存；后续每次都显式提供，不自动信任目录内的新收据。绑定包括部署 UUID、
绝对目录、核心/日志两个项目、四产品及日志源码 commit、清单/库存/Compose 文件 hash、
每服务镜像与所有挂载。Compose 文件必须是生成器使用的 JSON 格式（`.yaml` 后缀可用）；
不解析自由 YAML、插值或 overrides，不接受匿名/命名写卷、遗漏服务或额外写路径。

固定 DEP-E 输入为 `cb371ab453621841e4a498598529a4ddcc41d3b2` 的
`deploy/tianshu/VOLUME-INTERFACE.md`、schema 与示例，测试只读该 Git 对象。
读取 1.0.0 的旧四产品格式；读取 1.1.0 时必须保留五个日志状态卷和 observability 来源，
不允许使用给旧日志配置器的 1.0 投影代替备份清单。日志项目固定 `<core project>-obs`，
Compose 工作目录为 `<deployment>/observability`，与核心项目分别核验。

## 停写与有界执行

```text
python -B -m ops.recovery --root <scope> --scope-id <UUID> lifecycle-backup --deployment-directory <部署> --project <核心项目> --binding-sha256 <登记收据> --backup <新备份名> --timeout 60
```

仍默认 plan，不发现运行进程、不连接 Docker、不写门禁。执行时顺序为：

1. 独占已有 `action.lock`，先核验**完整**运行 owner 集合和不可变输入。
2. 持久写入 `MAINTENANCE.json` 禁用标记、started 事件；任何失败均保留门禁。
3. 禁止已登记 owner 自动重启；按 platform → companion → memory → gateway →
   Vector → guard → Grafana → Prometheus → Loki 停止。消费者停前允许接收上游最后日志；
   不宣称停机时所有缓冲都已上传，Vector 的未发缓冲同样备份。
4. 每个 owner 必须真的退出。全部退出后再次核集合并保留独占边界，再进入原一致快照层。
5. 在封包/恢复发布前重核身份、停机、版本与挂载。完整事件和结果只在操作成功后写出。

`--timeout` 为整个停写/备份/恢复/验证共享的单调时钟预算，范围 `(0,3600]` 秒。
取消、超时、不正常退出、残留写者、身份/目录/版本/卷漂移均拒绝。
普通 Python 文件系统调用仍依赖本地 OS 返回；不能把此预算宣称为可抢占故障磁盘的硬实时期限。
失败前已停止的服务保持停止；未确认退出的服务**可能仍运行**，失败输出的 activation=disabled
只表示没有授予激活资格，不是声称所有服务已停。残留文件和未完成事件保留供排错。

`local-process` 是**测试专用适配器**，不向产品添加合作协议。测试 worker 是独立 Python
进程，持续写 SQLite/WAL 和日志；它登记 PID、内核创建时间、服务名与绑定 hash。适配器
只写精确 owner 的停止请求文件，以 Windows 原始 process handle 或 Linux boot ID/starttime
确认退出，并同时要求 graceful 收据和已释放的 owner lease。提前写收据不算退出，PID 复用拒绝。
worker 的启动登记也取得 action.lock 并核门禁，所以不会在维护期间重新登记。旧 DEP-C scope
合作锁仍仅用于旧测试；已登记部署不能从旧 backup/restore/code-selection CLI 绕过本层。

`compose` 适配器使用真实 Docker CLI 接口，**本轮没有 Docker，未实际执行容器**。
登记还要求所有镜像为 `reference@sha256:<64 hex>`，四产品必须与发布清单的 digest 相等。
执行额外显式提供 `--docker-executable <绝对docker路径> --docker-endpoint unix:///var/run/docker.sock`。
清除继承的 DOCKER_/COMPOSE_ 环境选择，不使用 shell，不连接远端。
枚举本地所有容器（最多 1000），核 Compose 两项目标签、目录/配置文件、单服务唯一容器、
不可变 ID/Created/Image、镜像实际 ID、读写挂载和权限边界；其他项目挂到本部署也拒绝。
先对精确 ID 更新 `restart=no` 并读回，再只发 `SIGTERM`。不用会在超时后强杀的 stop 模式，
没有 SIGKILL 退路、全局 kill、删除 owner 锁、down/remove/prune。只承认 exited/ExitCode=0，
OOM、Dead、Paused、Restarting 均不算成功。异常退出可能已有数据持久，但本层不据此猜恢复安全。

本层要求私有、单一运维控制的本地 scope。维护期间不允许其他操作员、Dockge 或宿主进程
绕过门禁写入/启动容器；Docker 自身没有对外部管理员的排他启动锁。本实现不承诺对抗同用户
恶意文件替换/未登记宿主 writer，也不把容器标签当作生产授权来源。

## 完整备份归属

| 资源 | 唯一写者 | 备份方式 |
| --- | --- | --- |
| 四应用 SQLite、登记 sidecar/guard | 各产品 owner | 停写后整组 SQLite 写入预留，Backup API 合入 WAL，原 guard 单独核验 |
| 四应用日志目录 | 各应用 owner | 完整保留段文件，绝不因 Loki 204、查询或对账而删除 |
| observability/data/vector | obs-vector | 全目录原字节，含缓冲与位点 |
| observability/data/loki | obs-loki | 全目录原字节，含 WAL/chunks/compactor 标记 |
| observability/data/grafana | obs-grafana | 全目录原字节，含内部 SQLite 与其 sidecars |
| observability/data/prometheus | obs-prometheus | 全目录原字节，含 TSDB/WAL |
| observability/data/guard | obs-guard | 全目录原字节，含 guard 持久状态 |

日志组件内部数据库不拆成应用 SQLite，不遗漏未知索引/WAL；只有生命周期确认所有 owner
停止才能走这种 raw 目录快照。包封闭文件及目录集合，保留空子目录，拒绝 links/hardlinks、
文件增删/内容变化和超预算。应用 SQLite 仍保留旧 rowid/guard/完整事实约束。
R1 修复后，文件集合、目录集合和发布前目录同步共用严格枚举：打开目录、逐项读取或条目类型
查询发生 PermissionError/OSError，均以去敏的 `directory_enumeration_failed` 拒绝，不视为空目录。
停写后才出现故障也不发布备份/恢复目标或生命周期完成收据，保留维护门禁和原锁。旧版本在枚举
失败时生成的包可能已经遗漏数据，不能从包自身证明完整，应从可读且停写的当前权威重新备份。
配置、凭据与证书沿用原设计：只存配置引用/hash，不把秘密注入备份；重建需单独重新注入与核对。

## 恢复、更新与激活边界

```text
python -B -m ops.recovery --root <scope> --scope-id <UUID> lifecycle-restore --deployment-directory <原当前权威部署> --project <核心项目> --binding-sha256 <登记收据> --backup <备份名> --snapshot-sha256 <备份外存hash> --target <全新目标名> --authority-id <原权威UUID>
```

执行后恢复到**此前不存在的新目录**，不覆盖、隔离替换或删除旧目录。原权威的版本、库存、
全部数据库事实、来源恢复世代与独立 guard 必须和备份一致；撤销、遗忘、unknown/发送/owner
账本任何新事实都阻断旧包。随后执行 `verify-restored`，成功仅是 `state_verified`，两个项目
持续停用；不复制源运行登记、PID 或自动生成新的运行 owner。

`lifecycle-verify-restored --target ... --authority-id ...` 也重新停写/核原当前权威。
尚无通过固定四产品功能验收与当前批准后启动恢复目标的公共合同，本层**没有 activate/restart
命令**，不根据备份自身 guard 或日志可查授予激活。此缺口不能以合成验证结果替代。

`lifecycle-prepare-update` 额外要求 `--candidate-manifest`、`--compatibility`、`--update-id`
及 `--backup`。先停写并备份，再核 schema 兼容性，输出禁用的代码选择和 COMMITTED 事件。
`lifecycle-rollback-code` 使用同样版本/兼容性参数，只选择代码，**不恢复数据**。
它们不自动执行新镜像、修改 Compose、迁移或重启；`selected-code.json` 不等于已运行版本。

首次全新安装完成初始化后可先留一份基线备份，再做候选更新；初装备份不代表可无条件恢复到
未来状态。原当前权威丢失的灾难恢复，缺独立保管的新批准/撤销禁止集合重建接口，本工具拒绝，
不猜测安全世代，不用旧备份内自洽的 guard 放行。独立故障域/加密保管仍另行配置。

## 本地验证与官方依据

`python -B tests/deployment/recovery/run_verification.py --output tests/deployment/recovery/.runtime/<新报告>.json`
执行旧恢复回归、公开 CLI/真实合成进程演练与 Docker inspect/命令契约测试；后者明确是替身，
不是 Linux 容器。Windows 不提供标准库目录 fsync，报告保持 `directory_fsync=false`。
四真实产品恢复、容器构建/权限/停止与实机 NAS 验收均未完成。

Docker 行为按官方 [自定义停止信号](https://docs.docker.com/reference/cli/docker/container/kill/)、
[更新重启策略](https://docs.docker.com/reference/cli/docker/container/update/)、
[Compose 服务/标签定义](https://docs.docker.com/reference/compose-file/services/) 核对。
