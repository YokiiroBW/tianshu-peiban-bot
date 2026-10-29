# OneBot WS 机器人接入交接（2026-10-29）

## 目标与状态

按用户确认的 AstrBot 式流程：创建机器人即建立可接入的反向 WS 服务，SnowLuma 填写该机器人的地址与 Token 后，成功握手自动固定 QQ 并登记现有账号观察策略，不需要用户手动检测。当前为隔离本地实现和验证，未合主线、推送或部署。

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

## 发布前剩余事项

按统一单写者流程集成 Platform 与宿主候选，发布前核对现有版本/容量/数据并针对 catalog 做备份。生产当前只映射 18765，新服务无需增加 Docker 端口；每个机器人路径不同。部署并切换客户端后才可验真实 SnowLuma 的新地址。现有 1200 和 9091 的旧 WS 已连通，不代表新功能已部署或真实聊天验收。
