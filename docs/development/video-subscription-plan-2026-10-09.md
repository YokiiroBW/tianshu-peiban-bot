# 视频订阅完整接入执行计划

2026-10-09 用户授权：规划并实现视频订阅前后端及天枢系统接入，使用 gpt-6.1-sol / xhigh 并行开发。本计划覆盖旧媒体队列的 DSH 执行限制与尚未派发限制，不恢复旧自动化、不新建聊天。当前长期目标已建立。

## 范围与完成标准

沿用现有 B 站首期架构，实现账号、解析与质量选择、四类来源订阅、规则试算、完整基线与增量扫描、持久队列、隔离下载及校验、元数据、AssetLibrary 发布、媒体服务器核对、原生网页与任务中心。可选五平台扩展询问未收到补充，已明确按现有 B站首期完成，不将尚未实现平台标为可用。

复用 TS090 元数据/NFO、TS098 规则、TS099 成品预检、现有 Cookie/CSRF、权限与任务投影。Platform 唯一拥有订阅与作业事实，AssetLibrary 唯一写最终库。公共错误可解释；缺配置和真实外部验证未执行需如实显示。

验收包含后端状态恢复与失败路径、真实本地 HTTP 联合、前端桌面及窄屏交互、账户切换/迟到响应隔离。合成上游、真实本地服务与实际外部账号分开记录。最终集成检出可运行；未经本次明确请求不推送、部署 NAS、迁移生产或使用真实账号。

## 基线与隔离

根 projects/platform 的 dffedb2 已落后部署版本且有未跟踪 build/。采用现有干净 worktrees/quality-life-20261003/platform 固定提交 6a2ed3d0d6f6b3f0be6761c3b9cf61239ea5d2b4，包含已部署 c1d717c 与后续本地变更；并不声称全部基线已部署。AssetLibrary 使用同批干净 54b2beb39568d8ccf954688e24dd18ab868d014c。

已执行 workspace.py check，其失败源为历史 TS016 工作树 Git 所有权，不是本次代码失败。现有分配器绑定旧 projects HEAD、只支持旧卡且拒绝脏检出；本次采用直接固定 SHA 的 git worktree add，不清理原修改，不改全局 safe.directory，不为恢复旧分配器扩大任务。

## 写入分工

三个子智能体均 gpt-6.1-sol / xhigh；目录 worktrees/video-subscription-20261009/ 下各独立分支。

| 工作 | 检出 | 独占范围 |
| --- | --- | --- |
| 后端 | backend | services/platform/media、必要 service/server/web_console/__main__/tasks 装配、后端媒体专项测试、媒体 API 文档 |
| 前端 | frontend | apps/web/src/features/media、必要 App/modules/TasksPanel 装配、媒体浏览器测试、网页文档 |
| 资产发布 | assetlibrary | 成品发布领域与持久层、必要 Host 注册/API/索引与权限接入、对应测试/迁移/交接 |

父级协调唯一拥有根合同、计划、依赖及锁与最终串行集成；同产品按前后端不相交文件划分写者，覆盖旧“同产品只一写者”，公共入口仍单写。工作者不得互写检出或根合同，公共 DTO 先固定再接线。

下载引擎选公开 yt-dlp 独立进程，官方已核版本 2026.08.19；B站账号和四类来源枚举由天枢适配器管理，不引入第二订阅事实源。依赖与容器配置由父级收口，版本能力通过隔离样例验证，真实账号验证另记。

## 当前状态

前后端及资产发布已完成本地实现与集成验证，固定版本、复用位置、实际通过/失败/跳过和未覆盖边界见 [交付记录](video-subscription-delivery-2026-10-09.md)。真实本地Platform→AssetLibrary→Emby/Jellyfin两P整链completed，最终集成桌面/手机浏览器4/4。没有推送、产品main合入、NAS部署或真实个人账号操作。
