# TS-322：联合发布前 Chat 主线集成

用户已授权自主生活完成后连同记忆扩展一起推送、部署。只读远端核对发现 Chat GitHub main
`21e09fe6f3824b2f75b62baedccd0baabed00a2e` 与 TS315 候选
`1c2ee12c42d466bd9b48d1dd67d491429753f395` 分叉，共同祖先
`17206ad54eff41954f6cc7fcb48238560a00da33`。不能强推覆盖任一侧。

由联合发布协调者在独立 `TS-322/chat-audit-core` worktree、
`codex/ts-322-chat-release-integration` 分支从 TS315 精确提交集成远端完整历史。
TS315 及其旧检出保持不动，不把 root 混合改动纳入提交。

线上只读进一步确认 Chat 运行已发布 `release/2026.10.03-quality-life` 的
`66097a98e8613437f96a76ddae4e23bb7e1fd609`。这是 orphan 发布快照，与候选无共同祖先；
不能用 GitHub main 代替现网基线。其已上线 source row 优先、platform message alias
与禁止正文/秒级去重的修复必须完整保留。

范围为本次三提交及真实生产差异：`README.md`、`app/services/message_service.py`、
`app/services/import_service.py`、`tests/test_message_service.py`、`tests/test_message_identity.py`、
`docs/handoffs/DQ-main-integration.md`，另加
`docs/handoffs/TS-322.md`。只解决分叉冲突及相应回归，不扩展归档、采集器或备份功能。
保留新归档候选和本地已完成能力；上述两个运行模块按实际 66097 完整保留并核 blob，
不把当前候选的旧 semantic 匹配重新发布。远端 legacy 修复所要保证的未标识重复投递保留、
群聊/私聊 ID 隔离行为须在新实现中得到有效验证，不能将旧哈希算法覆写到新统一身份。

先审查实际 merge diff，再运行消息写入与受影响归档读取检查。复用 TS315 未变化部分结果；
如冲突只改变测试/文档而生产源码未变，明确记录。实际运行主机、数据和发布来源另由只读
盘点核定；本卡不操作生产、不推送。合并提交须同时包含双方 SHA，清晰记录精确提交、
验证及未覆盖，再交统一发布流程正常推送。
