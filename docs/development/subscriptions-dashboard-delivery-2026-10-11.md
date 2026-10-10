# 订阅概览与导航本地交付

按用户提供的 Easy-vdl 参考布局调整，保留天枢浅蓝紫设计令牌、卡片、深色模式及移动端。订阅根页现在是概览；左侧订阅展开概览、视频订阅、链接下载、下载中心、账号与媒体库。移除用户标记的重复大标题和说明，保留面包屑和辅助技术标题。

复用原媒体控制器和所有业务面板；原 `/0`–`/3` 书签保留，新增 `/4` 为概览，旧资源入口转订阅管理。概览展示完整台账统计、订阅状态环图、近期五项、下载暂存所在磁盘和配置状态。SQL 聚合不受原最近100项列表限制；未知数据不显示成零，发布目标配置不冒称实际连接成功。未实现的直播、其他平台、GPU不显示为可用功能。

## 固定代码

- 集成检出：`worktrees/video-subscription-20261009/integration`，分支 `codex/video-integration-20261009`。
- 集成最终产品提交：`251a8fe2587320a4c75b6d7adda7fd61b54429a6`；后端集成提交 `877eef8`。
- 前端来源：`aec043d281366bf0fa95f1bd3854a5a27f313e72`；后端来源：`c3a7c6ddf7b9f67fab8631eb663f44f72d233f42`。开发者均沿用 gpt-6.1-sol/xhigh，隔离检出并行开发、父级串行集成。
- 详细交接：集成检出内 `docs/handoffs/subscriptions-dashboard-2026-10-10.md` 和 `docs/handoffs/subscriptions-dashboard-backend-2026-10-10.md`。任务10/10开始、10/11完成。

## 实际验证

- 后端 overview 9项、既有 media runtime 14项通过；Ruff检查通过。
- 前端媒体/导航/概览行为90项、三尺寸浅暗色/Axe/无横向溢出3项、StrictMode 1项通过；类型、格式与构建通过。
- 应用壳15通过、4个既有小屋标题断言失败、1条件跳过，已确认属于原基线问题；未扩展修改。
- 最终集成构建通过，保留既有 renderer 大包提示。
- 最终集成实际 Platform HTTP + SQLite + 浏览器：桌面/手机6项全部通过，35.5秒。覆盖原扫码、四种来源、新成员、合成发布、元数据补全，以及新概览实际响应与KPI/累计/近期任务/磁盘展示一致。B站与发布上游是隔离合成替身；不称真实账号或媒体服务器再次验收。自启HTTP夹具测试后停止。

预览：[桌面](subscriptions-dashboard-evidence-2026-10-11/desktop.png)、[手机深色](subscriptions-dashboard-evidence-2026-10-11/mobile-dark.png)。预览统计为明确合成数据，父级实际看图通过。

本轮完成本地开发、集成与验证，未合产品main、未推送、未部署NAS或使用真实B站账号。此前Emby/Jellyfin成品元数据整链结论及其边界继续见 [原交付](video-subscription-delivery-2026-10-09.md)，本次未变更下载或资产发布执行逻辑。
