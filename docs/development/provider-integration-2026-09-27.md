# 供应商自助配置本地集成

2026-09-27：固定候选经独立复核后已串行合入本地协调检出，尚未部署 NAS。

- 平台 main：1d51d888ff77594a1f6fbb1c7e0d4fdf92285ff8，合并后端 5ead375 与网页 3072ec6。
- 网关 main：f93b08a6ce2d31774d2f1b27dc00fd4d5f3ee26d。
- 陪伴 main：31677983798ba27b24d57925feab4774c2eec30f。
- 根合同与联合测试：578908b；独立报告已导入至 bdc6d7b。

总控复跑 `TIANSHU_WORKSPACE=<协调根> python -m unittest discover -s tests/provider_self_service -v`，6/6通过。此测试仍从 PROVIDER 工作区加载已验固定后端；已确认平台 main 后端范围与5ead375逐文件相同，apps/web与3072ec6相同，网关/陪伴main为上述固定SHA。合并无冲突。平台原有未跟踪build/保留；根既有其他修改未纳入。

独立复验关闭C1动态source-sync去重/collection追加阻断：source_sync 22与model_selection 11通过，报告见 ../reviews/provider-backend-fix-review-2026-09-27.md。网页7/7为执行者隔离浏览器报告，包含明确标记的错误注入；不等于NAS验收。

后续部署必须完成Linux镜像构建、原现场备份、私有供应商目录显式初始化、服务身份与内部TLS接线、容量guard受控更新以及网页到完整Companion回复验收。不得重跑旧首装或覆盖现有管理员。当前线上保持此前首次使用修复版，未启用本功能。
