# 单容器与整体优化执行范围（2026-10-27）

## 用户进一步明确的交付方向

用户已明确追问内部端口，并认可参考 AstrBot 的统一宿主、模块及插件组织。最终交付必须是一个业务宿主装配 Platform、Companion、Memory、Knowledge、Gateway，内部不建立 TCP 监听。此前将原 CLI 进程放进统一镜像的实现仅为过渡研究，不能作为本轮整合完成的依据。漫画文件助手保留必要的独立 UID，通过 Unix Socket 接入原插件宿主；外部 AstrBot/NoneBot 适配器保留正式网络协议和发送回执。Vector/Loki/Prometheus/Grafana 属于现有成熟观测引擎，在完整替代之前不得为减少进程而删除。

新增隔离本地任务：

- TS-334（原 runtime 作者）：拥有 deploy/single_container/exchange.py、frameworks/ 及其测试，实现显式模块注册、进程内请求/流式响应与生命周期桥接，无内部 TCP、无全局 monkeypatch；保留取消、回压与同步工作线程回调。
- TS-335（Platform 作者）：原 Platform 干净隔离检出新分支，拥有跨第一方客户端的组合根注入及原后台生命周期复用。WebConsole 与 Platform 共用显式内部端口；外部 AssetLibrary、Chat、设备、媒体及插件网络出口保持原实现。不得按 URL 猜测或全局替换 ClientSession。
- TS-336（原日志作者）：原 Memory、Companion 干净隔离检出各开新分支，拥有明确 config/env/client 工厂注入；Memory 同步 authority/source/retrieval 与 Companion 现 JsonService 使用共享模块 exchange 适配。各自的合同、授权、来源、回执、admission、PG/Redis namespace 和日志保持。
- TS-337（协调者）：Gateway 公共 source/authority 注入与 storage 0.1.3 对齐、统一业务应用装配和交付；旧镜像依赖树拼装不作为最终单宿主方案。集中核查注册、启动、取消、关闭和跨模块回调，必要真实 Linux/Docker 验收另按设备授权。

统一端口的最小合同是显式目标模块的 session/transport 工厂：aiohttp 客户端取得兼容 session，httpx 客户端取得 async/sync transport。内部适配复用原请求和响应合同而不走网络，不绕过原 schema/auth/账本；阶段内仍有合同编解码的成本，不能宣称已经消除了全部序列化。模块调用可重入，不能用一个锁或单一串行队列包围跨模块 await。同步调用只在线程中回到宿主 loop，禁止 loop 内阻塞等待。

用户已明确授权：天枢统一为一个容器，内部模块化运行，漫画辅助作为插件；同时调研并实施有证据的复用、精简、性能及日志占用优化。先完成隔离本地实现、集中验证与可审阅交付；不操作生产数据、不推送部署、不发送 QQ 或调用真实模型。

当前根协调检出有既有混合修改，全部保留；本批从根提交 ab93be4e91b824008d387d71c80e77dfac7341b3 创建独立托管 worktree，产品使用已发布提交及原有干净隔离检出新分支。不能把旧任务继续标为在开发。

## 单写范围

- TS-329：协调者拥有统一构建、配置导出、Compose、Dockerfile、发行文档与最终集成；路径 deploy/single_container/ 中非下项的文件。
- TS-330：统一进程运行时；独占 deploy/single_container/runtime.py、process.py、health.py 及对应运行时测试。只实现一个天枢容器的内部进程生命周期，不另造业务实现。
- TS-331：日志与监控优化；独占 deploy/observability/ 及对应 tests/deployment/observability/，保留所有现有完整日志、检索、补传、15条告警与鉴权语义。优化必须有具体数据或调用证据。
- TS-332：漫画辅助插件接点；独占 Platform 原漫画辅助与 plugins 内必要接点及相关测试；沿已有插件宿主、文件协议、两种 UID 和回执实现演进，不建立平行插件系统。
- TS-333：协调者完成全第一方功能领域覆盖梳理、集成与功能等价验证；非本批范围的 AssetLibrary、Chat Audit 等独立产品只核接入，不接管其数据或实现。

## 共同约束

统一镜像与发布版本；保留业务数据、配置、凭据域和必要的内部身份。现有生产 PG/Redis 不切回 SQLite。默认完整功能内置，外置能力仅为可选。先沿已有 CLI 和成熟二进制收拢运行，不用重复重写换取进程数量；后续真正共享运行时的收益必须量测。

日志同时检查实际内存、持久卷和重复存储。不得用采样、静默丢弃、缩短原保留期、取消历史告警或删除未确认源段来冒充优化。源码改造与运行元数据的只读检查已在范围内，生产清理/迁移不在本轮。

每项交付注明精确提交、复用点、实际验证、原有失败和未覆盖；稳定后集中验证，不重复未变成功检查。功能尚未构建或实机运行时如实记录，不能以单测代替容器验收。
