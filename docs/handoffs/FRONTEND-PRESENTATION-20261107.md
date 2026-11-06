# 前端可读文案与悬浮通知发布 — 2026-11-07

本次继续用户明确授权的前端发布，跳过测试、备份与数据库迁移，由唯一生产协调者串行执行。

- 旧线上：`4e48464dd1a09afd291724907401563b38c59b86`。
- 实际部署源码：`3b6740053737985e002e719848cfa3e302f410f9`；已推送 `github/codex/model-routing-layout-20261107` 并合入集中集成分支。
- 实际镜像：`sha256:cfd7c9dbc8b8527fad4ec306da0be9403bffa5c53faa34aad9b8b08f4a172c7e`。
- 实际容器：`2a7e9a5230193dd39c588bd550d23d60a60b551aaa4c3b17f7b2ef822a5064e0`。
- 北京时间2026-11-07 01:16:57：六角色ready，HTTP200，0重启、无OOM；容量守护healthy，容量守护与巡检服务active。

66个web文件变更（含1个随文案同步的测试断言），没有后端、依赖、数据库或API合同修改。复用humanize，增加providerPresentation/readableRecord，为普通视图提供可读名称及中文说明；NotificationProvider/Notification/Notice通过Portal统一右上角悬浮，成功通知六秒收起，失败/unknown可手动收起，宿主随当前原生modal，避免挤动布局和被顶层弹窗遮挡。现有ID、请求value、任务链接及恢复调用保留，新建供应商/路由沿用requestId自动生成内部ID。

发布由干净Git归档构建镜像，实际配置/.env/seccomp SHA256、11个挂载及其他两个容器ID/状态保持，业务数据目录保留。未运行测试、未创建备份、未迁移/清理数据库、未修改现有模型路由或重放未知效果。实际验收范围为构建与线上身份/启动状态；没有浏览器功能或视觉验收，不将启动就绪称端到端业务验收。

本地回执：`C:/YOKI/Codex/tianshu-peiban-bot/.runtime/frontend-presentation-release-20261107/final-receipt.json`。历史RBJ业务验收及403/unknown见RBJ-08，本次不改变其结论。
