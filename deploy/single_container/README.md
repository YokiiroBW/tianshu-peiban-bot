# 天枢统一发行

普通用户的发布物是一份安装目录、一张天枢镜像和一个 Compose 服务。发布者准备完成后，用户在安装目录执行 `docker compose up -d`；不需要部署五个 Python 业务容器或单独漫画助手容器。

镜像包含一个业务宿主、一个 Python 依赖环境及保留功能的 Vector/Loki/Prometheus/Grafana/guard 原生引擎。五业务模块无内部 TCP，OneBot 的端口用于外部 WebSocket/SDK；漫画文件助手按既有插件生命周期和独立 UID 按需运行。默认网页/API 公开18446，OneBot启用时18765；观测鉴权入口沿原策略仅主机环回发布。

发布者用 `build.prepare(release, output, constraints=...)` 归档四产品及 application 的精确40位提交、构建 wheel、按实际依赖和 `runtime.constraints` 解析并校验 Linux cp312 二进制 wheel，生成备齐 Python 依赖的 Docker build context。`release.images` 必须是 Platform 与四观测引擎的不可变引用；正式可拉取发行使用 `repository@sha256:manifest_digest`，不能用 latest。本地 image ID 与 registry manifest digest 不等同，Linux builder 仍需实际解析并构建。该步骤不构建或启动镜像。引用格式见 [Docker官方文档](https://docs.docker.com/reference/dockerfile/#from)。

原配置生产者的 settings/env、各自冻结合同原字节、TLS、secret、观测 JSON/provisioning/dashboard 交给 `plan.make_installation`；原观测 Compose 的 environment 交给 `observability_environment`，保留 Grafana URL 等原值。需要五个日志来源（原 `configs.vector(include_knowledge=True)`）和明确 Knowledge 日志预算。`installation.export` 只写不存在的新安装目录，保留已有数据/媒体 bind；不读取或复制生产数据库、不重置模型、凭据或 PG/Redis namespace。已有 WebAccess 使用其原端口及 Origin；新安装使用显式 bootstrap frontend。

`host.json` 每个模块拥有原 settings、独立 env 与固定逻辑 origin；常规原生成器使用 `*.internal` 地址。自定义部署时应由发布者使这些明确目标与原第一方调用地址一致，不能靠 DNS/URL 猜测或关闭 origin 检查。外部 AstrBot 等适配器继续使用正式服务协议与原 token。

本轮本地结果、可移植联合验证及 Linux/生产未覆盖见 [TS-340](../../docs/handoffs/TS-340.md)。当前候选尚未提供已运行的 Linux 发布镜像；不能把 source context 或 Windows 联合结果称为 NAS 实机验收。
