# TS-318：AL08 内容发现

用户已明确授权本地继续开发。记忆扩展协调方在 `autonomous-life-boundary-2026-10-26.json` 的 `partial_release` 释放本卡不重叠路径。实际基线为 Platform `3e2cc412c676450a90dc0cbd3c3387d1c945e5e2`，独立分支及 worktree；不写 TS311、Memory、Companion。执行者为同聊天子代理；根记录、任务登记及最终集成由主协调者单写。

## 范围与实现

实现内置 `content-discovery` 插件，复用原实例配置、Vault、http.client、state.kv、调用回执及角色绑定。新增窄只读 `content.discovery` Host 端口，由真正注入的 provider 决定可用性，不能宿主没 handler 却向调用者宣布可用。保持原五个端口默认行为。

真实搜索协议先适配 SearXNG JSON：可信实例配置服务地址和 Vault secret，模型只提交查询，不选服务地址或凭据；无后端如实 unavailable，并可返回本地来源。检索片段不冒充正文，搜索结果不伪造 Knowledge 正文 hash。

媒体提供者投影复用原 owner 的只读目录接口：B站 member/part、音乐 recording/source、书籍与漫画 catalog/version。稳定 owner 标识去重，URL 仅规范无语义差异部分；标题相同不能证明转载。不得下载、格式探测、扫描订阅、改变用户进度或返回本机路径/账号信息。来源失效独立返回；私有音乐范围必须来自可信配置。原 owner 缺必要只读接口时先向协调者报告，不能越出白名单改 media。

候选包含真实来源、稳定定位和版本、标题及有据作者、发现/发布时间、摘要类型与实际可读性。可按调用者传入的已知排除键过滤；选择/跳过状态复用 plugin_state，但不得另建已读记忆账本，也不能把命中/选择说成已观看。

## 路径与分工

本卡当前可写：

- `services/platform/content_discovery/`
- `services/platform/plugins/builtin/content-discovery/`
- `services/platform/plugins/manager.py`
- `services/platform/plugins/ports.py`
- `services/platform/plugins/port_schemas.py`
- `services/platform/plugins/execution.py`
- `services/platform/plugins/actor_bridge.py`：TS316 最终交付后已释放，只替换原静态能力集合为实际注入集合。
- `services/platform/service.py`：TS316 最终交付后已释放，只装配原 Media owner 的 Discovery provider。
- `services/platform/media/repository.py`：协调方追加释放；仅原 documents/member 表的有界只读 keyset 分页，不改 DDL、下载、账号或管理路径。
- `tests/backend/test_content_discovery*.py`
- 新增 `docs/platform/content-discovery.md`、`docs/handoffs/TS-318.md`

2026-10-27 更新：TS316 已以同一 clean SHA `3e2cc412c676450a90dc0cbd3c3387d1c945e5e2` 最终本地交付，边界记录的 assembly 已明确释放 TS318 在自己 worktree 内串行装配。故本卡追加以上两个精确路径，完成实际服务注入及原角色桥消费；不用换基线或再合 TS310。`server.py`、`actor_capabilities/` 及其他原边界路径仍不在本卡，无需修改。不能绕开入口新建转发层，不改正在使用的候选合同。实现依赖已验 TS310；集成依赖已交付 TS316。

可读原 owner 接口和已提交源码，不改另一任务 worktree、虚拟环境或测试产物。测试仅本卡私有临时 SQLite/loopback HTTP；现完整解释器只读使用，设置 `PYTHONDONTWRITEBYTECODE=1`，不连接共享 PG、真实账号/模型/QQ，不消费其他批模型预算。

## 集中验证与交付

完整功能块稳定后先检查完整 diff，再执行相关检查：真实插件子进程搜索与目录调用、无 provider 不宣告能力、来源失败独立回退、私有范围及跨角色、URL/身份去重、版本/排除键、选择与已读区分、原回执重放与重启、去敏及响应预算。变更原 plugins 公共能力应覆盖相关旧调用方；未变成功结果不重跑。未接服务装配前不得宣称 AL07→实际服务整链或 AL08 完成。

本卡现需在真实 Platform 实例经原 AL07 角色桥验证实际注入、角色绑定与调用、旧回执重放和跨角色拒绝。无需改用户记忆入口，也不调用真实模型。按精确路径提交本卡产品代码与交接，记录基线、命令、通过/失败/跳过/未覆盖，主协调者独立审查并安排最终集成。局部与实际搜索/媒体接通分别记。本批不推送、部署、生产迁移或操作真实设备；用户对话记忆逻辑保持不变。
