# 2026-10-12 记忆部署与视频订阅核验

用户明确授权提交并部署记忆与视频订阅更新。Memory `165307f78664fe60ccad1c70b76ba26ad19d05f3`、Companion `4e4fc8322661e0afaf91b3c67c139442c8d257eb` 已非强制快进推送 GitHub main 并部署 NAS。

视频订阅更新已于 10 月 11 日发布，当前 Platform `7d2403c1f4aae5472c658fd9631b334daf7debe9` 已包含订阅概览、子导航及模型功能配置；本次核实实际容器与 59 个网页资源一致，保持该版本。另一对话最新未发布候选实际上为自主日程/日记（Companion `95894dad`、Platform `be98a630`），已向用户询问是否一并发布，尚无回复，因此没有扩大范围合入。

## 当前启用状态

NAS 既有 `automatic_memory_candidates=false` 已保留。新的持久后台整理、提议处理、回执恢复代码已经上线；运行能力接口准确返回 generation=disabled、submission=paused、extraction=paused。现有自然召回仍按原配置运行。本次不能声称自动记忆正在生产环境提炼，也没有执行真实模型/QQ聊天验收。开启问题已明确询问用户，等待选择。

记忆候选处理端口通过生产内部 TLS 和既有 Companion 服务认证验证：读取一个不存在的固定探测 job 返回 404/not_found，证明实际路由、鉴权和处理器已接通；未生成测试候选、未写入虚构用户记忆。

## 构建与上线证据

- 从固定 Git 提交打包，保留原依赖和运行用户/入口/健康检查。Memory 76 个源码文件与模块核对、路由注册及 CLI 通过；Companion 96 个归档文件、68 个模块、安装路径哈希、CLI 与 pip check 通过。候选容器无网络、无生产数据挂载。
- 两个产品已完成此前定向及跨产品 HTTPS 重启召回验证，复用未变检查；完整基线失败与跳过仍见 `memory-loop-2026-10-11.json`，不宣称全量无失败。
- 五核心 live/ready 均 200、TLS 校验开启；十容器运行、guard ready。仅 Memory、Companion 容器被替换，其他八个容器 ID 保持，Gateway origin 未重签。
- 35 个 SQLite 数据库完成冷备和离线恢复验证。冷备位于 `/volume2/tianshu-v2-resident-updates/memory-subscriptions-release-20261012-release/cold-snapshot`。
- 原角色、管理员 grants、天气、供应商与功能模型绑定、加密 key、所有 settings 文件保持。各服务合同挂载保留；Memory/Knowledge 的旧冻结合同与 Companion/Platform 的增量合同不混用。无需数据库迁移。
- 两份 Dockge 快照与实际 Compose 字节一致；59 个在线静态资源与既有 Platform 镜像逐文件 SHA256 一致，no-store。

Memory 镜像 `sha256:92b4f8a16f4882cfe5c453849d51e595dac3088ca3f5b3a321abfa3c0b98edb7`。
Companion 镜像 `sha256:10b6e5f0981aeef538f4ad9045423cf874ebf4c20b05794074efc4aaaf055bce`。

旧镜像、Compose/guard 控制文件及冷备保留；不能在服务继续写入后用旧账本覆盖新用户数据。私有执行材料保存在忽略的 `.runtime/memory-subscriptions-release-20261012/`，没有提交凭据、真实配置或聊天原文。

视频下载入库仍依赖实际媒体库目标配置（此前 targets=0；本批未更改媒体配置），未执行真实 B 站下载。入口 `http://192.168.31.210:18446/#/subscriptions`。
