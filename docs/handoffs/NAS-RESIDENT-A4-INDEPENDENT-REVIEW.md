# NAS resident A4 独立阻断审查

日期：2026-09-26。结论：**固定代码未见剩余静态阻断；现场验收未开始，`release_ready=false`。** 本审查没有连接或写入 NAS，没有运行安装、容量或 A4 验收脚本，也没有读取私有凭据内容。

## 固定对象和范围

- A2 容量守护：基线 `0fd6201a5633a9a5a9e8292f3bd3eadbd61a25e9`，初交 `f5cac49388ec04e68791976969400edb89ad8641`，复审提交 `1edf794e8ee7936f41e97fd01c6e3190c2d15bdd`。审 `guard.py`、配置 schema、systemd 219 unit、隔离夹具与交接。
- A1 真实输入与单次 runner：基线 `1fcd3fa`，初交 `0556286196084b79061e8480d2a69753acafa7f6`，复审提交 `c1799cdf81c833933dab279c0c562db3b82b842f`。审五个跟踪文件与私有输入的**哈希**，不输出凭据。
- 固定本轮私有输入哈希与 runner 常量一致：plan `1f2406873734c0deb7fa3108af3cd6df1efd0eea306401549810aa878b565412`、manifest `8762c8d6ce660868cdd79374960139c64013586d63eb271484d51b541e8a3ab0`、site `a9cf63b1f5eea2377cf9ffe7408c423d833564b4459c9b5373e9975e406b1586`、OBS settings `c561e85d4ce0c506b76a9e31cc8a42d9d38a7d8638acd07c245c1d83aabda6f8`。v2 capacity config 为 `c0cf7314a5bd987182728b2679ba21cde9d79d0dfedf5d2f88ae0ab235c16b26`，已渲染 unit 为 `77620ca2c757484a09288bf52a427a94f316757219987bb3a4e8c914181d1f63`。

## 审查结果

1. **A2 停机时限已修。** 初交 unit 的 `TimeoutStopSec=180s` 可能早于九个 ID 的逐项 restart 禁用、TERM 与读回。复审版限制 Docker 单调用 10 秒、批量 inspect 15 秒、TERM 等待 120 秒，计算外部等待上界 530 秒；unit 改为 600 秒，另留约 70 秒处理收据与调度。systemd 219 源码的 `SERVICE_STOP_POST` 超时分支确实会终止停机控制进程，因此仍须现场读回，不能用静态计算代替。见 [systemd v219 service.c](https://github.com/systemd/systemd/blob/v219/src/core/service.c)。
2. **A2 身份与容量门槛已修。** 配置/schema 强制每卷可用空间至少 20 GiB、部署树预算至多 20 GiB；`arm` 锁九个完整 ID，并核项目、服务、首/终 Compose 标签、镜像 digest 和每服务完整 bind 的源、目标、只读位。失败路径先锁存，再只对已锁 ID 禁用 restart、读回、发 TERM、确认退出；新 ID 只报 `unconfirmed`，不按项目名停替代物。
3. **A1 失效收束已修。** 初交的 60/30 秒 A2 停机调用会抢先中断 600 秒的 systemd 停机链；复审版串行等待 unit stop 最多 720 秒、确认 inactive/无 Job，再调用 A2 fail-close 最多 660 秒并核其状态，最后做精确 ID 回读。任一步不确定即停止后续写动作并要求人工停机。普通 CLI 超时只向 CLI 主 PID 发 TERM；即使主进程退出，也把 Docker 副作用标为未确认，不自动发起第二停机动作。回退不再用 `docker stop --time 30`，改为显式 TERM 和限时读回；Docker 官方说明前者超时会发 SIGKILL，见 [docker container stop](https://docs.docker.com/reference/cli/docker/container/stop/)。
4. **A1 本机目标与来源已修。** runner 清除继承的 Docker/Compose 覆盖项，固定本机 Unix socket，剩余服务 Compose 明确传 `-p` 两个固定项目名；A2 配置改用已解析的 NAS Docker 实体路径，避免 `/usr/bin/docker` 符号链接被 A2 拒绝。runner 核固定 unit SHA、无 drop-in 与无需 daemon reload，核首次 Platform ID/Compose 标签、最终导出、20/20 GiB 与完整 bind，且在报告 ready 前再次核原 300 秒 origin 未过期。Docker 环境覆盖的依据见 [Docker Compose 环境变量说明](https://docs.docker.com/compose/how-tos/environment-variables/envvars/)。

两次固定提交的 diff whitespace 检查通过，工作树干净。A2 隔离夹具 18/18 通过为执行者和协调者所报告；A1 离线九 digest prepare→OBS configure→A3 export、capacity schema、CLI/compile 通过为执行者所报告；A4 未重复运行这些检查。独立审查依据是固定源码、提交差异、配置/输入哈希和现有交接。

## 现场前置与未证实范围

- 协调者已只读核 DSM 219 的 `DropInPaths`、`Job` 空值和 `NeedDaemonReload=no` 输出格式；新 unit 当前尚未安装，安装后须对**该 unit**复核有效状态、watchdog 与 `ExecStopPost`，再做受控阈值、崩溃和九 ID 停机收据验收。内核 fsync 无 Python 硬超时，Docker API 失联时 A2 无法保证停机。
- 首次启动到 A2 `arm` 之间必须由协调者现场值守。CLI 超时、未确认退出或进程被外部强杀时，runner 保留现场并报告人工停机，不声明自动收束。原 300 秒来源不能重发或延长；超时需重新审查，不盲重试。
- 运行容器的 `config_files` 标签应为 Platform 的 first export、其余八服务的 final export；Dockge 目录内保存的两份 Compose 是**另行接管的副本**，需与 final export 逐字节一致，不能宣称现有容器由 Dockge 创建。
- 私有未跟踪 A4 验收包在 `C:\YOKI\Codex\tianshu-peiban-bot\.runtime\nas-resident-a4-acceptance-2026-09-26`，含 `README.md`、三个只读/低影响脚本及 `KIT-SHA256.txt`。它将核九容器、六网络、端口/TLS、一次管理员登录登出、真实四源 JSONL→Guard/Loki 对账与 Dockge 副本；**尚未执行**。真实模型、历史数据、故障注入、长期留存和 `release_ready=true` 均不在此轮已证范围。
