# CR20 部署实现与最终切换边界

本卡是已获授权的完整重构的一部分。实现者为 foundation_models（gpt-6.1-sol / xhigh），在 A 组与全域装配完成后直接继续，不另等授权。主协调者是唯一真实生产写入者。源码实现可在 CR19 前完成；测试、构建和实际部署分别等完整集成与集中验收。

## 独占范围与输入

- 本实现者继续拥有根后端 manifest/lock、入口、composition 与 migration 总注册；新增 `deploy/core/**`、必要的根 Docker 构建忽略文件、`tests/deployment/**` 与 `docs/handoffs/CR20-deployment.md`。
- `web/**` 继续由 frontend 独占，不修改其源文件或锁。消费正式构建产物；SDK 和 channels 依赖由 capabilities 所有者提交。
- 最终源码退出范围包括已被替代的 `packages/tianshu-storage`、`deploy/tianshu`、`deploy/observability`、`ops/resident_install`、`ops/resident_capacity`、`ops/recovery` 及只服务旧路径的用例/入口。先核对真实引用，将仍有效的日志、容量、恢复能力迁入新归属后删除旧实现；不只从镜像排除或挪入 legacy。独立产品、共享合同和协调历史文档不按目录名批量删除；原根脏树不在代理操作范围。
- 冻结的 `application/deploy/single_container` 只用于复用有效监督、信号、媒体进程和容量保护知识；不把旧 BusinessHost、ThreadedExchange、五产品装配、旧迁移链带进新版。
- 真实资源边界在 `coherent-refactor-cutover-boundaries-2026-10-31.json`。其中 candidates 不是可立即删除指令。实现者不 SSH、不读取真实凭据、不 build/pull、不停容器、不部署、不清理。

## 必须实现的交付

1. 一个明确可构建的产品镜像，Python 安装根锁与真实 SDK，前端用其正式锁构建。镜像只携带新运行源码、静态资源和实际需要的插件/媒体运行依赖，不携带旧 projects/references、旧数据库和测试夹具。ffmpeg、实际所需浏览/下载/漫画辅助程序及其版本在 manifest 中明确，不以运行时临时安装代替声明。
2. 单应用容器监督 interactive、model-broker、worker 三个核心角色与需要的内部辅助进程。收到 TERM 后停止接入/派工并有界排空，等待子进程，最终回收进程树；一个核心角色异常应反映真实健康与退出，不能只因入口进程活着报 ready。仅 worker 持有 PluginHost；仅指定渠道角色持有 OneBot 连接。内部 Broker/渠道控制端口不发布到 LAN。
3. 同源 Web 静态文件/SPA、API、媒体 Range 流与实时流均接线。API 未找到不能回退 index.html；带哈希资源可缓存，入口 HTML 有正确更新策略。保留现有 HTTP 18446、HTTPS 18443、OneBot 18765 和回环观测 19490/19491 的可配置落点，证书/密钥外置。
4. 单一 schema 明确的初装配置示例与环境引用：PG DSN、Broker 内部密钥、密封凭据 master key、初装 token、路径、配额、模块、观察服务。配置缺失应给出可定位错误，不能自动创建弱密码或把旧身份/记忆/任务导入。初装 token 与密钥由发布者生成，文件权限限制，输出中不出现值。
5. 独立 PG/vector Compose 与空库初始化。候选为 NAS `/volume2/Dockers/tianshu-core-postgres`、端口 25018、`pgvector/pgvector:0.8.7-pg17-bookworm`；发布时固定实际 digest。保留现有共享 `postgres-1`，不改其镜像或数据。原生迁移必须包含所有正式域和 Procrastinate，插件 schema 显式注册；每次空安装得到同一合法结构，不启动旧迁移或 SQLite 兼容层。
6. 迁入完整 Vector/Loki 日志链路、持久缓冲、断线补传与查询，按新事件 schema 2.0.0 改 remap；不复制旧 1.0.0 解析假设，不新建 PG 日志账本。保留容量守护的实际阈值和对运行入口的保护；提供新目录/进程对应配置，使旧 writer 停用后守护仍覆盖新版。
7. 新数据域备份/恢复工具只操作明确的新版库、文件目录与密封密钥引用，输出清单和实际状态，拒绝跨目标恢复。原有数据允许丢弃，不意味着新版不需要正常备份能力。相关用例写好交 CR19 执行，不新增日常全量验收门禁。
8. 发布清单列镜像/静态资源/源码 SHA、配置 schema、迁移版本、运行身份、挂载、监听与所有者；精确的 stop/drain/start/health 操作材料由根执行。旧资源清理按经实时复核的允许清单逐项操作，无全局 prune、DROP DATABASE、FLUSHDB 或从前缀猜删除目标。清理代码必须解析路径并核验边界，停止旧写者是数据删除前置条件。

## 最终验收与切换次序

所有 A/B/C、前端和本卡代码合入后，根进行一次集中 CR19：必要的原生 PG/任务/模型隔离、来源撤回、动作未知恢复、关键生活/多模态/渠道合同、前端构建与实际关键页面，以及镜像空库启动/新数据域恢复检查。具体测试以真实实现和明确风险为准，不为凑数重复成功检查。

新镜像与配置先固定。生产切换前重新核验旧容器 ID/image、单写者、端口、挂载、数据域与容量；新数据库从空数据开始，不复制验收夹具。按授权排空并停旧 writer，清除确认的旧天枢 schema/命名空间/专属运行状态，再启用新版本。共享 PG/Redis、control-hub、AssetLibrary、Chat Audit、AstrBot/QQ 与媒体库保持独立。若验收未通过，不提前删除旧数据。

只有根完成实际操作后才能标记 deployed；工具/文档存在不表示已经切换。外部账户未配置、未真实调用的能力单独注明，不用替身通过冒称实机成功。

## 模型配置的只读核对与待验证项

旧配置的默认模型是 `gemini-3.8-flash-high`，当前实际来源协议为 OpenAI Chat 兼容格式。新装不能仅凭模型名称把它标成原生 Gemini 已可用；保留明确的协议、端点、共享账户容量及验证状态。旧功能绑定中 writing/memory 指向 `deepseek-v4.1-flash`，search 指向该 Gemini，code/tools 指向 `gpt-6.1-sol`；新功能路由需显式重建，后台不自动借用前台默认。

New API 的官方[原生 Gemini 接口文档](https://docs.newapi.ai/zh/docs/api/ai-model/chat/gemini/geminirelayv1beta)说明了 `/v1beta/models/{model}:generateContent`；其[认证源码](https://github.com/QuantumNous/new-api/blob/main/middleware/auth.go)支持相应路径的 `x-goog-api-key`。这仅提供候选接入依据，不能据此证明用户供应商的具体部署/模型已支持多模态；CR19 再验证真实配置。

旧配置还包含 Qwen3 Reranker 与 Embedding；后者最近一次旧测试是 unknown。SiliconFlow 的[模型说明](https://www.siliconflow.com/models/qwen3-embedding-8b)列出可变维度范围，但新安装仍须实际检查返回 1024 维及固定 profile，不能把旧测试记录或文档能力当作本轮成功。只读元数据保存在原根本批私有 runtime 目录；没有导出或打印 API Key，也没有在开发阶段调用模型。
