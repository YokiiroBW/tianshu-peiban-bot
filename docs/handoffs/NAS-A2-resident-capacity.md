# NAS-A2 resident 容量守护本地交接

日期：2026-09-26。状态：**needs_validation**（本地实现与夹具已完成，DSM unit、真实九容器和阈值注入尚待受控实机验收）。基线为主线 `0fd6201a5633a9a5a9e8292f3bd3eadbd61a25e9`，独立分支 `codex/resident-capacity`；本交接随同该分支的单次交付提交固定。

## 目标及变更

只为固定的 `tianshu-v2-resident`、`tianshu-v2-resident-obs` 两个项目提供短期常驻容量保护，不增加采集协议、日志回收、通用告警或产品功能。新增 `ops/resident_capacity/guard.py`、严格配置 schema、systemd 219 unit 模板和操作说明；隔离夹具位于 `tests/resident_capacity/test_guard.py`。未改产品、已有部署模块、项目仓库或 NAS。

- 配置根精确为 `/volume2/tianshu-v2-resident`；两项目和九服务白名单在代码中固定。因 A1 使用 `--project-directory`，三份 Compose `working_dir` 标签均须等于部署根，`config_files` 则分别为真实 first/final 外部路径。九镜像 digest 与每服务**完整** bind 的 source/target/read_only 均须精确配置，不能用同项目名或一个根内 bind 替代。`arm` 只在九服务同时运行、`unless-stopped`、镜像/标签/全部 bind 一致且容量安全时锁九个完整 ID。未武装时 `fail-close` 不碰首启 Platform。
- 每轮仅扫描部署树文件元数据，按逻辑字节核整体预算**至多** 20 GiB；对树内每个文件系统及显式 `free_paths` 核可用空间**至少** 20 GiB。配置可设更严格阈值，不能弱化。源日志不会被读取、删除、截断或降低完整性。预算/余量、扫描、Docker 身份或读回失败即锁存。此轮不接 Collector/Vector 指标门禁；四源联合查询留给实机验收。
- 锁存先写 `failure.json`，然后**仅**对九个已锁 ID 禁用 restart、读回，再发 SIGTERM 并轮询退出；每次 `stop-*.json` 保留状态。新 ID、同名替换或归属冲突不自动停，报 `unconfirmed`。Docker API 失联、TERM 不退出或收据无法写入都不得报告已停；从不发 SIGKILL。
- unit 模板 `Type=notify`、`WatchdogSec=30s`、`Restart=on-failure`、`StartLimitInterval=0`、`ExecStopPost=fail-close`，并在 `pkg-ContainerManager-dockerd.service` 后排序。TERM 上限收窄为 120 秒，Docker 单命令 10 秒、批量 inspect 15 秒；九 ID 两阶段读回加末次轮询的外部等待上界为 530 秒，unit `TimeoutStopSec=600s` 留 70 秒给收据/调度。守护健康检查后才通知 READY；进程崩溃或 watchdog 超时时由 systemd 的独立控制进程执行停机。锁存后再次启动只重试确认停机，确认完成即正常退出，不恢复项目。内核文件系统操作仍没有 Python 可保证的硬超时，现场必须核收据。

## 本地验证

- Windows CPython 3.13.11 执行 `python -m unittest discover -s tests/resident_capacity -v` 的显式解释器路径：**18/18 通过**，覆盖九 ID 武装、首启缺服务拒绝、Platform 首导出标签、项目工作目录与外部 Compose 文件目录分离、完整 bind 不可缺/错/改只读位、错误归属不受控、ID 漂移、强制门槛拒绝弱值、unit 超时大于计算上界、锁存禁重启/TERM/退出读回、同名替换不自动停、Docker 失联或故障收据写失败不冒称停机、配置漂移仍用原锁定 Docker 身份、heartbeat 新鲜度。初次用 PATH 中的 `python` 未找到解释器；成功轮使用 `C:/YOKI/ComfyUI_Anime/ComfyUI-zove4/python/python.exe`。夹具不调用 Docker 或 NAS。
- systemd 219 的 `StartLimitInterval`、`WatchdogSec` 与 `ExecStopPost` 仅依据上游源码/单元文本做静态核对；本机 Windows 无法执行 `systemd-analyze verify`。DSM PID1/systemctl 存在及实际 Docker unit 名称来自总控只读现场核对，本任务未连接该主机。

## 剩余风险与下一步

实际安装须由总控/A1 将固定源码复制到只读工具目录、以现场绝对路径生成 root-owned JSON 和 `/etc/systemd/system/tianshu-resident-capacity.service`，先实核两项目 Compose label、九镜像、Docker 二进制、Docker 数据卷所在路径与 unit 语法，再在九服务完整后 `arm`、启动 unit、核 `systemctl is-active` 和 `status=ready`。真实超阈值注入应改夹具/受控阈值，不填真实磁盘，并读回九 ID restart=no 与退出。新 unit 在 DSM 当前显示 `not-found/inactive`；还没有部署或启用。

九服务启动到 `arm` 的短窗口由总控现场有界监督，A1 进程异常按其自身精确 ID 流程收尾；本守护未武装时不能处理其 SIGKILL。Docker API 整体失联时无法保证停机，宿主其他写入可在轮询间跨过 free 下限；它不是文件系统硬配额。Dockge 本次仅导入固定 Compose 且禁自动更新/重新创建；人为绕过或替换容器需要人工排查。上述边界未实机验证前，不标记为无人值守或 release ready。A4 对固定提交做一次集中阻断审查后，随 A1 安装脚本由总控决定 NAS 验收，不在本任务直接写 NAS。

**显式恢复同九 ID**：总控先保存并复核原 `failure.json`/全部 `stop-*.json`，确认九 ID 均 `exited` 且 restart 为 `no`、故障原因已消除、容量安全、镜像/标签/bind 未变、没有替换容器。停旧 unit 并等其 `ExecStopPost` 完成，原状态目录完整保留；由操作者记录恢复决定，给**同一部署**建立新的空状态目录和相应 root-owned 配置/unit 路径，`daemon-reload`。按已审查维护流程只对原九 ID 恢复 `unless-stopped` 并读回，再按批准顺序启动；在受控短窗口内重新 `arm`、启动 unit、核 active/ready，并比对新旧 `armed.json` 的九 ID 完全相同。新旧收据一并保留。任一身份变化、停机不完整或预算证据缺失均不允许静默重武装；无需更换 NAS scope，也不删除源日志。
