# 机器人首轮集成与 NAS 更新

2026-09-28。用户授权继续推进；两种机器人均接入开发，真实测试对象稍后指定。本轮已合并平台及陪伴产品主线并更新 NAS 后台能力。**尚未在真实 AstrBot/NoneBot 宿主启用插件，尚未进行真实机器人收发。**

## 固定版本与部署位置

- Platform main / NAS：`c5f4d3acc3a906376c5dfe36279ef26b91769b25`，镜像 digest `d549ed6fa1ffdb5ae9b94ec9da421ef35c8462aeb35f4dfdf98a16fb99abf59c`。
- Companion main / NAS：`484240a9244dcd62dec22c174d5281a715aaae10`，镜像 digest `f5cf98fb5ccd22fe6f763469bed92781cb4bcaa344887cd2fe172736c53148c7`，包含固定 N361585c 与 A910ce8c。
- Gateway 与 Memory/Knowledge 镜像不变；网关更新授权后重建，Memory/Knowledge 为对齐新 Compose 标签重建，既有配置及数据挂载保留。
- 本次更新目录 U：`/volume2/tianshu-v2-resident-updates/bots-20260928`；core compose 为 `U/core.compose.json`；观测 compose 仍为 `connections-20260928/obs.compose.json`。
- 容量配置 `/volume2/tianshu-v2-resident-tooling/resident-capacity.json` 指向 `U/capacity-state`，systemd 同步；Dockge 的 `tianshu-v2-resident/compose.yaml` 已同步。
- 访问地址仍为 <http://192.168.31.210:18446>。不得重跑首装或旧四产品恢复脚本覆盖本轮状态。

## 已执行与现场验证

更新前保存完整停机备份 `U/deployment-before.tar`、SHA 收据、原账号校验值、旧 capacity state/配置/unit、容器元数据和 Dockge 配置。新镜像按固定源码构建，平台配置在隔离容器预检通过。

新增独立 bot-ingress service principal，仅授予 `source.register/source.dispatch/mapping.prepare`；网页管理员增加 `bot.manage`。连接配置 `slots:{}`，没有虚构真实账号、会话、作者或连接凭据。平台实际实例读回 `available:true, slots:0, connections:0`。原管理员文件逐字节校验保留，默认模型仍 configured；平台日志持久预算 5000ms 保留。

十个服务 running / unless-stopped，五个核心健康检查均 ready，容量 guard ready。真实服务 HTTP 读取 13/13 通过：资料目录/内容/检索，角色/生活/已发布日记，人格与记忆浏览。Knowledge 日志入完整性台账 81/81 verified、invalid_source_lines=0，Loki 查询成功并返回近期记录。旧部署验收对话仍为 sent / 1 reply；此项是保留历史读回，**不是本轮新模型调用或真实机器人收发**。

浏览器实际显示正常登录页面；当前会话已过期，不重置密码，不伪造用户会话。登录后的管理操作本轮未复验。首页持续 HTTP 观察结果及末次现场读数见同名 JSON；有限观察不能代替长期稳定性验收。

首页持续观察实测 121 次 / 364 秒，均返回 200 与 HTML。末次容量心跳 age 2.913 秒、十服务 ready。

## 插件产物

已将下列产物按哈希核验暂存 `U/plugin-artifacts`，没有安装到其他现有服务。离线隔离 Python 3.12 安装、pip check、禁用预检、合成配置 NoneBot 插件加载通过，不启动 driver。

| 产物 | SHA-256 |
| --- | --- |
| `tianshu_companion-0.1.0-py3-none-any.whl` | `cea5e02357fef0f40fe4665d978eb7c95d7b2485b6ae5da543bb9c272e3c7e22` |
| `astrbot_plugin_tianshu.zip` | `878fae4c8bfa486f3b610499e813897e9eeaf1a8df93d8cdc7f582ba3a2e8f38` |

AstrBot 包默认 disabled。SDK 本地验证组合为 NoneBot 2.5.0 / OneBot 2.4.0，NAS 目标 AstrBot 为 4.27.3。实际 NoneBot 宿主待确认；首轮仅 QQ 纯文本。详见 BOT-N 固定打包交接提交 `67e0e94`。

末次消费者合同核对新增阻断：原 AstrBot 包将出站文本也限制为 8000 字符，而 Core 可产生至 32768 UTF-8 字节的单段；8001 ASCII 在 SDK 零调用时被记 unknown。已返修 A；上表原包仅为历史暂存证据，返修完成前不得安装启用。合同原始包 `f79280b` 已归档但不宣称全面冻结，QQ 消费者范围与此边界须同步验收。

## 更新期间故障与限制

开始更新前，旧十服务已因容量监督的 `docker_command_failed` 保护停止。已保留早期不确定停止收据与最终 stopped/errors=[] 收据；同 binary 完整快照随后成功、容量充足、十个旧 ID 完整退出且 restart=no。原始 Docker 非零原因及约 35 分钟心跳空白仍未解释，不能宣称已根治；独立报告为 `resident-guard-docker-failure-2026-09-28.md`。

本轮首次 arm 因 Memory/Knowledge 容器仍引用旧 compose 文件标签而拒绝，未修改保护规则。确认只有这两个身份标签不匹配后，以相同镜像/配置/数据重建这两个服务，再 arm 十服务成功。初次失败记录与最终读回均保留在 U。没有删除旧失败 state，没有放宽身份或容量检查。

本次结论限于**后台更新及现有功能复验通过、机器人能力待绑定**。下一步由用户指定实际机器人账号与测试群/私聊及允许作者后，统一配置 Sources/Core binding、HTTPS/CA、插件凭据及持久 journal，安装并启用限定连接，验收真实往返、去重、停用和断线恢复。当前空槽位需要部署方据真实对象配置，不代表已具备随意添加机器人账号的完整自助流程。独立 AssetLibrary、家庭/设备服务及其他智能体接入不在本轮完成范围。

## 最终长回复返修收尾

上述新发现已关闭于 AstrBot `589c99c087ee884e87639e6ee26ea94d7e1c6224`，串行合入 Companion main `ad180ca34c36c657f4a8acccb1e731cd434c3e03`。仅独立插件、测试、说明改动，后台服务器继续运行已验镜像 484240a，无需重启。总控相关 12 项通过，HTTP fixture 因协调检出层级错误首次未运行，切至同实现 BOT-P 检出后 1 项通过，覆盖 8001 sent / 32769 failed；字节边界、重启重复领取、私有 CA 测试通过。

最终可安装包为 `U/plugin-artifacts/astrbot_plugin_tianshu-r2.zip`，SHA-256 `c57463ca0ba80ce1aeae53bb8f71c41b46b35d46593f89d13ce6271ba98851de`，默认 disabled；`astrbot-current.json` 指向该包。旧 ZIP 保留作历史证据，不再作为安装候选。NoneBot wheel 不变且不包含 AstrBot 插件。根 `contracts/bot-connection/v1` 发布限定 QQ 实现配置（32768 UTF-8 字节），通用 schema/TG 不宣称全面冻结。当前仍无真实连接和机器人消息。
