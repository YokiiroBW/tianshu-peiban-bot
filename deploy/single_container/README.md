# 天枢统一发行

普通用户的发布物是一份安装目录、一张天枢镜像和一个 Compose 服务。发布者准备完成后，用户在安装目录执行 `docker compose up -d`；不需要部署五个 Python 业务容器或单独漫画助手容器。

镜像包含一个业务宿主、一个 Python 依赖环境及保留功能的 Vector/Loki/Prometheus/Grafana/guard 原生引擎。五业务模块无内部 TCP，OneBot 的端口用于外部 WebSocket/SDK；漫画文件助手按既有插件生命周期和独立 UID 按需运行。新安装默认网页HTTP端口18446、外部TLS API端口18443，OneBot启用时18765；保留原WebAccess时以捕获的实际绑定/Origin为准，观测鉴权入口沿原策略仅主机环回发布。

发布者用 `build.prepare(release, output, constraints=...)` 归档四产品及 application 的精确40位提交、构建 wheel、按实际依赖和 `runtime.constraints` 解析并校验 Linux cp312 二进制 wheel，生成备齐 Python 依赖的 Docker build context。`release.images` 必须是 Platform 与四观测引擎的不可变引用；正式可拉取发行使用 `repository@sha256:manifest_digest`，不能用 latest。本地 image ID 与 registry manifest digest 不等同，Linux builder 仍需实际解析并构建。该步骤不构建或启动镜像。引用格式见 [Docker官方文档](https://docs.docker.com/reference/dockerfile/#from)。

更新网页时同时传入 `frontend` 构建目录及 `frontend_manifest` 清单（CLI 为 `--frontend`、`--frontend-manifest`）。发布器逐项核对清单中的前端源码、锁文件和全部产物字节，再将网页复制到 `/srv/tianshu/web`，替换基础镜像内的旧网页；后端集成提交可不同，但所有前端输入必须完全一致。发行回执保留构建者与最终 Platform 提交、Node/npm 版本及文件哈希。

原配置生产者的 settings/env、各自冻结合同原字节、TLS、secret、观测 JSON/provisioning/dashboard 交给 `plan.make_installation`；原观测 Compose 的 environment 交给 `observability_environment`，保留 Grafana URL 等原值。需要五个日志来源（原 `configs.vector(include_knowledge=True)`）和明确 Knowledge 日志预算。`installation.export` 只写不存在的新安装目录，保留已有数据/媒体 bind；不读取或复制生产数据库、不重置模型、凭据或 PG/Redis namespace。已有 WebAccess 使用其原端口及 Origin；新安装使用显式 bootstrap frontend。

`host.json` 每个模块拥有原 settings、独立 env 与固定逻辑 origin；常规原生成器使用 `*.internal` 地址。自定义部署时应由发布者使这些明确目标与原第一方调用地址一致，不能靠 DNS/URL 猜测或关闭 origin 检查。外部 AstrBot 等适配器继续使用正式服务协议与原 token。

运行日志默认五来源合计最多10000个逻辑事件、保留30天，数量或天数任一先到先清最旧的可安全退役日志。主设置为 `log_max_events` / `retention_days`，修改后重新导出并重启guard/Loki。容器内writer管理CLI可手动clear旧日志含pending，持久cutoff阻止旧raw/Vector缓冲重放回灌；query角色仍只读。活动段、持久buffer、Loki WAL/chunk/cache及Compactor异步回收不等于瞬时物理一万条。详见 [日志保留说明](../observability/RETENTION.md)。

2026-10-28，R11已正式运行于 **192.168.31.230**，镜像 `sha256:82dc69fd1b1a2404ac3c99c3bc9c8347a7fab52ec5e8b045c8effe4134795f11`（3.390GB、66个冻结Linux wheel）。六模块、五观测引擎、漫画插件和容量守护正常；旧13已停机、关闭自动重启，旧镜像/配置/冷备保留。原业务配置、数据、PG/Redis地址与namespace沿用，本批未恢复或迁移业务数据库。

本轮已修复并发就绪503、日志1024批次分页超时与旧pending饥饿，并优化音乐批读、回执轮转、请求内向量复用和网关读取锁。双图入口为[任务与设置→链路图](http://192.168.31.230:18446/#/settings/12)，分别展示账号/角色/会话关系和实际消息/记忆/日程观测；缺少事件的环节如实标示。

正式20次并发ready全200、跨租期检查、25逻辑存储绑定及前端71文件HTTP字节核验通过。跨租期后60秒working set中位0.9983GiB、CPU单核126.8269%（8核总量15.8534%）；仍有历史日志追赶，不能与旧13或R7不同负载窗口计算固定节省率。真实QQ/付费模型/外部发布、正式带session浏览器JS未作实测结论。完整记录见[整体链路优化与R11验收](../../docs/development/chain-optimization-2026-10-28.md)，R7前次发布与清旧记录见[原单容器发布记录](../../docs/development/single-container-deployment-2026-10-27.md)。
