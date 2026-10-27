# 天枢统一发行

普通用户的发布物是一份安装目录、一张天枢镜像和一个 Compose 服务。发布者准备完成后，用户在安装目录执行 `docker compose up -d`；不需要部署五个 Python 业务容器或单独漫画助手容器。

镜像包含一个业务宿主、一个 Python 依赖环境及保留功能的 Vector/Loki/Prometheus/Grafana/guard 原生引擎。五业务模块无内部 TCP，OneBot 的端口用于外部 WebSocket/SDK；漫画文件助手按既有插件生命周期和独立 UID 按需运行。新安装默认网页HTTP端口18446、外部TLS API端口18443，OneBot启用时18765；保留原WebAccess时以捕获的实际绑定/Origin为准，观测鉴权入口沿原策略仅主机环回发布。

发布者用 `build.prepare(release, output, constraints=...)` 归档四产品及 application 的精确40位提交、构建 wheel、按实际依赖和 `runtime.constraints` 解析并校验 Linux cp312 二进制 wheel，生成备齐 Python 依赖的 Docker build context。`release.images` 必须是 Platform 与四观测引擎的不可变引用；正式可拉取发行使用 `repository@sha256:manifest_digest`，不能用 latest。本地 image ID 与 registry manifest digest 不等同，Linux builder 仍需实际解析并构建。该步骤不构建或启动镜像。引用格式见 [Docker官方文档](https://docs.docker.com/reference/dockerfile/#from)。

原配置生产者的 settings/env、各自冻结合同原字节、TLS、secret、观测 JSON/provisioning/dashboard 交给 `plan.make_installation`；原观测 Compose 的 environment 交给 `observability_environment`，保留 Grafana URL 等原值。需要五个日志来源（原 `configs.vector(include_knowledge=True)`）和明确 Knowledge 日志预算。`installation.export` 只写不存在的新安装目录，保留已有数据/媒体 bind；不读取或复制生产数据库、不重置模型、凭据或 PG/Redis namespace。已有 WebAccess 使用其原端口及 Origin；新安装使用显式 bootstrap frontend。

`host.json` 每个模块拥有原 settings、独立 env 与固定逻辑 origin；常规原生成器使用 `*.internal` 地址。自定义部署时应由发布者使这些明确目标与原第一方调用地址一致，不能靠 DNS/URL 猜测或关闭 origin 检查。外部 AstrBot 等适配器继续使用正式服务协议与原 token。

运行日志默认五来源合计最多10000个逻辑事件、保留30天，数量或天数任一先到先清最旧的可安全退役日志。主设置为 `log_max_events` / `retention_days`，修改后重新导出并重启guard/Loki。容器内writer管理CLI可手动clear旧日志含pending，持久cutoff阻止旧raw/Vector缓冲重放回灌；query角色仍只读。活动段、持久buffer、Loki WAL/chunk/cache及Compactor异步回收不等于瞬时物理一万条。详见 [日志保留说明](../observability/RETENTION.md)。

2026-10-28，R7已正式运行于 **192.168.31.230**，镜像 `sha256:d72f5febd4945ca5db8e51e2869f5720ab1ee86250ba76ede62b953bc3e1069b`（3.376GB、66个冻结Linux wheel）。正式容器healthy、六模块ready，漫画插件及容量守护运行；旧13已停机、关闭自动重启并保留镜像和冷备。25个逻辑库核验、公开HTTP/TLS/OneBot入口、五源日志确认及跨租期就绪检查通过；此前运行日志已按授权清空约900MB，业务配置和数据保留。

正式60秒采样working set中位0.9182GiB；旧13此前为1.737GiB，两窗口负载不同。真实QQ/模型收发、带session网页全流程及漫画发布未作实测结论。完整证据、启动故障修复、资源口径及恢复边界见[正式发布记录](../../docs/development/single-container-deployment-2026-10-27.md)；早期TS340/R6未部署表述为历史阶段。
