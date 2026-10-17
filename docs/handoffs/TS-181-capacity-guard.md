# TS-181 容量 guard 故障收口

本轮只修容量 guard 的全局 inspect 成本、短暂慢读处理和首因锁存生命周期。未改节点执行器、部署工具、systemd unit、容量阈值或保护对象。工作分支为 `codex/ts-181-infrastructure-live-validation`；交付提交为本文件所在提交，精确 SHA 随交接回复回传。

## 基线与范围

- 开始时检出干净，原 HEAD 为 `c622b792396721226eb88479e00b20b226caf675`；确认它是 `11acd10f1eadf3b4a4001b190f5d697fc2261535` 的祖先后，仅用 ff-only 更新到该集成基线。
- 本轮修改 `ops/resident_capacity/guard.py`、同模块 README、既有 `tests/resident_capacity/` 和本交接文件。未写 `deploy/infrastructure/`、根 CURRENT 或任务板，未触碰其他工作树。
- 本地没有连接 NAS、读取密钥或真实配置正文、操作生产容器或推送。

## 事实与根因

协调者提供的脱敏 `guard-detailed-evidence.json` 显示：04:20:35 CST 健康采样的全 NAS 387 容器 inspect 用时 0.329988 秒，部署逻辑字节 `2549553483`、最低空闲字节 `79919276032`。04:20:56 的有效待发布故障回执保存了 `docker_unavailable`，对应同一 387 容器单次 inspect 超时 15.017968 秒。后续正式故障/停机回执变成 `service_exit`，10 个锁定 owner 最终退出且 restart=no。

原调用链为 `run -> sample -> snapshot -> 全局 ps/单次全量 inspect`。单次 batch timeout 直接进入 fail-close，并在 main 进程剩余 watchdog 时间内串行执行可能较长的关闭流程。若原子 latch 写入发布前中断，仅留下完整私有 `.tmp`，已有 StopPost 会重新生成 generic `service_exit`，丢失首因。现有证据能证明 pending 回执及后续覆盖行为；没有首轮退出的 signal/syscall 记录，不能断言具体是哪一个 fsync 或 watchdog 信号中断。

## 实现

1. 每轮仍完整枚举全部 ID（原 4096 上限），冷启动和新 ID 的 inspect 分为最多 32 个。仅在同一 Docker 对象内缓存非 resident 项目的创建元数据，且把 status/restart 清空。所有 resident 项目以及 armed 锁定 ID 每次都重新读取；新 ID、替换、重复 owner 和外部部署目录挂载仍参加原分类/保护。失败采样不会发布部分新缓存，完整结果才清除消失的 ID。没有磁盘缓存。
2. batch inspect timeout 允许原有的最多一次完整重采样；所有分批、失败时间和重试共同消耗原 25 秒 deadline，以及运行循环已有的 poll/notify/测量/持久化预算。没有延长 10/15 秒命令限制、30 秒 watchdog 或 TERM 限制。`ps`/创建进程/非法元数据故障不会新增重试。
3. 原 unit 的 `NOTIFY_SOCKET` 与 `WATCHDOG_USEC=30000000` 环境下，run 只保存首因并退出，由既有 ExecStopPost 独立停机；保存失败仍退出给 StopPost。直接 CLI run 继续同步 fail-close，配置不匹配继续使用 armed Docker 身份。未添加配置或开关。
4. 首因读取/恢复仅接受当前已验证私有 state 中 `failure.json.<32hex>.tmp`，验证有限 JSON 结构、字段、文件类型、属主、私有权限及原有 64KiB 读取上限。取最早有效记录，正式 latch 不重写，pending 保留作证据。run 启动和 status 同样识别有效 pending，不会在尚未发布 canonical latch 时重新 READY/healthy。stop 回执 `reason`/trigger diagnostics 保留首因，新增 `invoked_by` 和 `pending_failure_recovered` 区分本次调用。

创建字段缓存的依据为固定 Docker 24 API 中容器 update 的资源与 RestartPolicy 范围；不是把可变运行状态视为不变。参考 [Moby v24.0.2 API 定义](https://github.com/moby/moby/blob/v24.0.2/api/swagger.yaml)。本次真实部署使用 Docker 24.0.2；后续更换为允许原地改变标签/镜像/挂载的容器管理后端时需重审这项假设。

## 调用方与验证

已按范围检查原调用方：生产 run 由本模块 unit 的 ExecStart 启动，ExecStopPost 独立调用 fail-close；`ops/resident_install/run_candidate.py` 的安装/回退路径等待原 unit 的 StopPost 完成后再按既有规则做显式 fallback；`deploy/infrastructure/resident_worker.py` 使用 arm/systemctl。未改变这些调用方。原 unit 的 `WatchdogSec=30s`、`TimeoutStopSec=700s`、Restart 行为和 stop 命令保持原样。

使用只读复用的本地 Python venv：

```text
C:/YOKI/Codex/tianshu-peiban-bot/.runtime/infrastructure-20261016/acceptance-venv/Scripts/python.exe -m unittest discover -s tests/resident_capacity -v
```

最终 **67 项通过，10.253 秒，无跳过**，其中 17 项为本轮新增生命周期/缓存回归。覆盖 387 全局 ID 到稳态仅刷新 10 owner、同一 Docker 对象的 owner 状态/策略/镜像/挂载变化、新 ID/替换/重复/外部挂载、失败缓存发布、暖对象 fail-close 的真实合成 CLI 退出确认、真实合成子进程一次 timeout 后恢复、共享原 deadline 的可恢复/不可恢复采样、无虚假 heartbeat、pending 首因/权限/结构/状态目录范围、pending 下 managed/standalone 重启和 status、managed latch 写入失败与配置变化、原 unit StopPost 委托。原 50 项的保护与部署契约也全部通过。

开发期间先有两处旧测试仍断言单一 407-ID inspect/timeout 从不重试，已按新行为更新；新增一个非法 JSON 夹具最初因行数减少触发合法完整重试并成功，修正为等行数非法 JSON 后通过，再跑上述最终 67 项。`git diff --check` 通过。

## 限制与下一步

本地运行环境为 Windows；POSIX 属主/权限拒绝分支用隔离属性夹具覆盖，未冒称已经跑过真实 Linux root、systemd watchdog 或 NAS。下一步由协调者对固定提交独立导出，在 NAS 隔离 Linux 范围验证，并按已批准生产维护流程上线 guard 文件。本地尚未部署。

Python deadline 不能中断阻塞内核文件系统调用，已有 watchdog/独立 StopPost 仍是后备路径。真实容量越界、owner 改变、Docker 持续不可确认仍会锁存停机；当前后续空闲低于 20GiB 的问题不会因本修复放行。旧回执和旧 state generation 的保留/新一代受控恢复按模块现有说明执行。
