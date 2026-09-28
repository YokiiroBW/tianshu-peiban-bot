# 已有能力贯通与 NAS 部署验收

2026-09-28 02:21 UTC，本轮子任务已审查集成、部署并完成真实业务验收。入口 http://192.168.31.210:18446 。使用原管理员账号登录；无需重新初始化账号或重复配置默认模型。

## 交付与证据

- Platform 运行代码 c848ce77d4974693f3e86d995c6a8d9c26330976；main 60d17f28c3cef96274c054b05532fa2d21234a1c 仅增加交接文档。Memory/Knowledge 02df5ba8c041a7a6eb8b705c5f264e10543032ec；Companion 31677983798ba27b24d57925feab4774c2eec30f、Gateway e4f112f02d28a1ded134126feed33f15e003ec20 未改代码。
- CONNECT-B/U/M 已完成；独立 CONNECT-A 固定提交审查见 `../reviews/connect-a-fixed-integrations-review-2026-09-27.md`。本地真实 HTTPS/Chromium 联调与页面回归见该报告，不能替代本页的 NAS 证据。
- 五个核心服务 readiness 均 ready；五个观测服务运行。十服务容量 guard ready，最近心跳约 1.8 秒，约 460 GB 可用。五个观测容器没有统一 Docker healthy 声明。
- 真实 HTTPS 业务读取 13/13：知识目录/正文/检索/笔记/经验/教训、生活角色/状态/日记、已发布人格、记忆概览/主体/记录。所有响应 HTTP 200，详见同名 JSON。
- 正式平台输入→陪伴→已配置真实模型→回复链路：专用验收消息 `deployment-check:connections-20260928-r1` 已完成，快照 phase=sent、回复数 1。验收文本明确不是用户个人经历，不作为长期记忆。
- 新增 Knowledge 日志经 Guard 账本验证 51 条，Loki 查询成功，服务名全部正确，非法来源行 0。
- 原管理员账号文件逐字节哈希保持不变；默认模型保持 configured；网关 platform_origin_renewal 已启用。Dockge 核心/观测 Compose 两份副本与本次部署文件同字节。

## 当前可用范围

陪伴、记忆浏览、资料目录/正文与查询、人格浏览、生活状态与已发布日记读取已经接入。当前记忆条目、笔记/经验/教训、已发布日记为空，不代表连接失败。资料包含实际使用说明 START.md；生活初始状态明确标注 fictional、last_persisted，未伪造用户行为或发布日记。

AssetLibrary 与 Home Assistant 的网页配置入口已启用，但没有配置真实外部地址/凭据，不称外部服务已连通。没有登记 Git checkout，因此 continuation 未开放；未扩大遗忘/审批等用户授权范围。小屋视觉专修仍属于独立任务。

本轮在 NAS 上通过正式应用适配器及受控服务凭据执行业务请求，未伪造网页登录会话。重启后网页显示已有管理员登录入口；本轮没有密码，因此未重新执行 NAS 上完整认证页面点击流程，不能把后端请求称作该流程已验。

## 运维与恢复边界

- 数据根 `/volume2/tianshu-v2-resident`；本次更新目录 `/volume2/tianshu-v2-resident-updates/connections-20260928`（下称 U）。现场私有文件不入 Git。
- 当前核心/平台首次启动/观测 Compose 分别为 U/core.compose.json、U/platform.compose.json、U/obs.compose.json。Dockge 保留这次更新副本，不运行旧首装流程。
- `/volume2/tianshu-v2-resident-tooling/resident-capacity.json` 已使用 `knowledge-ten` 和 U/capacity-state；systemd 停止超时 700 秒。根 guard/monitor 修订已部署，Knowledge 独立日志来源只接受 memory-knowledge。
- Knowledge 使用独立 CA、数据库、日志和只读资料目录，无宿主机发布端口。初始化依次执行正式迁移及导入，临时导入客户端已从运行配置移除。Persona 使用已发布 v1 合同及固定 manifest 哈希。
- U/deployment-before.tar 是本轮修改前完整停机备份；另保留 Companion 生活初始化前备份、旧容量配置/unit/guard、镜像源锁与镜像身份、旧 Dockge 配置。外部连接加密数据库必须和其密钥一起备份。
- **尚未执行新十服务完整备份恢复演练。旧四产品恢复/导出工具不覆盖 Knowledge，禁止直接用于本版恢复。** 后续备份需要覆盖完整数据根、新 Knowledge 数据/日志/配置/资料、加密密钥、Compose 与十服务容量清单。回滚必须停受管服务并协同容量监督状态，不能只替换容器镜像或盲目覆盖数据库。

## 保留的失败与修复记录

初始旧服务的日志/网关配置就绪失败，首条部署验收输入被拒绝，未把它计作成功。预检一次性容器挂载受管目录触发旧容量 guard 的 foreign_deployment_mount，九服务被保护停止；只清理本任务九个已退出验证容器，保留数据与失败证据。首次安装前置检查因全停止状态拒绝，之后明确接受统一停止状态、完整冷备后继续。

新目录初始权限导致平台启动失败，已精确修复新 Knowledge/Persona 合同目录权限并重新通过预检。正式消息接受并生成账号对应人物/会话范围后，使用官方 issuer/resolver 接通记忆；生活状态通过领域接口初始化。全部容器变更完成后才启用新容量状态并验收。不要在容量监督运行期间创建额外挂载受管数据根的验证容器，即使只读也会触发保护。

最终现场回执 U/completed.json；本仓库同名 JSON 为不含凭据或聊天内容的摘要。U 中保留各步骤日志和原失败记录，禁止重跑带 attempted 标记的安装脚本或删除旧证据。
