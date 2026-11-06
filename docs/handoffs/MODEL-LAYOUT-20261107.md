# 模型供应商页面布局修复发布 — 2026-11-07

用户明确授权指定协调者直接部署，跳过测试和备份。

- 修复：`web/src/features/settings/models.css` 对齐实际 `.model-function` 结构，约束字段与路由操作行宽度；对应测试文件已提交但未运行。
- 实际旧线上：`8dbe3eeb386c8a982a12d078322fcc18d38d904c`。
- 实际新源码：`4e48464dd1a09afd291724907401563b38c59b86`，已推送 `codex/model-routing-layout-20261107`，并快进集中集成分支。
- 镜像：`sha256:3f5b47b74830b33b901b85347fa2f9bae73466398313e6ac7d78fcb1d47b2127`。
- 容器：`107c3c1dcbbe5b1b2db1830109cfc961aa87d42aa3f4ae925e1010df62f0e91e`，北京时间2026-11-07 00:34:52启动。
- 北京时间00:36启动状态：六角色ready，HTTP200，0重启，无OOM；容量守护healthy，守护与巡检服务active。
- 保留：配置/.env/seccomp的SHA256、11个挂载及其他两个容器ID和运行状态。数据目录保留，没有数据库迁移或清理，没有模型路由修改及未知效果重放。
- 按用户要求未运行测试、未创建备份。完成必要镜像构建与部署状态读取，没有页面视觉验收，不宣称视觉测试通过。
- 产品差异仅上述CSS及对应测试；后端、依赖与部署定义与旧线上一致。历史403/unknown及RBJ业务验收结论不因本次发布而改变。

本地发布回执：`C:/YOKI/Codex/tianshu-peiban-bot/.runtime/model-routing-release-20261107/final-receipt.json`。
