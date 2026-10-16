# 生活页视觉与真实天气：已部署 NAS

状态：**Platform `480df16c9385ce9f9f5d7a9d38f0a41a334915d4` 已合入 GitHub main 并部署 NAS**。入口：[陪伴 → 生活与日记](http://192.168.31.210:18446/#/companion/1)。本记录覆盖本地交付“尚未部署”状态。

保留现有应用壳，按用户批准概念图落地今日概览、当前活动、日程时间轴、心情和日记入口。日程以摘要显示，悬停/键盘可预览，点击阅读完整原文，手机支持点开；后台独立推进，网页定时只读刷新。历史经历仍分日分页，未来阶段不写成已经发生。

和风天气使用平台原加密连接目录，网页可设置专属 API Host/API Key，搜索并逐角色选择位置。顶部时间以服务端当前时间为基准、按实际位置 IANA 时区显示；角色日程保留原业务时区并明确标注。天气展示温度、体感、风力、数据来源和获取时间，失败保留旧值并标记。没有密钥回显、没有新依赖或数据库迁移。**用户尚未配置自己的和风凭据，因此没有真实和风账号联验，也没有把合成温度说成实况。**

## 发布与核验

- 仅替换 Platform 镜像：`127.0.0.1:19555/tianshu/life-visual-platform@sha256:05a7f57a3a0ed754041b319e7fcdd01c6f34fc5fcf8f6f3056d0dab902ca8a47`；其余现役镜像和服务配置保持，原角色/人格/模型设置经摘要核对保留。
- 先完整冷备，再隔离副本验证，随后正式切换。备份：`/volume2/tianshu-v2-resident-updates/life-visual-20261003-core/production-generation-024ee07d4d3d40129d8a081feb1c8e89/cold-snapshot`。新增weather记录后若回滚旧版本，需要同时恢复external目录同批备份。
- NAS 十服务运行、五核心 TLS live/ready、guard ready；实际授权生活与天气状态HTTP读取通过。没有使用用户口令登录或发送测试对话。
- HTTP 41份网页资源与已验证镜像逐一匹配；匿名真实浏览器入口200、无页面错误；Dockge两份快照已同步。
- 本地TypeScript、构建、涉及文件格式通过；浏览器16项定向用例、天气后端11项、外部连接/实际本地HTTP入口5项通过。没有进行全量回归。截图使用隔离合成角色和天气。

配置位置：进入生活页的 **设置天气**，填写和风控制台的 API Host 与 API Key，再搜索选择城市。连接供角色共用，位置各自保存。密钥无需发送到聊天中。

机器记录：[部署回执](life-visual-deployment-2026-10-03.json)。产品实现：[交接](https://github.com/YokiiroBW/tianshu-platform/blob/480df16c9385ce9f9f5d7a9d38f0a41a334915d4/docs/handoffs/LIFE-VISUAL-20261003.md)、[和风接入说明](https://github.com/YokiiroBW/tianshu-platform/blob/480df16c9385ce9f9f5d7a9d38f0a41a334915d4/docs/platform/weather.md)。
