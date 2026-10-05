# 可更新角色技能部署交付（2026-10-06）

用户恢复推送和部署授权后，技能第一版已推送 GitHub main，并部署到 NAS。仅更新 Companion、Platform 及两者合同闭包；Gateway 因停机期间服务授权到期，使用原镜像、同一既有授权条目重新签发并恢复启动。未更改 GSCore 服务。

## 固定版本

| 项目 | 提交与镜像 |
| --- | --- |
| 协调与合同 | `3c38a69362b8a9ab26f102189b0efa9e67b19b74` |
| Companion | `e0e7f98dc6b9d2266e479dcca2f2286b9983263a`；`sha256:8bb72e973098e883eed46cc14615e5e7d3db9e52534f817d461ccee39e23bb99` |
| Platform | `870e6fa958b7a8892a1273b6d87535ab4a02b025`；`sha256:aaa646487d146fb6b7c327cd274013eb1b4a812470dcf9f22956a35ab8b000a0` |

三仓库 main 均非强制快进推送并通过远端引用读回；本部署记录的后续文档提交不改变镜像来源。生产合同目录为 `/volume2/tianshu-v2-resident/contracts-skills-3c38a69362b8`，新增 `skills/v1` manifest SHA256 为 `0ad3a444e8bff26721c14664260d08b463feb3288a556364e1af35f3e277d0f2`，其余既有合同 pin 保持。

入口：[任务与设置 → 角色技能](http://192.168.31.210:18446/#/settings/8)。逐角色目录、启停、目录更新、生图技能和 GSCore 适配复用既有工具循环、凭据管理及生图任务。实现与本地测试详见[本地交付](skills-v1-delivery-2026-10-06.md)。

## 实机验收

- 十个受管理容器运行，五核心服务经 TLS 验证的 `/health/live` 与 `/health/ready` 均为 200；capacity guard 为 ready。健康端点不代表真实模型或所有远端依赖已验收。
- 真实澄汐角色 `skills/read` 返回 200：`life.manage`、`memory.propose`、`image.generate` available；`game.guides` 已安装但 not_configured；三类媒体、订阅及网页搜索为 unsupported。两产品技能合同 pin 一致。
- 56 个 HTTP 网页资源与固定 Platform 镜像逐一匹配，缓存头 `no-store`。没有使用真实已登录浏览器会话；网页交互证据仍来自本地隔离测试。
- 用户状态与 settings 哈希保持，包括角色、模型设置及 ComfyUI 连接和绑定。原 core/frontend 网络、挂载与发布端口保持。除两款产品和恢复授权所需的 Gateway 外，其余七个原容器 ID 保持。
- Dockge 两份 Compose 快照已备份并同步，最终实际字节与源配置匹配；OBS 配置哈希保持。最终读回为十容器运行、guard ready，见 `dockge-sync.json` 与 `final-readback.json`。
- 本轮未主动触发真实 QQ、模型、GPU 或 GSCore 技能调用；恢复后的后台正常业务与人工测试分开表述。

安全回执位于协调工作区之外的 `.runtime/skills-release-20261006/`：`resident-resume-poll-result.json`、`skills-read-result.json`、`http-assets.json` 及镜像构建回执。凭据、生产账本与完整运行配置不入 Git。

## 停机与恢复经过

部署前十个天枢 owner 已被既有容量守护停止。2026-10-05 23:41 CST 记录为 `docker_command_failed`，随后全部正常停止；历史记录没有具体失败命令或 stderr，不能宣称已确定或修复其根因。本次 Docker 查询正常，守护源码未修改，旧失败锁存保留，启动验收后使用 fresh generation 正常 arm。

首次启动未通过：Gateway 原服务 origin 于 23:45:44 CST 过期，实际 TTL 为 300 秒；条目、owner、路由及凭据均有效且未撤销。Gateway 在续租环节返回 `forbidden`。通过 Platform 受支持的 local issue 为同一 `gateway → platform / config.snapshot` 条目重新签发，旧 origin 行和审计保留；未新增权限或服务凭据，未绕过过期校验。仅重建 Gateway 注入新 origin，原镜像、其余环境与网络保持；platform-first 配置同步该变化。

旧续接前置检查要求容器退出码为零，会拒绝已查明退出 1 的 Gateway；在签发及启动之前停止。改用本次明确的全停止、容器身份与挂载检查后续接成功。原失败回执均保留，没有重复启动未知任务或自动还原旧数据库。

冷备位于 `/volume2/tianshu-v2-resident-updates/skills-release-20261006-release-v2/cold-snapshot`，33 个 SQLite 数据库通过离线恢复验证。续接复用此备份，未生产迁移、未恢复旧账本。回退产品应使用保留的旧镜像与部署定义；不能直接覆盖恢复运行后新增的生产数据。

## 未完成范围

GSCore 的真实服务 HTTP 关闭，现役实现仍有消费者及终态缺口，本次没有改动它；因此游戏攻略/配队尚未真实联通，详见[接入诊断](gscore-integration-2026-10-06.md)。媒体聚合、下载、订阅和网页搜索仅保留扩展目录，未实现处理器。NAI 与其他在线生图适配器也未安装，不作为本轮可用能力。
