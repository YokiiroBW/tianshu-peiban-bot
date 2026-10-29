# OneBot WS 机器人接入交接（2026-10-29）

## 目标与状态

按用户确认的 AstrBot 式流程：创建机器人即建立可接入的反向 WS 服务，SnowLuma 填写该机器人的地址与 Token 后，成功握手自动固定 QQ 并登记现有账号观察策略，不需要用户手动检测。2026-10-29 已按用户授权串行部署至 192.168.31.230；候选分支未合主线、未推送。

## 组成与基线

- Platform：`C:\YOKI\Codex\onebot-onboarding-platform`，`codex/onebot-onboarding-20261029`，基线 `73c4f09`，候选提交 `31f7bcc`。
- 统一宿主：本 worktree，基线 `58e0dd0`。监听复用原 18765 socket，按独立路径与 Token 鉴权，实际 SDK 接受握手后固定身份；原 `/onebot/v11/ws` 保留。宿主通过 ThreadedExchange 和现有 SDK 观察服务通信。
- 配置写入 Platform 现有加密 catalog 的独立 `onebot_servers` 表；保留原账号 rows，启动时增建表。Platform 自动复用观察策略和内部 SDK 通道，群默认停用、私聊默认仅观察。
- UI 创建、复制地址与 Token、显示连接状态与 QQ；保留外部宿主兼容入口。自定义 Token 可选，留空自动生成。

## 实际验证

- Platform 定向先有 11 项通过、PostgreSQL 专项 1 项因未配置隔离库跳过；随后在本机独立 PostgreSQL 16.15 样例库完成该专项，新增表、原记录、身份与重启均通过。服务只监听本机，测试后停止，不连接生产数据库。
- 实际 NoneBot SDK socket 及握手拒绝、断线、旧入口、失败握手不绑定：3 项通过；统一宿主跨 owner 线程握手与身份重读：1 项通过。
- 实际 OneBot SDK + Platform + 合成 WS 客户端联合：握手、`get_login_info`、自动登记及账号策略 `ready` 通过；未发送 QQ 消息。最初样例复用一个环境变量造成 Platform 设置冲突并在 Windows 清理临时目录失败，使用独立测试环境变量后通过。
- 前端 typecheck、构建、定向桌面/手机浏览器 4 项、改动文件 Ruff 与 diff 检查通过。预览 `http://127.0.0.1:5174/#/settings/3` 使用内存合成数据，连接状态为演示。
- 自动审批拒绝删除失败样例的 `C:\Users\Administrator\AppData\Local\Temp\tmpmjqrjckd`，理由仅为 `blocked by policy`，目录保留。

## 生产发布与剩余事项

生产版本 `2026.10.29-unified-linux-r26a`，镜像 `sha256:999cb29f49b6969f5d9e1cd096ec3269ea4200ea9354402b2c5b9d9e17edf458`，容器 `7f7339e236960490b9d952f14ec395cf468971c78fa5fd8c096c60e6a05e8651`，容量守护 `unified-capacity-20261029-r21`，20 GiB 底线不变。Platform `31f7bcc`、宿主 `5badd01`；依赖继承 R25，原 R25 镜像保留。发布前已将 PostgreSQL `bot_adapters` 命名空间和加密 key 一并备份并验证原加密记录；新表 `onebot_servers` 已创建，原记录除运行时 `last_checked_at` 变化外内容一致，原收据与 key 不变。

首次 R26 增量镜像因打包时目录权限过严导致业务进程无法启动，未产生成功启动的业务会话；已在 R26a 修正，并以运行用户 10001 验证 341 个 Platform 文件、56 个宿主文件、83 个网页文件及关键导入。最终六业务 ready、鉴权 ready 200、37 挂载一致、1133 份配置保留（仅既有范围启动租约续期），其他 13 个容器未改变，守护 active/armed、0 重启/无 OOM；最终容器 healthy。既有 obs-guard 日志对账超时/503 仍有波动，非本次新增故障。容量余量约比守护底线高 134 MiB，后续发布前须重新核对。

真实网页刷新后已核对默认“内置 OneBot v11（QQ）”创建表单；网页与脚本 HTTP 200，未知新路径握手返回 403。SnowLuma 中原 1200 和 9091 的旧 WS 节点均已重新显示已连接；没有改其节点，没有创建真实新机器人或发送 QQ 消息。新路径的真实 SnowLuma 握手和自动绑定需实际创建机器人并逐账号切换客户端后再验。新服务复用 18765，每个机器人路径不同。
