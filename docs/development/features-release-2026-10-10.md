# 本地功能检查与发布收口 — 2026-10-10

用户授权检查本地工作树、提交和部署。NAS 只读核对确认 Platform `cbd592b739450542b5667d9980cef0c2aaa7291d`、Companion `537ea036298a239b8dae29baf7dba1f7ea4e39c5` 在运行，十个管理容器及 guard ready。技能、天气设置、用户管理员开关和图片投递修复已经上线，不能再次按未部署功能覆盖。

## 本地集成

Platform 视频订阅候选 `123acfa302cfe27969746584c406b98ecda8d200` 基于较早的 `6a2ed3d`。在隔离检出 `worktrees/features-release-20261010/platform` 从线上版本合并，提交 `619c944924f582e9fe472f89d666d2da03c6f966`。解决 server/web_console 两处冲突：保留当前 delivery、proof、credential 路由和 45MiB 图片发送上限；媒体请求独立 6MiB 上限与生活内容 1MiB 上限并存。媒体生命周期仅在内部监听器启动。

复用现有媒体模块、任务中心、登录/CSRF、应用壳和全部既有线上功能。订阅管理、链接下载、任务及账号媒体库使用独立侧栏 `/#/subscriptions`；下载发布最终由独立 AssetLibrary 负责。

合同提交 `43a388847d077d367a9ba257a807cb6338e5ea48` 保存已验证的 media-package/v1，保留 production_publish_authorized=false 的真实状态。独立 AssetLibrary `7742d1ed` 已有本地提交与联合验证，但生产媒体库、目标和独立产品主线迁移整合尚未确定，没有将候选直接覆盖其主线。

## 本轮验证

- 最终合并状态 TypeScript、Vite 构建及冲突文件 Ruff、Git diff 空白检查通过。既有 renderer 大包提示保留。
- 最终媒体/订阅导航浏览器 69/69 通过，三尺寸合成 API 数据。
- 媒体、WebConsole、任务中心、适配器实际 61 项：60 通过、1 条件跳过。首次命令误列不存在的 test_bot_delivery 模块，额外一个导入错误明确保留，未称该命令全绿。
- 正确的 test_c4_delivery 10 项：9 通过、1 条件跳过。两个条件跳过不作为跨产品实联通过。
- 初次后端使用根工作树合同因 CRLF 原始哈希不匹配失败；改用已发布 f3c4789 原始 git archive，加上媒体合同后完成上述验证，没有放宽哈希校验。
- 未变的媒体纯元数据、AssetLibrary/PostgreSQL/Emby/Jellyfin 合成媒体联合验证沿用 10 月 9 日交付，不重复或冒称本轮重新执行。没有真实 B 站账号扫码、个人 Cookie 或实际视频下载验收。

## 保留与待处理

根工作树 8 项已跟踪改动和大量历史未跟踪文档保留，未批量 git add。旧小屋设计对齐/样片工作树保留，未将其旧基线整体覆盖当前小屋。陪伴补全工作树中的 6 个未跟踪生活 UI 文件为旧并行遗留，未混入发布。

实际媒体库目标已向用户询问；完整下载发布仍需 AssetLibrary 目标、独立发布凭据及可选 Emby/Jellyfin 配置。不得以页面部署宣称下载入库已可用。

## NAS 部署结果

Platform `619c944` 已非强制快进推送 GitHub main 并部署。镜像为 `sha256:bee9d5cffd496924e37c63097b47f59f77d3b781b004014a7fd62d162cf490cb`。入口：[订阅](http://192.168.31.210:18446/#/subscriptions)。独立入口、账号管理和媒体运行基础已上线，目标数为零，完整下载发布尚未启用。

以固定 Git archive 构建完整仓库 Dockerfile，安装新增锁定依赖与 FFmpeg；Linux 86 个运行模块导入、CLI、pip check、源码/合同/网页字节核验通过。无网络、无生产挂载的临时容器中，媒体 owner 启动、空目标读取、关闭通过。首次镜像校验把 pip 生成的 `__pycache__` 计入源码集合而失败，校正校验范围为源文件后对同一镜像通过，没有重建或更改产品代码。

沿用既有发布冷备/恢复检查、容量守护、串行启动与 Dockge 同步能力，仅重建 Platform，其他九个容器 ID 保持。Gateway origin 没有重新签发。五核心 TLS live/ready 全部 200，十容器运行，guard ready；实际 HTTP 60 个静态资源与镜像哈希一致，no-store。服务健康不代表全部外部依赖、模型或真实账号已验。

冷备路径 `/volume2/tianshu-v2-resident-updates/features-release-20261010-release-v2/cold-snapshot`，33 个 SQLite 数据库离线恢复验证通过。新增独立媒体侧库，原业务库未执行历史迁移。平台配置仅新增 `media`，空 targets、启用账号与解析基础，媒体合同显式读取固定镜像内快照；既有合同挂载、所有其他配置、角色、模型、管理员 grant、天气密文保持。两份 Dockge 快照与实际 Compose 字节一致。

第一份发布脚本仅暂存，没有启动；本地生成代码审查时重做 JSON 换行处理，语法与实际 JSON round-trip 通过后使用独立 release-v2 执行。没有在该调整期间停生产或写业务配置。广域 Docker 容器列表只读查询两次超时，目标十容器的既有精确查询正常；未扩展排查或修改独立服务。

验证未创建生产网页登录会话，没有触发真实 B站/QQ/模型/GPU 测试。实际媒体库选择、AssetLibrary 主线与迁移整合、专用凭据、可选媒体服务器、真实下载仍待完成。旧镜像、冷备和发布定义保留；回退不能直接用旧账本覆盖部署后新增用户数据。

脱敏汇总见同名 JSON。私有执行回执位于根 `.runtime/features-release-20261010/`，生产配置与凭据不入 Git。
