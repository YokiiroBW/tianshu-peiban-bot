# NAS-01：四核心隔离运行资源适配

用户已授权继续由 Codex 实现并推进 NAS 隔离实机验证。目标 NAS 实报不支持 CFS/PID 控制器，支持 memory/cpuset。任务在独立 `codex/nas-resource-profile` 工作树实现；本卡限定部署工具和专项测试，不改四产品业务代码。

本批只放行 `nas-cpuset-qa-v1`：显式两颗逻辑 CPU、原内存上限、明确 `pid_limit=unsupported`；仅 `tianshu-qa-*`、回环网页、isolated_test TLS。默认模板与原资源限制保持。release 检查拒绝此 QA profile，不把 PID 缺失记为通过。运行前读真实 Docker 能力，容器创建后、启动前核 HostConfig；一次性容器继承同一服务资源边界，构建也使用该 CPU 集合与 2 GiB 内存参数。

新 profile 固定进入 deployment.json 的 compose_inputs 与完整性摘要；需要全新 scope/tag，禁止修改已有 a bundle 来绕过完整性。此阶段五日志组件/恢复资源适配尚未完成，configure-observability 对该 profile 明确拒绝，不允许混用未验收九服务配置。后续单独闭合日志与恢复，再谈正式 NAS release profile。

实现白名单：deploy/tianshu/resource_profile.py、configuration.py、compose.py、bundle.py、synthetic_init.py、linux_validate.py、linux_runtime.py、linux_lifecycle.py、observability_release.py；tests/deployment/packaging/test_resource_profile.py；本说明及 docs/handoffs/NAS-01.md。
