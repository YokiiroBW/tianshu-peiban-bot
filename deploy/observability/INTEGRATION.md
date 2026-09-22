# DEP-A / DEP-C / DEP-D 接口

发布清单schema唯一归DEP-A。本包已只读消费固定 `e86d622a813f116b059b62b820cbd1342722042d` 的形状：产品 `source{repo,commit}`、diagnostics/v1 manifest/文件原字节hash、四个 `category=logs,mount=true,kind=directory` 卷。`mount=false` 的DB/WAL/guard条目完全不读取、不挂载。

`configure.py`输入和命令见README；`settings.example.json`是本包私有设置，不是另一份跨产品发布合同。生成后完整Compose归本包所有，DEP-A只引用入口并独立管理该项目，不复制服务定义或混入四产品业务网络。知识进程日志可放memory同一目录，允许service=memory-knowledge；需要独立第五目录时双方另扩适配。

| 资源 | 主机来源/要求 | 容器落点与权限 |
|---|---|---|
| 四产品日志 | 发布清单logs卷，独占非嵌套，UID10001可读 | Vector/guard的`/sources/<product>`，只读 |
| 合同 | 发布清单diagnostics/v1，原字节全匹配 | guard `/contracts/diagnostics/v1`只读 |
| Vector | 本包data/vector | `/var/lib/vector`独占读写，buffer+checkpoint |
| Loki | 本包data/loki | `/var/lib/loki`独占读写，WAL/chunks/index/compactor/删块标记 |
| Grafana | 本包data/grafana | `/var/lib/grafana`独占读写，用户/告警配置 |
| Prometheus | 本包data/prometheus | `/var/lib/prometheus`独占读写，指标30d/2GB上限 |
| 守卫 | 本包data/guard | `/var/lib/guard`独占读写，完整性/告警SQLite |
| 容量观察 | 本包Vector/Loki存储目录 | guard `/capacity/*`只读，只取文件系统水位 |

服务均10001:10001、只读rootfs、no-new-privileges、cap_drop ALL。内存限额总计3.5GiB，只是候选资源约束，不是实测占用或NAS余量承诺。只有guard连接storage网络，其余只有observe，均internal。无现有control-hub/AssetLibrary/Chat Audit网络、目录或服务引用。

TLS服务端SAN：obs-guard、obs-loki、obs-vector、obs-prometheus及显式Grafana主机名。guard→Loki另持client.pem/key，具有ClientAuth用途，由Loki的client-ca.pem信任。入口通过内存OpenSSL握手核链/有效期/SAN/密钥/EKU，不签生产证书。测试工具的“DEP-B SYNTHETIC TEST ONLY”证书仅用于夹具。宿主loopback查询还需guard证书含该访问名/IP。两端口只绑127.0.0.1，均显式选择；本号不占用NAS端口。

四凭据互不相同、32–128位URL-safe随机字符串；Vector仅writer、Grafana仅query及初始化密码、Prometheus仅metrics，guard读取三个角色。Loki的客户端私钥不挂给Prometheus，后者通过guard受限的`/backend-metrics`取Loki指标。无需产品业务token、真实账号或模型。

DEP-C未来需备份五数据目录及包版本/配置引用，尤其Vector位点与缓冲配套，Loki WAL和Compactor标记不漏。本包备份不生成应用删段许可，恢复后先对账再信观察水位。DEP-A当前四产品volume枚举不足以描述这些观测存储，需协调扩清单；本号不改共享schema。

DEP-D最小公开命令为README中的 `reconcile.py`：输入合成期望JSONL、显式只读目录、HTTPS查询地址、CA、query凭据文件、采集时间范围、报告路径。输出status、三份unique计数、两处缺失/多余、hash冲突、重复、序号缺口、非法/未闭合行，应用回收授权始终false。任何失败exit1，不回显token/坏行原文。调用者应和release_id、源提交与合同hash绑定存档，不能把日志报告当四产品业务验收。

`run_native.py`为真实Vector/Loki原生进程恢复证据；不代表四产品、Grafana或Linux。`run_stack.py`只接受本机unix/npipe Docker端点、Linux daemon、新建随机项目；不操纵远程Docker。缺环境返回dependency_missing，不伪装通过。实际版本、证据及限制见专属handoff。

最终词汇已重绑协调验收后的TS104–106固定提交（见baseline-commits.json），集合与初始基线相同。仍须阻断：Linux镜像digest与全栈启动、Grafana实际装载/权限、物理满盘、Linux到期删块、通知/全栈外探活、应用产生水位和安全回收、30天容量/24小时观察。记忆候选消费者和Chat Audit归档缺口未被本包修改。
