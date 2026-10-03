# 质量修复与角色独立日常：NAS 部署完成

状态：**本轮改动已合入并推送 GitHub，现役服务已部署到 NAS 并完成运行核验。** 验证时间：2026-10-03T09:50:20.316294+08:00。入口：[天枢](http://192.168.31.210:18446)。

本记录覆盖前序本地交付中的“未推送/未部署”状态。完整固定 SHA、镜像 digest、冷备、失败与恢复、服务检查、Dockge 和浏览器回执见 [机器记录](quality-life-deployment-2026-10-03.json)。

## 合入与部署范围

| 仓库 | GitHub main 实现提交 |
| --- | --- |
| platform | `3f715b6e94c6552b30e03c17497f4dd56f32292e` |
| companion | `11e6cc37057d60e389c8837926fe61352065a605` |
| memory | `8415bc85bcad089a9e24461d5f3e88039754103c` |
| chat-audit | `21e09fe6f3824b2f75b62baedccd0baabed00a2e` |
| assetlibrary | `def57a8492c13d582c7be85e978a64895b353b6f` |
| coordination（含守护修复） | `205ea1808e09f5791ff210cca16a73065492ef79` |

协调仓库随后保存本部署记录的提交不做自引用。Platform、Companion、Memory 按已验证候选快进；AssetLibrary 合并时保留主线另有的 5 项 CI/夹具修复。Chat Audit 的主线与现役发布谱系分叉，因此主线精准适配修复并通过 31 项定向测试，NAS 使用已推送 `release/2026.10.03-quality-life` 的 `66097a98e8613437f96a76ddae4e23bb7e1fd609`，没有将另线历史代码整包替换线上。

NAS 更新 Platform、Companion、Memory 和独立 knowledge 服务；knowledge 使用同一新版 Memory 镜像，保留自己的配置、数据库和 guard。NoneBot 更新为 0.4.0，Chat Audit app 与 backup-worker 更新到修复镜像。AssetLibrary 只有 CI 变化，无运行时镜像更新；现役 AstrBot 没有天枢插件，保留已构建 ZIP，不擅自新增接入。其他对话的小屋样片不属于本轮发布，原文件保留。

## 实机结果

- 十个核心及日志服务运行，五个核心的 TLS live/ready 均为 200，容量守护为 ready。
- 已通过原 Store 迁移安装 Memory source lookup 标记及两项索引，保留原 source-guard；knowledge 没有执行不适用的迁移。
- 只新增既有 `life_readers.web-life.runtime_roles=true`，复用现有读者和角色选模；角色、人格、启用状态和模型选择经摘要对比保留。
- 实际 Platform → Companion 的角色、今日计划、经历时间线读取通过；现役运行角色在没有测试对话的情况下，通过 Gateway 实际模型完成了 8 阶段今日计划，时间线已有持久经历。另一个旧生活实体保持原 1 阶段基础作息，生成状态为 unavailable，未将其冒充模型生成成功。没有创建测试角色、伪造经历或发送测试对话。
- NoneBot 两个能力接口为 200，0.4.0 安装源码一致；原实例、密钥、观察/回复策略保持。
- Chat Audit 应用、数据库、存储、备份和 FFmpeg 检查正常，版本接口为修复提交，backup-worker 检查通过。
- NAS HTTP 的 38 个前端文件与构建镜像哈希一致；新建匿名浏览器上下文访问成功、无页面错误。没有冒称已用原账号口令登录。
- 两份 Dockge 快照已备份并同步实际 CAP 配置，保留项目名、网络、挂载和凭据引用。

## 备份及执行中处理的问题

核心完整冷备：`/volume2/tianshu-v2-resident-updates/quality-life-20261003-core-attempt2/production-generation-d3eab7cef0154a81848bc9f9f1435be9/cold-snapshot`，含数据库、guard、配置和外部控制对象。NoneBot 冷备：`/volume2/tianshu-v2-resident-updates/quality-life-20261003/independent-services/backup/nonebot`。Chat Audit 一致性 PostgreSQL 备份：`/volume2/tianshu-v2-resident-updates/quality-life-20261003/independent-services/backup/chat-audit/chat-audit-postgresql.dump`，已做归档可读性检查，媒体目录未搬动。

部署前遇到守护 Docker 全局枚举竞态，原程序触发保护停止十个服务；本轮修正为在原 25 秒预算内最多重新完整采集一次。29 项针对性测试通过，持续错误、缺少 owner、挂载变化和身份替换仍拒绝。新版守护已安装并正常运行。

首次克隆验收因部署脚本漏传分页参数返回 400，自动回退后原版五核心健康检查通过。修正验收请求后采用新的冷备与第二次升级，最终成功。没有将这次无效请求归因于产品缺陷，也没有覆盖原失败回执。

本轮完成部署和上述即时运行验证；长时间跨日、真实 QQ 交互和模型生成内容的长期质量仍不属于已完成验证。原有基线测试失败与跳过详见 [本地交付](quality-life-delivery-2026-10-03.md)，未因部署成功改写为全量测试全绿。
