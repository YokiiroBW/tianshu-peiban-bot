# A1-R1 首装执行器独立审查

## 返修提交定向复核（`2e4419066208ae02b71ceb91088fd8b7003cdfc9`）

对原提交 `da108cc` 的三项发现逐项复核后，**本地代码门槛均已关闭；未发现返修新增的首装执行阻断。** 这只表示固定 A1/A3 离线联合路径可进入协调集成，不是 NAS 激活或九服务运行验收。

- 首次 Docker 命令前，`_trusted_first_export` 从固定 A3 Git 对象 `8e381646cee06f37a61e80c16e9e2b50cd5984a9` 提取导出器及其本地依赖，以禁用 replace refs 与 Python 环境注入的子进程对同一 bundle 重导出，并逐字节比较整份锁及两份 Compose。首份锁的自报散列已不能让额外的 `privileged`、宿主 bind、命令、entrypoint、env_file 或 OBS 改动进入 Platform 启动。签发后的最终导出及 `finalize` 使用同一固定导出器和完整读回，再核首末两份 Compose 散列相等。
- `_clean_install` 现核四产品 data/logs 和五个 OBS data 目录的准确布局及空状态；旧 Platform 日志或 Loki 数据会在 Docker/迁移前拒绝。
- `_docker_occupants` 读全部 `docker ps -a` 的完整 ID，再 inspect 项目标签和实际/声明 bind。首启前不允许固定项目或占用部署根的容器；后续 `_platform_only` 只允许同一完整 ID、运行中、正确项目/服务/Compose 路径/镜像/精确 bind 且非特权的 Platform。正常 `run --rm` 一次性容器不需豁免；失败残留将阻断继续。

审查新增完整 diff 与首启顺序，`git diff --check da108cc 2e441906` 无空白错误。独立定向重跑 A1/A3 固定对象联合样例 1 项通过（含真实 bundle、固定 OBS 配置、签发后再导出及篡改拒绝），容器占用隔离样例 1 项通过；这两项仍没有连接 Docker daemon 或 NAS。A1 报告的本地 7/7 通过由其交接提供，未重复全套。A1 单独基线的适配器不支持 resident，必须与 A3 固定提交合并后才具备此候选入口；真实 Linux 权限、九镜像与容器、来源有效期及容量/日志联合验收仍归协调现场门槛。

## 固定范围与结论

只读审查协调基线 `a47b70c28e3d71dca80023a98e8b5360694b27e5` 至 A1-R1 提交 `da108cc959de7d197d1bb02a9171c84ac3b6200b` 的六文件差异，并按联合依赖 A3 `8e381646cee06f37a61e80c16e9e2b50cd5984a9`、OBS `a194fa7b527ac2da0f13b8c3e95e76a1b836b4b3` 核输入合同。**结论：首轮 `activate` 前存在一项 P1 执行输入信任缺口；首装空状态与唯一容器身份另有两项需在同轮收束的 P2 缺口。当前提交不能作为 NAS 首装激活依据。** 未操作 NAS、Docker 或产品实现。

## 阻断发现

### P1：首次导出的自报散列可掩护未审查的 Docker 权限与挂载

`ops/resident_install/install.py:234-333` 的 `_export_lock` 核锁格式、清单/库存散列、项目和服务名、九镜像引用，以及两份 Compose 文件是否等于锁里自报的 SHA-256；对 core 已准备的 `compose.json` 只比镜像，不比服务定义、挂载、特权、命令、网络、重启或 env 文件。`_exporter_ready`（372-393 行）只确认仓库有固定 OBS Git 对象、某个 `resident_export.py` 可执行 `--help`，也没有固定 A3 导出器字节。`activate` 随后将这份**首次导出的** core Compose 交给 `docker compose config`、Memory 迁移和 `up platform`（605-645 行）；第二份导出的相同散列检查在这些 Docker 副作用及 Platform issue 之后才执行（674-684、807-850 行）。

隔离定向复现：给首份 Platform 服务加 `privileged:true` 和可写 `/:/host` bind，并同步更新首份锁的 Compose 散列，其余 manifest/bundle/source/image 条件保持一致；`_export_lock(root, lock, manifest, prepared)` 仍返回成功，输出为 `{"accepted":true,"privileged":true,"host_root_mount":"/"}`。因此锁与文件自洽不能证明其来自固定 A3 公共导出器，首次启动可执行超出已准备 bundle 的宿主权限。任何 Docker 命令之前应从固定 A3 代码和同一 bundle 重算可信导出，完整比较锁和两份 Compose，或实施等效的全量服务配置及来源绑定；回归至少覆盖特权、外部 bind、command/entrypoint 与 env_file 篡改。

### P2：空安装检查遗漏日志和 OBS 可变状态

`_clean_install`（223-231 行）只要求四个 `data/<product>` 为空并无 `reports/resident-install`/`INCOMPLETE`；`verify_integrity` 的库存本来就不索引运行时日志和 OBS 数据。隔离定向复现向 `logs/platform/prior.jsonl` 和 `observability/data/loki/old` 写入旧内容、保持四产品 data 为空，`_clean_install` 仍通过。若复制 bundle 时目标目录已有这些内容，后续日志采集可混入旧事件，OBS 可以继承旧状态，而执行器仍称首次空安装。首装前应核全部可变 bind 目标及声明部署根的占用边界；不满足时在 Docker 和迁移前拒绝。

### P2：Platform-only 检查只证明当前运行服务，未证明唯一项目容器

`_platform_only`（550-575 行）对两个 Compose 项目只取 `ps --status running --services`，再取 Platform `ps -q` 与可选旧 ID 比较；它不核两个项目的 `docker ps -a` 全部容器、停止的额外服务或项目外对同一部署根的占用。`_projects_empty`（362-370 行）仅在本次开头按项目标签查空，不能替代 issue/finalize/人工续期前的全量读回。额外服务即使已创建又停止，也可通过当前检查；报告中的“only Platform”只能解释为检查瞬间唯一运行服务，不能证明首装阶段始终只有该容器拥有项目状态。应在关键副作用前后按确切项目/容器 ID 核唯一所有者，并核声明根未由其他容器持有；若发现额外容器，保留现场并停止后续发行/导出。

## 已核的正确边界与验证限制

- `prepare` 固定四产品与 OBS 提交、resident 项目和输入范围，要求独立私有凭据与单行管理密码，并经原 `initialize` 生成 scrypt 校验值；A1 单独基线尚不能接受 resident，须与 A3 合并后运行。
- Memory schema 1→2→3 使用固定 Memory 公共 CLI 与新备份名；Platform issue 使用公共 `local issue`，把短期来源只写入 0600 Gateway env 并更新 bundle 索引。首次与第二次导出 Compose SHA 相等、Platform ID 相等及剩余至少 120 秒的代码路径存在；无自动续签，`reauthorize` 有人工一次性标记。
- A1 交接列出的六项本地测试包含固定产品 CLI 和 A1/A3 Windows 联合样例，但没有执行 Linux `activate`、Docker Compose、实际九镜像、NAS 权限或真实容器身份。原联合样例使用合成证书/镜像字符串，且绕过了 Windows 不支持的 POSIX chown；不能作为此三项执行前置边界通过的证据。固定差异 `git diff --check` 无空白错误。

本报告只针对 `da108cc`。A1 已收到最小复现及同轮收束范围；任何返修须另固定提交、复审前置拒绝和 A3 联合路径，不把本报告结论自动转移到后续版本。协调者持有合入与 NAS 执行决定。
