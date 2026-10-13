# Dockge 旧栈与冷备清理 — 2026-10-14

用户授权删除 Dockge 中不再需要的天枢冷备。本次按实际容器、挂载和当前部署逐项盘点后清理，没有执行全局 Docker prune，也没有清理镜像。

## 已删除

| 对象 | 数量 |
| --- | ---: |
| 已停止的天枢测试、演练和恢复 Compose 项目 | 73 |
| 上述项目的停止容器 | 420 |
| 无使用者的项目网络 | 269 |
| 测试 Prometheus 独占匿名卷 | 2 |
| 已被较新备份替代的冷备 | 34 |
| 已退役的隔离运行目录 | 50 |

84 个备份及运行目录的分配空间合计 101.65 GiB；`/volume2` 可用空间实测增加约 101.71 GiB。后者受 NAS 其他活动影响，不作为精确删除字节数。

## 当前保留集

保留以下最近两份完整冷备，均有 37 库 `offline_restore_verified` 回执，清理后原 manifest 保持不变：

- `/volume2/tianshu-v2-resident-updates/memory-phase1-20261014-release-r1/cold-snapshot`
- `/volume2/tianshu-v2-resident-updates/platform-log-recovery-20261014-release-r1/cold-snapshot`

历史 NFO 原件备份保留于 `/volume2/tianshu-v2-resident-updates/media-legacy-nfo-20261013-release-r1/nfo-update/original-nfos`。

仍被 AstrBot / astrbot-ops-pro 使用的五个目录保持：`/volume2/Dockers/tianshu-v2-validation/wave1-20260923a/scope-{c,d,g,i,j}/deployments/source`。正式服务的数据、配置及镜像保持。

**旧交付文档中的冷备路径仅代表当时的验证结果，其中部分已按本次授权删除；当前可用冷备以本条保留集为准。** 删除对象的回执和 manifest 副本已留存于本次 NAS 记录的 `removed-backup-metadata/`。

## 清理期间中断与恢复

首次执行在删除前发现另一会话已完成 Memory phase1 部署，因此停止并重新盘点，最终按最新部署选择保留备份。

第二次执行完成所有计划删除后，容量守护记录 `docker_command_failed` 并保护性停止十个 resident 容器；原清理结果如实记录为 `requires_inspection`。批量 Docker 删除期间发生查询失败，未取得具体 Docker stderr，不能进一步断言底层原因。守护代码和停机策略未修改。

已按最新部署配置恢复服务并重新启动守护。停机期间 Gateway 来源过期，使用既有同入口、同 owner、同 route 的恢复流程重建 Gateway；镜像不变，没有新增权限。此前运行的 80 个服务名称及镜像全部恢复，Gateway 容器 ID 因上述恢复变化。恢复后同步两份实际 Dockge Compose 展示配置。

恢复保留 Platform `9219d31359829ed130da4062e23c38ab2f949769`、Gateway `0a6b0099149b4b7dcad4c165f9b1972d091eed08`，以及另一会话刚部署的 Companion `6ea390a01e5f4d5a55934b4182d8cd772ac588d2` 和 Memory `c4fe39ae8b1b649a975806be77a4a6f26fbb781c`。

## 最终核验与证据

- 五个核心服务实际 TLS `/health/ready` 全部 HTTP 200；守护 ready，覆盖十个服务。
- 网页 HTML 及三个入口静态资源均 HTTP 200。未据此宣称真实模型、QQ 或全部业务链路重新验收。
- 刷新实际 Dockge 页面后，73 组旧项目已消失；剩余 `tianshu`、`tianshu-v2-resident`、`tianshu-v2-resident-obs` 和独立 `tianshu-control-hub`。
- `tianshu-control-hub` 原有八个运行、一个退出的状态保持；其页面聚合“已退出”并非本次新增问题。仍运行的旧 `tianshu` 三容器也未删除。
- 两份保留冷备、NFO 原件和五个共享目录复核通过，最终失败项为空。

NAS 操作证据：`/volume2/tianshu-v2-resident-updates/cold-cleanup-20261014-r2/`，含精确 `plan.json`、删除前运行清单、原始失败结果、恢复结果和最终 `result-final.json`；原始记录没有被成功结果覆盖。

本机证据：协调工作区根的 `.runtime/dockge-cold-cleanup-20261014/`，含 `cleanup-plan.json`、`cleanup-final.json`、`service-recovery-poll.json`、`dockge-sync.json`、`verification.json` 及 `runtime-check-1791917660.json`。这些运行材料不入库。
