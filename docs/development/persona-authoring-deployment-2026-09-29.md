# 人格档案编辑 NAS 部署

2026-09-29 已部署到 http://192.168.31.210:18446/#/companion/2 。入口：陪伴 → 人格与世界 → 创建与编辑。支持新建、查看、编辑、复制、保存草稿，以及明确应用到既有角色；四个文本字段为人设、语气、表达风格、称呼。草稿不改变运行人格，应用影响之后新准备的轮次，在途快照保持不变。浏览器后退与版本冲突保护输入，目录区别当前生效与曾应用。

## 固定版本与验收

- Platform fd1eb9538bb78252859cb1128bf33be24670a5e9，镜像摘要 `1391cb6ebeff3954b8d7cf752d32f76b9384b24dec2308bbe68e8f1f5a24c5cb`。
- Companion da3907288d816bef1c7d25df6cd0f74de012fc3a，镜像摘要 `a54bffafb37650b17926e8fbb69724c3eb326f6e51e3c9ef95a9c7ab68204431`。包含总控补齐的未启用 Personas 旧角色配置兼容，不可改回 f56a2dc 单独部署。
- 新合同 `contracts/persona-authoring/v1` 已由总控校验发布，原 `persona-management/v1` 未改。详细代码审查见 `reviews/persona-authoring-review-2026-09-29.md`。
- 总控人格/实际模型请求测试 6/6；真实隔离 HTTPS 浏览器 2/2，覆盖创建、编辑、应用、复制、当前/曾应用、后退、冲突、权限和登出。桌面/手机截图已审查。旧只读回归 47 通过、1 项 Windows 临时 SQLite 清理错误，单独在新旧代码均通过；不宣称该整套全绿。
- NAS 十服务容量 guard ready、五核心 ready；平台使用专用 HTTPS 凭据读取新增人格目录和既有角色成功。页面 CSS/JS 的 LAN HTTP 内容摘要与新镜像一致，五条编辑 API 未登录均 401。
- 原管理员账号、现有一份人格及修订/发布摘要保留。NoneBot 镜像保持原样且健康，群/私仍仅观察；鉴权归档查询与 Loki 日志读取通过。未发送 QQ 测试消息。

实机验收未写入测试人格、未替换用户人格，也未进行登录后的生产网页写入演练。创建→应用→实际模型请求的写闭环在隔离真实服务上验证；生产现场验证部署、凭据读取、接口门禁、页面资源和数据保留。不会把 ready 中的 dependencies/model `not_verified` 称作本次真实模型全链路已验证。

## 配置与恢复点

平台原 `web_personas` 增加 `authoring_enabled=true`、`apply_subjects=[actor:household]`；原 Web operator 增加 `persona.create/edit/apply`，沿用原专用凭据与 CA。Companion 原 v9 数据库没有迁移、没有生产人格编辑。

更新目录 `/volume2/tianshu-v2-resident-updates/persona-authoring-20260929` 保存停写冷备 `deployment-before.tar`、SQLite backup `companion-before.sqlite`、旧 compose/guard/unit、账号与人格摘要及验收证据。不要重跑已执行的 update.py。恢复应先停止当前容量守护与服务，再按同一次备份组合恢复配置/镜像/必要数据库；不可直接覆盖正在使用的数据库或只恢复一份 compose。

新容量 state 为本更新目录的 `capacity-state`。运行 compose 仍为 `/volume2/tianshu-v2-resident-updates/bots-20260928/{core,platform}.compose.json`（内容已更新），Dockge `/volume1/Download/dockge/stacks/tianshu-v2-resident/compose.yaml` 已同步。平台、陪伴及刷新来源授权所需网关已重建，其余七个容器 ID 保留。详证见同名 JSON。
