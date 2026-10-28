# TS-176 节点与容器观测执行器

执行模型 `gpt-6.1-sol / max`。主工作区 `C:/YOKI/Codex/tianshu-peiban-bot`；本任务只写 `worktrees/TS-176/workspace`。先读主工作区 CURRENT、基础设施计划和执行安排，按需读旧 `references/legacy-control/agent/internal/{metrics,dockercollector,dockgecollector}` 与 nas_ops，勿把参考库当生产已接入。

只允许 `ops/infrastructure/`、`tests/infrastructure/`、`contracts/infrastructure-node/candidate-v1/`、`docs/handoffs/TS-176.md`。根任务板／已发布合同／全局入口由协调者维护。

第一步快速交回一个可执行的接口提议：节点配置、请求／响应、能力、分页／完整度、指标口径和日志读取上限。它应适合平台经配置好的子进程调用，执行器通过明确注册的 SSH 目标查询设备；没有默认 NAS 地址、密钥或真实调用。参数用结构化数据，禁止浏览器传 shell 命令。兼容 Windows 开发端运行，节点侧 OpenWrt 不要求 Python。

实现 Linux／Synology CPU 与内存、磁盘容量与网卡计数，OpenWrt board／info／interface dump；Docker 容器实际身份、Compose 标签、运行／健康、端口／挂载摘要、CPU／内存／IO／网络与限定日志；Dockge stacks 的只读配置投影和运行差异。优先提取旧规则和测试夹具，不引入旧 Agent／Control 数据库。不直接照搬64容器上限、硬编码 dockge 容器名或端口查询失败当空闲。

先做第一批读取闭环；扩展动作／部署必须等协调者下一张精确卡。需要平台字段或运行包装器调整时及时向协调者反馈，不能另起未协商服务。当前只在隔离夹具／本地合成 SSH／Docker中验证，不读真实SSH配置／密钥，不触达NAS路由器。

实现按节点和变化原因组织，传输、解析、采样状态、命令行入口分工清楚。总超时、输出字节、进程清理和并发有界；首样本／重置／陈旧／部分／不支持诚实表达；每设备终端流量不能由接口总流量伪造。合同含schema、正反例和实际验证脚本，协调发布前状态为 candidate。

稳定后集中差异审查和相关测试，交回精确提交、测试与未覆盖，写 `docs/handoffs/TS-176.md`。不要更新根任务板或自行部署。返修仍由你完成。
