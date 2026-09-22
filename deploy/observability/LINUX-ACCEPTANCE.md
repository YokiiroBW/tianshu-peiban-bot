# DEP-I：隔离日志全链验收入口

入口为 `tests/deployment/observability/run_linux.py`，默认只生成计划，**不启动容器**。
固定消费 DEP-G `880c2c33ffc4c12b9267d38267c2dacde074890d` 的身份 schema；
`ceca645fbc60b68a28eb1fa4e942ecf8bab3ea1b` 的补充语义兼容：
`not_observed` 可以已有真实 image inspect ID，不要求为了取镜像ID启动日志栈。
本包随附 schema 和 planned 例子均为固定 Git 对象原字节，例子不是运行证明。

## 协调者准备与运行

先由 DEP-G 在新合成 scope 生成最终1.1清单、两个项目和 runtime identity，明确文件SHA256。
要求四核心容器已退出，容器ID与身份一致；五个日志容器尚未创建，五个持久卷为空、
UID/GID 10001、目录无 other 权限。已有四产品合成启动日志保留并成为 baseline，不能清空。
五日志镜像必须已准备，ID、RepoDigests与本机实际inspect一致；执行器不pull或build。
证书需包含 loopback `127.0.0.1` SAN、各内部服务名，以及配置的 Grafana 域名。
执行账户需能取得G的同inode锁、读配置/证书、写合成日志并为新录制器密钥设置10001所有者。
只有协调者安排这类Linux权限；本入口不安装宿主依赖、不改系统/设备。

在专用虚拟环境安装测试依赖（不要宿主全局安装）：

```sh
python3 -m venv /explicit/new/acceptance-venv
/explicit/new/acceptance-venv/bin/python -m pip install -r tests/deployment/observability/requirements-linux.txt
```

使用实际路径与已审核的身份原字节SHA256替换下列参数，不可把示例hash当实际身份：

```sh
python tests/deployment/observability/run_linux.py \
  --identity /explicit/synthetic/deployment/reports/runtime-identity.json \
  --identity-sha256 ACTUAL_REVIEWED_SHA256 \
  --output /explicit/new/evidence-plan

python tests/deployment/observability/run_linux.py \
  --identity /explicit/synthetic/deployment/reports/runtime-identity.json \
  --identity-sha256 ACTUAL_REVIEWED_SHA256 \
  --output /explicit/new/evidence-execution --execute-synthetic

python tests/deployment/observability/check_linux_evidence.py /explicit/new/evidence-execution
```

可选 `--bounded-tmpfs-probe` 只填满guard既有、真实挂载类型为tmpfs且最大64MiB的`/tmp`，
调用实际guard容量判定观察reserve拒绝、ENOSPC和恢复，最后只删除该探针自己创建的填充文件。
它单列 `bounded_tmpfs_capacity_probe`，**不提升**真实五持久卷ENOSPC、Vector满缓冲或源日志满额维度。

退出码：0=计划成功；1=校验/执行失败；2=短程场景结束但仍为partial。
没有任何路径输出release ready。新证据目录必须不存在；失败保留所有源日志、五卷、容器与证据。
重试需协调安排新scope；不会清理旧栈来凑“全新”。

## 边界与观测

仅Linux/amd64本地Unix Docker socket；拒绝Docker环境覆盖/远程context，解析后固定socket。
G/I/J共用 `<deployment_root>/.runtime-owner.lock` 的非阻塞flock，覆盖检查到最终证据落盘，
不unlink或替换锁。逐字节复核清单、inventory、两个Compose、配置、词汇与合同；实际inspect
复核完整bind挂载、只读权限、容器标签、镜像ID、进程UID/GID。其他容器绑定scope或其父目录即拒绝。
只控制obs五owner；不start/stop core，不使用down、rm、prune或全局kill。
最终stop保留退出码/OOM，137或OOM不验收；其他退出码也不宣称九writer正常停写/可备份。

本轮经协调授权补guard正常关停：SIGTERM/SIGINT处理器只置标志，主生命周期停止准入、
取消网络等待，再以同一10秒预算join监听/monitor/全部HTTP线程；owner完成后才close backend。
monitor持久账本和告警库在自身finally内关闭。超预算返回2及`shutdown_unconfirmed`，
仍有owner时不抢先close backend；解释器随后退出是异常关闭，不能作为恢复备份许可。
Linux PID1信号效果仍需实际验证。G必须把日志包source重绑本次DEP-I固定提交并重新生成bundle；
执行入口会拒绝部署代码与本次guard/lifecycle/query等实际字节不一致的旧包。

每批四产品各100条、同instance连续序号；保存原baseline、逐批全集、源落地全集和检索全集。
用既有三方对账检查缺失、意外项、冲突、内容差异与序号空洞。HTTP查询成功不是通过条件。
断Loki后必须观察Vector真实buffer指标和磁盘buffer文件，再重建Vector、重连全量复查；
停止Vector期间rename旧段并写新段，再重建和全量复查。正常事件没有采样。
无效canary必须观察丢弃计数增长且中央无canary，源文件保留；有效事件遗漏仍失败。

真实Grafana API检查两个datasource健康、五panel配置、匿名拒绝、Viewer读/禁止写；
Prometheus通过Grafana代理实查guard/vector/loki三个up目标。它不证明浏览器视觉渲染。
Grafana配置的contact point只发内部 `obs-guard:18081` 录制器，无发布端口或外部URL；
录制器持久化封闭告警名/状态/接收时间，拒绝额外敏感字段。实际规则触发的canary告警是通过条件，
不把手工POST接收器算告警投递。新密钥只留受保护合成guard卷，不进入报告。

API/指标参考：[Grafana webhook配置](https://grafana.com/docs/grafana/latest/alerting/configure-notifications/manage-contact-points/integrations/webhook-notifier/)、
[Vector缓冲模型](https://vector.dev/docs/architecture/buffering-model/)、
[Vector buffer指标](https://vector.dev/docs/reference/configuration/sinks/vector/)。
接口版本的实际Linux兼容性仍待运行，不能由文档或静态代码证明。

## 保留与容量门禁

原正式配置严格保持Loki 720h、compactor延迟2h、Vector磁盘4GiB/block。
旧 `run_retention.py` 是独立原生加速实验；它的旧证据不合并到本次report。
`production_30_day_retention`、`accelerated_ttl`分别默认未执行；短程报告不会自动升级。
`physical_enospc`、`vector_buffer_full`、`disk_watermark_rejection`指实际持久卷维度，
本入口不填宿主磁盘，当前未接入独立有界持久文件系统；由协调准备后另增精确运行证据。

`capacity.py`提供显式实测速率/膨胀/安全系数计算；`acceptance.capacity_gate`补充拒绝状态、
源目录耗尽天数和可操作阻断。未测流量与Loki膨胀不能声称容量满足30天。
当前没有应用持久确认/安全回收合同，故应用容量门禁始终blocked：在源预算前停止新工作准入，
保留源段并扩容或补齐回收合同。Loki204、查询命中或位点推进**均不授权删段**。
精确合同建议继续引用 `RECLAMATION-PROPOSAL.md`，本任务未改根合同或实现回收。

## 本地验证范围

见 `docs/handoffs/DEP-I.md` 与 `tests/deployment/observability/evidence/dep-i-local/`。
Windows本地验证只证明计划、拒绝边界、合同原字节、证据一致性及既有TLS/对账回归。
Linux flock、实际Docker组件链、实际Grafana告警、tmpfs探针、NAS均需协调运行。
