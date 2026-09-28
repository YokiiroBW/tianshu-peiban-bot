# 机器人默认观察：NAS 更新完成

2026-09-29 已完成代码集成、备份迁移、镜像升级和部署验收。入口保持 http://192.168.31.210:18446/#/settings/3 ，位于任务与设置→机器人接入。NoneBot 账号 1365939091 已登记默认账号观察，群聊和私聊均 `observe=true / observe_only`，无回复角色、无名单限制。网页可分别调整群私策略；名单控制回复。

## 固定版本与现场

- Platform `04cbe8a6c0652c4d1ddec572afce1b895416f145`，镜像 digest `92db80dec5c98b88733ba08715b998112094942b155206e64f855c30b132a269`。
- Companion `6adf480f292c49fa48950ef863cf80b6e31c1794`，镜像 digest `44c200a814e88262d1b7e60da4b208e2f8a6704ec42c6016b6d2ac5f30f3293f`。
- Memory `10214b2f7dbfb6990e986f38f92c8103e07d6b9e`，镜像 digest `2f2f947da5488b225d2b096b3f30554db6d4b10dd23f0a8c8745851199a7fff6`。
- NoneBot 插件 0.3.0，保留 NoneBot 2.5.0/OneBot 2.4.x 宿主依赖；新镜像 `local/nonebot2:2.5.0-tianshu-0.3.0-6adf480f292c`。已核对新协议、原 instance_id、原连接密钥摘要一致和账号在线。
- Gateway 镜像保持，因来源凭据续签重新创建；Knowledge 与五个日志服务镜像及容器 ID 保持。Dockge 已同步。

更新现场：`/volume2/tianshu-v2-resident-updates/observation-20260929`。容量配置的 state_dir 已指向其中 `capacity-state`；运行 Compose 仍在 `bots-20260928/core.compose.json` 和 `platform.compose.json`，内容已更新。不要重跑旧首装或历史迁移脚本。

## 已验证

十服务容量 guard ready、五核心真实 TLS 就绪接口 ready；NoneBot 宿主健康。Platform→Companion→Memory→Platform 来源权限核对的真实鉴权查询通过（合成空会话，只读，不向 QQ 发消息）。账号文件哈希保留；Memory 明确迁移到 observation_schema=1，原 schema 仍3。日志检索返回近期五核心事件、完整性计数无 invalid_source_lines。升级后浏览器正确显示现有管理员登录页。

默认观察账号 state=ready、enabled=true、last_error=null，群私均仅观察。最终采样宿主观察0、积压0、回复认领0、发送0；Memory 归档0。现场还没有收到新的真实群私消息，因此不能声称真实QQ消息已归档。没有主动测试消息，没有真实模型/QQ回复测试；生产账号登录后的新设置表单未由总控重验。本地桌面/移动44项和独立真实HTTPS联合已通过，详见集成报告。

AstrBot 0.3.0 ZIP 已随固定源码在NAS构建，但未安装到真实AstrBot宿主；本轮只更新现有NoneBot。仅观察不拦截其他宿主插件，其他插件仍可能按自己的配置回复。

## 备份与恢复边界

停写后冷备 `deployment-before.tar`、`nonebot-before.tar` 及各自 SHA256，迁移另备 `R/data/memory/pre-observation-20260929.sqlite`。更新目录保留原 Compose、unit、容量状态、Dockerfile、私有 inspect 与迁移回执。需回退时同时恢复匹配版本数据和配置、重新 arm 容量守护；不能仅恢复旧镜像保留新 schema，也不能盲目重跑已执行脚本。未宣称回滚演练完成。

机器证据：[bot-observation-deployment-2026-09-29.json](bot-observation-deployment-2026-09-29.json)。本地集成证据：[bot-observation-integration-2026-09-29.md](bot-observation-integration-2026-09-29.md)。
