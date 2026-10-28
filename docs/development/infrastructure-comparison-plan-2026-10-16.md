# 天枢基础设施功能对照与后续开发计划

2026-10-16 对照，2026-10-17 更新首批本地交付。范围为 NAS、其他节点连接、Docker 监控、Dockge 控制、资源监控和 OpenWrt 路由器。开发前结论为新版已有 NAS 部署和运行保障，面向用户的基础设施管理缺少主要连接器与页面；首批已在现有平台补齐只读基础，控制和历史等后续缺口仍保留。

本次用户补充路由器运行 OpenWrt，已有 SSH 密钥。优先完成 NAS 与 OpenWrt 的读取能力，再增加容器操作和部署。下方“对照基线”及原缺口表保留 10 月 16 日开发前事实；用户随后已授权并行开发，最新本地结果见下一节，不能把本地结果当作线上已部署。

## 2026-10-17 首批本地交付后的状态

三位实现者全部为 `gpt-6.1-sol / max`；协调者回收精确提交、退回发现的问题并独立验收。执行卡 TS-175 至 TS-178、逐项证据和失败保留见 [执行与验收记录](infrastructure-dispatch-2026-10-16.json)。本批代码已在隔离集成检出串行收敛，未推送或部署。

| 能力 | 本批已实现并本地验证 | 下一步仍需完成 |
| --- | --- | --- |
| NAS／Linux 节点 | 服务端 SSH 登记、网页选择和连接测试、后台连续采集、CPU／内存／逐挂载容量／接口速率、断线旧值和修订隔离 | 真实 NAS 权限／数值核对与上线接线；未提供的其他节点不虚构已接入 |
| Docker／Compose | 设计稿三栏结构与状态细轨；运行／健康分列；节点／应用过滤、分页、端口／挂载／指标／限定日志、Compose 期望与实际对照 | 真实 Docker／Dockge 路径与版本验证；完整日志继续使用既有持久日志链路 |
| OpenWrt | ubus 系统及 WAN／LAN 投影、接口计数和速率、无来源的逐终端流量明确未接入 | 用户确认 `root@192.168.31.1:22` 与现有密钥后，一次严格 host key 只读 SSH 尝试被拒绝认证；尚无实机读数 |
| 运行守护／交付 | guard 阶段诊断与原预算内有限重采样；固定 Git 源码包、明确依赖和调用配置；Windows 子进程先登记后运行，异常退出完整回收 | 派生镜像、Compose 挂载和容量预算的部署验证；没有修改真实设备 |
| 历史／告警 | 当前数据与新鲜度基础已具备 | INF-04：服务端保留、断点趋势、去抖／恢复和通知状态 |
| Dockge 控制／部署 | 首批只读清单和 Compose 对照已具备 | INF-05／06：明确对象的启停重启／更新、既有 guard 维护协作、变更计划、失败恢复及真实隔离 Docker 验证 |
| 更多节点 | Linux 读取器可以复用，Windows 已作为开发运行端验证 | INF-07：Windows 目标节点、硬件指标、代理与逐终端流量按实际设备逐项接入 |

基础设施专属浏览器 6 项通过；相关旧后端 175 项的适用结果通过（诊断测试输入纠正后复测 90 项、复用 85 项）。壳回归保留 4 项小屋标题既有失败、1 项既有平台条件跳过，已在固定旧基线复现；未修改文件的整库格式提示单列。真正的 HTTP → 执行器 → OpenSSH 使用本地合成节点，包含 150 容器和断线恢复；这不替代真实 NAS／OpenWrt、Linux 镜像或真实 Docker daemon 验收。

## 对照基线

- 规范以 [整体架构 V2](../architecture/tianshu-system-architecture-v2.md) 第 8 节、[需求 V2](../product/requirements-v2.json) 的 U57／U58／U59／U62／U97 为准；[完整产品稿第 14 节](../product/tianshu-complete-product-v1.md) 补充设备、代理和部署细节。
- 视觉以 [家庭设备图](../../output/ui/product-pages-v1/03-home-devices.png)、[Docker 三栏设计](../../output/ui/docker-management-v1/README.md)、已认可的 [蓝黄红灰细轨](../../output/ui/docker-status-v3/README.md) 和 [部署状态图](../../output/ui/remaining-pages-v1/22-deployment-flow-v2.png) 为依据。图片中的设备与读数是示例。
- 平台代码核对至运行提交 `187b383f9238e029d6a4b15b324bf106b1ce7960`；本地检出为 `worktrees/TS-170/tianshu-platform` 的交付提交 `a932c50f7a2e1d6034499df273cc99719c41852e`，两者差异仅交接文档。部署事实取自 [10 月 16 日交付记录](music-recovery-deployment-2026-10-16.json)，不是本轮重新实测。
- `projects/tianshu-platform` 协调检出仍在 `dffedb2`，`workspace.json` 部分状态和早期部署 README 也落后于交付记录。后续开发须从届时最新集成／部署提交起步，不能直接把旧协调检出当作线上版本。

## 设计与新版实现

| 能力 | 设计要求 | 已有实现与证据 | 后续缺口 |
| --- | --- | --- | --- |
| NAS 运行基础 | 部署、探针、日志、备份恢复、可解释故障 | 最新交付记录有五核心健康、十服务运行、容量 guard ready、Dockge 配置一致及备份恢复证据；根 `deploy/`、`ops/` 已实现部署和保障工具 | 这些运维工具尚未形成通用节点管理产品；界面还看不到完整 NAS 状态 |
| NAS 与其他节点 | 登记主机、连接状态、能力、采样时间、CPU／内存／磁盘／网络／进程 | 当前网页连接管理面向 HA、资产、模型等业务服务；新版没有通用主机登记和节点遥测接口 | 设备身份、连接设置、后台采集、节点详情、断线重连；Windows／Ubuntu 按设备实际能力接入 |
| 资源读数 | 主机与容器分别显示，历史趋势明确来源 | [RuntimeSample](../../worktrees/TS-170/tianshu-platform/services/platform/media/overview.py) 用 `process_time`、`/proc/self/status`、`/proc/net/dev` 采样；[运行页](../../worktrees/TS-170/tianshu-platform/apps/web/src/features/media/RuntimePanel.tsx) 已明确标注天枢进程和容器。媒体页有实际卷容量与媒体分类占用 | 缺宿主整机 CPU、主机内存、逐盘 IO、逐网卡、进程列表和多节点趋势；当前 CPU 不能当 NAS 总负载 |
| Docker 监控 | 按节点／Compose 应用分组，容器运行、健康、CPU、内存分别显示 | [导航](../../worktrees/TS-170/tianshu-platform/apps/web/src/app/modules.ts) 有“容器”；[路由](../../worktrees/TS-170/tianshu-platform/apps/web/src/app/App.tsx) 只为 `home/0` 挂载 HA 页；[未接入页](../../worktrees/TS-170/tianshu-platform/apps/web/src/components/UnavailablePage.tsx) 明示容器接口尚未提供 | 容器清单、指标、详情、筛选、日志与三栏页面均待接通；已有 `StatusRail` 和设计令牌可直接复用 |
| Dockge 控制与部署 | 既定 stacks 目录、配置单写者、端口／目录／卷预览、执行与健康分开 | 已有天枢固定部署的 Compose 导出、安装、更新恢复与 Dockge 保存副本核验；这不是通用 Dockge 管理连接器 | 任意登记栈的发现、配置与运行差异、启停重启、更新、部署计划、执行回执及部分失败恢复 |
| OpenWrt 与网络 | 系统／接口状态、网络与代理观测，按能力提供终端流量 | 用户已确认 OpenWrt＋SSH 密钥；新版没有 OpenWrt 适配器或路由器页 | SSH 固定查询、`ubus` 能力探测、WAN／LAN、流量与趋势；代理引擎类型及逐设备流量来源待现场确认 |
| 历史与告警 | 趋势、陈旧状态、故障与恢复通知 | 已有 Vector／Loki／Grafana／Prometheus 日志栈；[Prometheus 配置](../../deploy/observability/configs.py) 当前只抓日志守卫 obs-guard、Vector、Loki。既有告警围绕日志传输和容量 | 缺通用节点／容器指标来源、用户页历史查询、基础设施告警与通知送达验证。已有 Prometheus 不代表已经监控整台 NAS |

订阅页的网络延迟是“浏览器到天枢状态请求往返，包含处理时间”，不是 NAS 到外网、路由器或音乐平台的延迟。[订阅页交付记录](subscription-dashboard-deployment-2026-10-16.json) 已说明该口径。后续保留订阅页用途，基础设施页按主机、容器、进程分层表达。

## 可以复用的实现

| 来源 | 可复用内容 | 接入新版时的调整 |
| --- | --- | --- |
| [旧 Control Docker collector](../../references/legacy-control/agent/internal/dockercollector/collector.go) 与 [telemetry](../../references/legacy-control/agent/internal/dockercollector/telemetry.go) | Engine API 版本协商、字段筛选、实际容器身份、CPU／内存／网络／IO 采样、超时和不完整状态 | 提取采集器与对应测试，适配新版节点接口；不搬入旧 Control 数据库、整套 Agent 握手和权限架构 |
| [旧节点 metrics](../../references/legacy-control/agent/internal/metrics/collector.go) | Linux `/proc/stat` 差值、`/proc/meminfo`、首次采样和重置处理 | 复用算法与夹具，补 NAS 磁盘／网卡；不能把 Linux 读取器宣称支持 Windows |
| [旧 Dockge collector](../../references/legacy-control/agent/internal/dockgecollector/collector.go) 与 [Compose 对照](../../references/legacy-control/src/tianshu_control/modules/inventory/compose.py) | stacks 文件识别、受限解析、期望配置与实际运行差异、partial／stale 状态 | 提取纯解析和对照规则；旧 1.5.0 适配基线需与现机版本核对，原数据库模型不直接移植 |
| [旧 nas_ops](../../references/astrbot_plugin_nas_ops/main.py) | 服务模板、端口检查、目录卷规划、部署验收、SSH 查询接点 | 按职责提取，去除 AstrBot 消息对象依赖。旧 `_dockge_compose_cmd` 硬编码容器名和 `/opt/stacks`；`_used_host_ports` 出错返回空集合，不能照搬成“所有端口可用” |
| 新版 `external_catalog.py`、`web_external.py`、`tasks.py` | 服务端加密连接保存、修订冲突处理、现有登录／CSRF、领域台账到任务中心的投影 | 在原能力归属中扩展节点连接，具体 SSH 字段单独校验；避免另造账号库、凭据保险库和全局任务中心 |
| 新版 `StatusRail`、媒体存储识别、根 observability | 四色语义与主题、实际容量池识别、已有指标和日志持久链路 | 仅在确有第二调用方时提取纯容量身份函数到明确归属的共享模块；节点业务不反向依赖媒体下载模块 |

旧 Control 参考固定为 `05d503a118d3ba187f3ebd444df019bf73bb90f0`，nas_ops 为 `4bd39eea3ca616a959726edd67f024d1198761f0`。它们是代码复用依据，不是新版已上线证明，也不意味着接管 NAS 上独立 control-hub。

旧 Docker collector 单响应上限为 64 容器，超限会标 partial。9 月 21 日 NAS 记录已有 105 个总容器；后续旧故障记录还出现过更大清单，均不是今天的盘点数。因此新版必须支持有界分批或分页，并显示完整度，不能直接沿用 64 项上限后丢掉其余容器。

## 建议的实现归属

Platform 负责节点登记、连接设置、观测投影、操作台账和网页；节点侧连接器负责读取系统／Docker／OpenWrt 及执行登记动作。主机指标、容器运行事实和 Compose 配置各保留自己的来源。

- **连接与采集**：NAS／Linux／OpenWrt 优先用已有 SSH 通道的固定查询适配器。先验证 NAS 容器运行环境能否使用所需 SSH 客户端及密钥引用，不能把开发电脑有密钥当作正式服务已能连接。设备端缺 Python 不应阻断 OpenWrt 基本读取；优先系统自带接口。高频 Docker 采样可提取已有节点 Helper，落在节点侧，平台业务容器不直接持有 Docker socket。
- **领域组织**：拟在平台 `services/platform/infrastructure/` 内就近放置节点、容器、路由器与动作模块；网页对应 `apps/web/src/features/infrastructure/`。`web_console.py`、`service.py`、`App.tsx` 和 `modules.ts` 只做接线，由一个平台写入者维护。
- **状态口径**：观测含节点／对象身份、来源、采样时间、完整度和原因。运行状态、健康状态、连接可达性、采样新鲜度、任务进度分开。首次采样、计数器重置、离线、无传感器显示未知或不支持；负载均值不改名为 CPU 使用率。
- **数据保存**：Platform 保存节点元数据和操作事实；历史数值优先接已有 Prometheus，中央日志复用已有 Loki 链路。容器最近日志按明确对象、时间与大小读取，不能用部署验收日志冒充业务容器日志。完整持久日志沿用现有用户要求；其他容器日志按登记接入，不把截断预览当作完整留存。
- **页面入口**：`#/home/1` 落地容器三栏页；`#/home/2` 先落地节点与路由器，游戏服继续保留自己的未接入说明。设置页补节点连接入口，复用现有主题、会话和错误状态；不另建控制台。
- **常用操作**：已登记对象的启停重启使用现有管理员身份与实际能力，一次点击进入执行和回执。新增部署／配置改变按原设计提供一次具体变更预览；不为普通查询和每次刷新增加确认层。

### OpenWrt 首批能力

通过 SSH 调用已知只读接口，例如 `ubus call system board`、`ubus call system info`、`ubus call network.interface dump`，再按实际接口读取计数器。官方 [procd 系统实现](https://github.com/openwrt/procd/blob/master/system.c) 和 [netifd 接口实现](https://github.com/openwrt/netifd/blob/master/ubus.c) 支持上述方向；设备实际版本和可用对象在首次连接时核对。

首批展示型号／版本、运行时长、负载与内存、WAN／LAN 接口状态、地址、接口流量和采样时间；CPU 使用率通过计数器差值另算。在线终端来源可使用设备实际提供的租约、邻居或无线关联信息，但要显示其来源和更新时间，不能把 DHCP 租约直接标成此刻在线。

每终端实时速率／累计流量在确认 nlbwmon 或其他实际来源后加入，并记录采样与卸载造成的覆盖限制。OpenClash／Mihomo 需确认实际安装和版本，再做健康、提供者刷新和订阅更换；当前不假设已经安装。后续网络变更要有可执行的恢复路径，以免改变路由后失去管理连接。

### Dockge 控制方式

建议使用“登记 stacks 目录＋受限节点执行器＋实际 Docker 读回”。[Dockge 官方说明](https://github.com/louislam/dockge) 将 Compose 文件作为管理基础并支持常规 Compose 命令；当前 [官方操作处理代码](https://github.com/louislam/dockge/blob/master/backend/agent-socket-handlers/docker-socket-handler.ts) 使用认证后的 Socket.IO 事件。现有证据不支持把它当作稳定通用 REST API。

每个栈确定一个配置写入负责方。由 Dockge 编辑的现有栈，平台先读取和提供登记的生命周期操作；平台负责生成／修改的栈，所有写入走同一执行端，保存预期版本、变更差异及上一版。发现外部编辑时重新生成计划，不能同时覆盖 Compose。宿主 stacks 路径、Dockge 容器内路径、Compose 工作目录及标签映射必须取实值。

容器指标可从 Engine 获取，内存必须说明是原始使用量还是扣除缓存后的数值；两者不是同一指标，见 [Docker stats 说明](https://docs.docker.com/reference/cli/docker/container/stats/)。host network 等不能归属于单容器的网络读数保留限制。

## 先处理的运行问题

[9 月 28 日分析](resident-guard-docker-failure-2026-09-28.md) 已指出容量 guard 在 `docker ps` 后批量 `inspect` 全宿主容器，失败只记录笼统的 `docker_command_failed`；无关临时容器恰好消失是可能的竞争窗口，尚未证实为现场根因。[10 月 14 日部署记录](memory-phase1-deployment-2026-10-14.json) 再次记录同类停机，根因仍未解决。

应先补固定调用阶段、退出码／超时、耗时和批次数等诊断，复现采样竞态并区分目标消失、权限失败和 Docker 不可达。若证明确为观测竞争，再在原截止时间内设计有限重采样；容量、容器身份和挂载冲突校验继续有效。不能把“读不到”直接改成健康。

另外，[现有容量 guard](../../ops/resident_capacity/guard.py) 要求固定九／十个容器身份、运行状态和重启策略符合登记。对天枢自身受保护服务直接停止或重建，会与这套规则冲突。后续控制器必须识别该部署，由既有维护／更新恢复路径接管整次操作；普通外部栈不受这个特定流程影响。此项需要在开放天枢自身控制前完成，不能靠关闭 guard 绕过。

## 候选工作包和顺序

下列 INF 编号只用于本计划，不占用正式 TS 编号。当前任务板已有 TS-171 在进行中，本次不修改其状态，也不恢复已完成任务。

| 顺序与工作包 | 用户可见交付 | 依赖 | 主要写入归属 |
| --- | --- | --- | --- |
| INF-00 运行故障取证与守护兼容 | Docker 采样失败能解释原因；给后续维护动作提供明确协作方式 | 已有 guard 与故障记录 | 根 `ops/resident_capacity/`、`tests/resident_capacity/`、相关运维文档 |
| INF-01 节点连接与 NAS 观测 | 网页添加节点、测试连接、看 NAS 整机 CPU／内存／卷／网卡与新鲜度 | 最新平台基线；现有 NAS 元数据；连接器形状落实 | Platform infrastructure 节点模块、节点页、连接设置；必要凭据目录扩展与薄接线 |
| INF-02 Docker 与 Compose 只读管理 | 设计稿三栏页；按节点和应用过滤；运行／健康／资源／端口／挂载／最近日志；Compose 差异 | INF-01 | Platform infrastructure 容器模块与页面；提取的节点 Docker／Dockge 采集器 |
| INF-03 OpenWrt 状态与网络 | 路由器系统、WAN／LAN、接口速率、终端来源与断连状态 | INF-01；OpenWrt SSH 已有，连接参数在首次接入时取值 | Platform infrastructure 路由器适配器和节点详情 |
| INF-04 历史趋势与基础设施告警 | 1 小时／24 小时／7 天趋势、容量与离线告警、恢复记录和通知状态 | INF-01；容器／路由器部分随 INF-02／03 接入 | 已有 observability 指标入口与容量配置；平台历史查询和图表 |
| INF-05 容器与现有栈控制 | 登记对象的启动／停止／重启／镜像更新；任务进度和结果读回 | INF-02、INF-00；天枢自有栈维护路径完成 | 节点动作执行器、infrastructure 操作台账、现有任务中心投影及容器页 |
| INF-06 Dockge 部署计划与恢复 | 模板选择、镜像／端口／目录／卷预览、部署、部分失败和按计划恢复 | INF-05；每栈配置写入归属明确 | infrastructure 部署模块与节点执行器；复用 nas_ops 纯计划规则 |
| INF-07 其他节点与网络扩展 | Windows／Ubuntu 实机接入、按支持能力显示进程／GPU／温度／磁盘健康；代理维护与终端流量 | INF-01／03；实际设备和代理／流量来源 | 对应系统适配器与已有节点页面，不另建节点平台 |

**第一批建议 INF-00＋INF-01＋INF-02＋INF-03。** 先把现有 NAS、Docker、OpenWrt 做到可查看、可定位，避免同时铺开所有设备。根运维工作与平台工作在明确写入范围后可独立安排；同一平台内部按 01→02→03 串行交付，共享入口保持单写者。

**第二批 INF-04＋INF-05，第三批 INF-06＋INF-07。** Ubuntu 若与 NAS 共用 Linux 读取器，可在第一批追加一个代表节点验证；Windows、代理配置修改、深度硬件传感器仍按实际设备拆出。全量节点监控与完整控制保留在目标内，不以第一批只读交付宣称全部完成。

### 每包必须证明的行为

| 工作包 | 有效验收 |
| --- | --- |
| INF-00 | 假 Docker CLI 复现非零／超时／枚举后消失，诊断可区分；原容量不足、挂载冲突和身份变化仍触发既定保护；停止回执和不确定状态保留。验证沿用 `tests/resident_capacity/test_guard.py` |
| INF-01 | 隔离 SSH 服务和固定系统输出贯通后台到网页；离线、缺指标、密钥不可用、首次差值、计数重置、断线恢复；最后用真实 NAS 只读数值核对。宿主与容器口径不能混用 |
| INF-02 | 有健康／无健康／不健康／启动中／停止／离线均正确；同名不同节点、容器重建、超过 64 容器、分页／部分结果、切换对象后的迟到响应；真实隔离 Docker 验证清单、指标与日志，不只用 UI 假数据 |
| INF-03 | OpenWrt 只读调用，缺 ubus 对象／无无线／接口变更／计数重置均显示正确；接口总流量与终端数据区分；实际 OpenWrt 读回通过后才标实机已接入 |
| INF-04 | 图表使用服务端连续采样，断点不连成假曲线；告警去抖、合并、恢复；浏览器离开不停止采集；历史保留与实际存储预算核对。通知未送达与未配置必须区分 |
| INF-05 | 隔离容器／栈的实际启停重启和读回；请求重复、发送后断线、超时未知不得盲目重发；身份变化不能误操作新容器；对受 guard 的服务验证完整维护协作 |
| INF-06 | 实际临时目录／端口冲突、外部 Compose 编辑、拉取失败、部分服务失败、缺健康检查、恢复旧配置；回退不能宣称撤销已经发生的数据变更；失败不删原有卷和文件 |
| INF-07 | 每个实际系统分别验收；Windows 测试不能由 Linux 夹具替代；代理更换失败保留上一可用配置，终端流量标明来源覆盖；未连接设备保持未接入 |

网页统一做桌面／手机、浅色／深色、键盘和减少动态的相关场景；复用既有 Playwright 与状态组件。后端沿用 unittest，提取 Go 采集器时运行其相关测试。每包先形成稳定改动，再做一次集中审查和影响范围验证；本地通过、集成、部署和真实使用分别记录。

## 首次接入时补齐的设备信息

| 对象 | 当前依据 | 需要在接入时确认 |
| --- | --- | --- |
| NAS | 既有记录为 `192.168.31.210:77`，DSM 7.2.1、N305、约 48 GB，Dockge 10006；新版有 10 月 16 日部署记录 | 当前版本、节点身份、实际 stacks／Compose 映射、可用采集方式、容器数量和指标开销；旧读数不作现时状态 |
| OpenWrt | 本次用户确认系统和现有 SSH 密钥；旧产品稿记载京东雅典娜，不能据此认定当前型号 | SSH 目标／用户／端口、密钥在部署侧的安全引用、host key、固件与 ubus 能力、是否有 nlbwmon／代理。无需用户在聊天粘贴私钥 |
| 其他节点 | 原稿提到 Windows／Ubuntu、笔记本和游戏台式机 | 当前实际设备及系统、可达通道、希望监控的能力；未提供的节点不虚构地址和安装状态 |
| 既有监控服务 | 9 月 21 日记录可见 Beszel、Uptime Kuma；天枢自己的 Prometheus／Loki 已有部署链路 | 是否已有可复用授权指标或探活数据。选一个同类数据的采集负责方，不并行装多套重复探针 |

正式拆卡时再分配 TS 编号、独立工作树和精确基线，更新任务板及必要接口样例。跨进程节点协议由协调方在根合同入口发布；平台内部网页接口写入本产品文档即可，不把现有 I01–I18 能力目录当作已实现 API。后续实施按本计划逐包推进，新增生产连接、服务安装和部署另记录实际执行范围。
