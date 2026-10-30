# 天枢统一发行

普通用户的发布物是一份安装目录、一张天枢镜像和一个 Compose 服务。发布者准备完成后，用户在安装目录执行 `docker compose up -d`；不需要部署五个 Python 业务容器或单独漫画助手容器。

镜像包含一个业务宿主、一个 Python 依赖环境及保留功能的 Vector/Loki/Prometheus/Grafana/guard 原生引擎。五业务模块无内部 TCP，OneBot 的端口用于外部 WebSocket/SDK；漫画文件助手按既有插件生命周期和独立 UID 按需运行。新安装默认网页HTTP端口18446、外部TLS API端口18443，OneBot启用时18765；保留原WebAccess时以捕获的实际绑定/Origin为准，观测鉴权入口沿原策略仅主机环回发布。

发布者用 `build.prepare(release, output, constraints=...)` 归档四产品及 application 的精确40位提交、构建 wheel、按实际依赖和 `runtime.constraints` 解析并校验 Linux cp312 二进制 wheel，生成备齐 Python 依赖的 Docker build context。`release.images` 必须是 Platform 与四观测引擎的不可变引用；正式可拉取发行使用 `repository@sha256:manifest_digest`，不能用 latest。本地 image ID 与 registry manifest digest 不等同，Linux builder 仍需实际解析并构建。该步骤不构建或启动镜像。引用格式见 [Docker官方文档](https://docs.docker.com/reference/dockerfile/#from)。

更新网页时同时传入 `frontend` 构建目录及 `frontend_manifest` 清单（CLI 为 `--frontend`、`--frontend-manifest`）。发布器逐项核对清单中的前端源码、锁文件和全部产物字节，再将网页复制到 `/srv/tianshu/web`，替换基础镜像内的旧网页；后端集成提交可不同，但所有前端输入必须完全一致。发行回执保留构建者与最终 Platform 提交、Node/npm 版本及文件哈希。

原配置生产者的 settings/env、各自冻结合同原字节、TLS、secret、观测 JSON/provisioning/dashboard 交给 `plan.make_installation`；原观测 Compose 的 environment 交给 `observability_environment`，保留 Grafana URL 等原值。需要五个日志来源（原 `configs.vector(include_knowledge=True)`）和明确 Knowledge 日志预算。`installation.export` 只写不存在的新安装目录，保留已有数据/媒体 bind；不读取或复制生产数据库、不重置模型、凭据或 PG/Redis namespace。已有 WebAccess 使用其原端口及 Origin；新安装使用显式 bootstrap frontend。

`host.json` 每个模块拥有原 settings、独立 env 与固定逻辑 origin；常规原生成器使用 `*.internal` 地址。自定义部署时应由发布者使这些明确目标与原第一方调用地址一致，不能靠 DNS/URL 猜测或关闭 origin 检查。外部 AstrBot 等适配器继续使用正式服务协议与原 token。

运行日志由 Vector 采集、Loki 存储并按 `retention_days` 留存，默认 30 天；旧日志账本 10,000 条准入与排序限制已退役。手动清空、原生持久缓冲与安全源段回收见 [日志保留说明](../observability/RETENTION.md)。

2026-10-28，R14已正式运行于 **192.168.31.230**，镜像`sha256:dd0cd89618a04a281717fc51aa81c10e3e552d8b2a8a5f0ef244141afb952611`（3.389GB、66个冻结Linux wheel）。六模块、五观测引擎、漫画插件和容量守护正常；旧13保持cold/restart=no。1126配置仅四项Loki参数及原scope网关租约引用更新，25逻辑存储绑定、37mount和业务数据保留，无业务PG恢复/迁移。

陪伴、记忆、日程的原生SQLite/PG完整联动、音乐worker/去重/导出/同步及两项NCM验证通过；外部协议端点使用隔离录制服务。已修复多会话回流和Memory导入缺列。日志删除outbox按stream持久合并五分钟，Loki用原生SQLite主/Bolt备库及24小时split，原全30天查询、10秒期限、hash和10000逻辑保有上限保持。两次严格历史追赶通过，随后独立120秒working set中位1.4531GiB，CPU单核91.0240%（8核总量11.3780%）；该内存高于前轮，不冒称同负载节省率。

双图入口仍为[任务与设置→链路图](http://192.168.31.230:18446/#/settings/12)。20次并发ready均200、跨租期、真实只读绑定与71静态文件验证通过。真实QQ收发及正式网页登录按用户要求暂不测，真实付费模型/音乐外部账号未混称验证。详见[完整链路与R14验收](../../docs/development/chain-closure-validation-2026-10-28.md)；[R11优化](../../docs/development/chain-optimization-2026-10-28.md)与[R7首次发布](../../docs/development/single-container-deployment-2026-10-27.md)保留历史。
