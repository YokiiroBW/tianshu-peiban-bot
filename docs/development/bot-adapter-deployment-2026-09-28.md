# 机器人网页适配器 NAS 部署验收

已完成天枢 Platform/Core 更新与 NAS 部署验收。入口：<http://192.168.31.210:18446/#/settings/3>。原管理员账号保留，服务重启后旧会话失效，需要重新登录。

## 发布内容

- Platform `186da00f84eb37653247c2bda05407ff89558540`，镜像 digest `sha256:561f5975b651cff3ad10997ab40e134af03d3234c6a662b574da10e30ee88ca6`。
- Companion `e85987147b1109c8a41b3782e6fc3ccc33e0e249`，镜像 digest `sha256:e86b8d10ca916d8b05a1f2b1dbce5dfe8e58abee7ce0e049f6d478d1988e0ba1`。
- 设置中的机器人独立页支持选择 AstrBot/NoneBot、地址和连接密钥、真实账号探测、角色与会话范围、保存停用、启停与显式恢复。用户无需编辑天枢/Core 配置。
- 已装配内部管理权限与 `actor:household` 角色，允许目标为 NAS 所在 `192.168.31.0/24`、现有天枢容器 `10.205.200.0/24` 与回环范围。私网 HTTP 仍须在网页显式选择；HTTPS 保留证书验证。
- 加密适配器目录在原持久数据挂载内 `/var/lib/tianshu/bot-adapters`，密钥和数据库属于同一备份单元。

## 实际部署与验收

两个镜像均在 NAS 从固定 Git archive 构建，依赖安装与镜像检查成功，推入现有回环私有 registry 并以 digest 固定。停服务后完成整个天枢部署目录一致性备份，再替换 Platform/Core；网关因来源凭据续发同步重建。其余七个服务容器 ID 与挂载保留。

10 个服务运行，restart policy 均为 unless-stopped；5 个核心就绪检查通过，容量守护 ready。原账号文件哈希保持一致，默认模型仍已配置。知识、人格、生活、记忆共 13 项实际业务 HTTP 读取通过。Platform 使用既有受管凭据调用 Core 新管理 status 得到 HTTP 200，没有创建测试 binding。日志抽验中 knowledge 台账 171 条全部验证，Loki 可读 20 条近期结果，invalid_source_lines=0；此为部署后抽验，不扩大为所有历史日志逐条重验。

局域网浏览器刷新正确到登录页，并保留机器人设置回跳地址。没有重置密码、代建账号或伪造登录会话；实际 NAS 登录后的向导未复验。部署前的真实后台浏览器桌面/手机各 1 项通过，双宿主真实框架联合 2 项通过，QQ SDK、模型与 Memory 为明确合成夹具。详细边界见产品 ADAPTER-WEB-JOINT 与 ADAPTER-JOINT 交接。全量后端既有失败记录仍保留，不声称全量绿色。

## 运维定位与回滚材料

- 更新、镜像、校验、日志和备份：`/volume2/tianshu-v2-resident-updates/adapters-20260928`。
- 当前容量状态：该目录的 `capacity-state`；systemd 服务已同步。
- 运行 Compose 保持 `/volume2/tianshu-v2-resident-updates/bots-20260928/core.compose.json` 与 `platform.compose.json` 路径，内容已更新；Dockge `/volume1/Download/dockge/stacks/tianshu-v2-resident/compose.yaml` 已同步。
- 更新目录中的 `deployment-before.tar`、`core-before.json`、`platform-before.json`、`capacity-before.json`、`unit-before.service`、`dockge-before.yaml` 与 `containers-before-private.json` 为回滚依据。回滚须协调停服务、恢复匹配配置和数据、核对镜像并重新 arm；不得简单复用旧 capacity state 或重跑首装。该回滚本轮未演练。

## 插件与真实机器人边界

最新安装包位于 NAS 更新目录的 `companion/.runtime/adapter-artifacts/`，包括 `astrbot_plugin_tianshu-0.2.0.zip` 与 `tianshu_nonebot_adapter-0.2.0-py3-none-any.whl`，实际 NAS 构建 SHA-256 见同名 JSON 证据。两种宿主适配代码已发布在本地 main 并随 Companion 镜像包含，但包尚未安装到真实 AstrBot/NoneBot 宿主；天枢管理页连接数量为 0。未发送真实 QQ 消息，未替用户选择联系人或群。

当前已完成的是天枢侧 NAS 部署。机器人实际使用还需宿主安装对应插件，取插件地址和密钥，在网页指定账号与会话并启用；真实收发验收仍等待用户指定对象。不得把部署完成表述为两种真实机器人已接通。
