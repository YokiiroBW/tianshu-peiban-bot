# 十服务容量 guard 停机：Docker 命令非零退出的只读分析

2026-09-28。范围仅为本地源码、既有部署说明和协调者转述的现场元数据；本分析未连接 NAS、读取现场日志或修改 guard。现场读数均标注为“协调者报告”，不当作本任务独立实测。

## 现场事实与时间

| 项目 | 已知证据 | 限度 |
| --- | --- | --- |
| 停机锁存 | 协调者报告旧 `failure.json` 为 `reason=docker_command_failed`，`time=1790574043.48`，即北京时间 13:40:43；十服务约 13:42 停止。事件发生在本轮镜像更新前。 | 收据未记失败的 Docker 子命令和退出码。 |
| 最后健康心跳 | 协调者报告 `time=1790571945.62`，即北京时间 13:05:45，状态 `healthy`，可用空间约 459 GB。两时间戳相隔 2097.86 秒（34 分 57.86 秒）。 | 未取得完整 unit 生命周期或主机时钟记录，不能解释心跳空白。 |
| 停止证据 | 协调者报告保全了旧 state 全部 8 份 `stop-*.json`；前几份 `unconfirmed`，最终 `stop-4900...` 为 `stopped`、`errors=[]`，十个锁定 ID 均记录退出并关闭 restart。 | 早期不确定收据须保留，不能用最终成功记录删除或改写。 |
| 事后 Docker 读回 | 协调者报告使用 guard 配置的同一 binary 执行 `Docker.snapshot()`：407 个容器、0.381 秒；分类无冲突，原 `armed.json` 十个 ID 均未替换，状态均 `exited`、restart 均为 `no`；可用空间约 460 GB。先前直接 SSH `docker ps/inspect` 也曾快速成功。 | 证明事后 Docker API 可读，不证明故障时的命令为何非零。 |
| 其他现场状态 | 协调者报告完整冷备 R、旧 state、unit、config 已保全；unit 事后 `inactive`、退出码 0。另一次由主任务 Python SSH 包装器发出的 `docker ps` 曾在 90 秒超时，直接 SSH 命令随后快速成功。 | 包装器超时不等同于 guard 的失败代码，unit 退出码也不解释首次触发。 |

## 源码路径与判断

`ops/resident_capacity/guard.py` 的 `Docker._run` 将进程创建失败或 `subprocess.TimeoutExpired` 映射为 `docker_unavailable`；只有 CLI 返回非零才映射为 `docker_command_failed`（约第 226–235 行）。因此这次锁存表明**某次 guard Docker CLI 调用非零退出**，不能据此称它已超时、Docker daemon 不可用，或归因于磁盘/网络。`_run` 未记录子命令、退出码和时长，现有收据不能再区分失败点。

每次 `sample` 先运行 `docker ps -aq --no-trunc`，再对枚举所得的**全宿主机容器 ID**执行一次批量 `docker inspect`，随后才核验十个锁定服务和容量（约第 237–253、519–526 行）。若无关的临时容器在这两步之间被删除，批量 `inspect` 可能非零退出；这是明确存在的采样竞态窗口，**尚无证据证明它就是本次触发源**。2026-09-27 供应商部署记录曾把同类竞争列为疑点，明确未证实根因。不能简单只枚举十个服务，因为 guard 还要发现其他容器对受管数据根的挂载冲突。

`run` 在采样失败时先写 `failure.json`，再执行 `fail_close`；后者只针对旧 `armed.json` 中的精确 ID 关闭 restart、发送 TERM 并读回退出（约第 529–620、633–651 行）。锁存后的重启可走 `latched_failure` 分支，若停止确认则返回 0（约第 638–640 行）；故最终 unit `inactive/exit 0` 与保护停机相容，不能视为原故障已解除。Docker 单次调用上限 10 秒，批量检查 15 秒；服务有 30 秒 watchdog 和 700 秒停止窗口（`tianshu-resident-capacity.service.in`）。这些时限也未解释约 35 分钟无新心跳，应另核同一 state 代次、unit 启停/重启时间线和主机时钟，避免推断未见的过程。

## 恢复判断

目前没有证据要求在受控恢复前先修改 guard 代码。协调者转述的最终停止收据、同 ID 事后读回、同 binary 快速完整快照、容量读数、冷备和旧状态保全，支持进入既有恢复流程的下一道门槛；不支持直接复用失败 state 或盲目重启。若**只恢复原版本**，旧 unit 停止并完成 `ExecStopPost` 后，应在监督窗口内仅对原十个 ID 恢复 `unless-stopped` 并读回，按批准顺序启动，以新空状态代次重新 arm，并要求新旧 `armed.json` 十个 ID 完全一致。依据为 `ops/resident_capacity/README.md` 的“同 ID 显式恢复”流程。

协调者现正执行的 Platform/Companion 镜像更新属于**受控新部署代次**；计划内替换 ID 应与新出口、镜像、Compose 标签、挂载及容量清单逐项核验，不适用旧 ID 必须相同的恢复条件。新代次仍须保持旧失败 state 与全部停止收据，验收新 `armed.json` 中十个获批 ID、unit active、guard `status=ready` 及持续心跳。若 Docker 再次非零、出现计划外 ID、停止收据有未解释的缺口，或约 35 分钟心跳空白对应尚未解决的 unit/watchdog 异常，应暂停接受新代次。本分析不替代新镜像的部署验收。

## 最小后续改进

在不改变 fail-close 条件的前提下，为 Docker 调用错误保存**受限诊断字段**：固定阶段枚举（`ps`、`batch_inspect`、`update`、`inspect_one`、`term`）、退出码或超时标志、单调耗时、批量 ID 数量。使用现有私有状态目录和原子写入；不保存原始 stdout/stderr、命令参数、完整 ID、挂载路径或应用日志。这样可区分 `ps` 失败、批量 `inspect` 竞态、停止阶段错误及超时。应以隔离假 Docker CLI 验证非零/超时记录与原有锁存、停止行为；先取得实际失败阶段证据，再考虑有限重采样，不能跳过全宿主机身份与挂载检查。

本报告只审查故障路径；不宣称已定位原始 Docker 非零原因，也不宣称新部署已验收。
