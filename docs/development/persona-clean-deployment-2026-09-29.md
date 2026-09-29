# 人格页面精简与 NAS 更新

2026-09-29 已部署到 http://192.168.31.210:18446/#/companion/2 。左侧统一人格列表、搜索和新建；右侧直接查看正文或编辑。名称和人格正文为主要输入，语气、风格、称呼等收于“更多设置”。保留保存草稿、保存并应用和复制。移除页面中的旧版本控制台、批准/比较、技术标识、重复说明和无意义统计；历史与人格数据仍保留在后端。

## 版本与验证

- Platform 固定提交 `9bed67e2de8a101f55f9a95e9a8b1288719ce8c2` 已合并 main。
- NAS 镜像 `127.0.0.1:19550/tianshu/platform@sha256:2b8605bb64b5db5f670f999f726f8f4dd1d67d1683d2e629d6785eb834cd835a`。
- Companion 保持 `da3907288d816bef1c7d25df6cd0f74de012fc3a`，本轮不改后端、合同或数据库结构。
- TypeScript、Vite、格式检查通过；隔离真实 HTTPS Platform/Companion 浏览器用例 2/2，覆盖创建、编辑、应用、扩展字段保留、冲突保留输入、离开保护、只读权限与手机切换。桌面/手机截图已由总控审查，见产品 `docs/handoffs/PERSONA-CLEAN-20260929.md` 和同目录截图文件夹。
- NAS 十服务容量守护 ready、五核心 ready；专用凭据读取人格目录与既有角色成功，LAN 页面资源哈希与镜像一致。五条人格编辑接口匿名访问均 401。
- 现有账号、人格/修订/发布数据摘要均保留；NoneBot 镜像不变，群私均仅观察。鉴权归档查询和 Loki 近期五服务日志检索通过，日志无非法源行。

浏览器写闭环在隔离环境执行，生产现场仅作只读验收，没有覆盖用户人格或发送 QQ 消息。本轮没有重新进行真实模型对话测试；ready 内的 not_verified 不代表这些外部依赖已实测。

## 部署与恢复

更新目录 `/volume2/tianshu-v2-resident-updates/persona-clean-20260929`，包含一致性停机冷备 `deployment-before.tar`、旧 compose/guard/unit、账号及人格摘要和验收记录。新容量 state 在该目录 `capacity-state`。运行 compose 仍为 `/volume2/tianshu-v2-resident-updates/bots-20260928/{core,platform}.compose.json`，Dockge 配置已同步。

Platform 被替换；为更新来源授权重建 Gateway，其余八个容器 ID 保留。不要重跑一次性 update.py；恢复需先停当前守护与服务，再按同次备份组合恢复配置和镜像，不覆盖运行中数据库。精确证据见同名 JSON。
