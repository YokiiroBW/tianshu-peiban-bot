# OpenCode 会话修复已部署 NAS

2026-10-04，platform `6a2ed3d0d6f6b3f0be6761c3b9cf61239ea5d2b4`、gateway `d1865da927bee8e266ac34bdc674dc36c1a513a8`、companion `a5725d04aba00762d66306ae4821a40b70f7e2ed` 已部署到既有 NAS；协调合同固定为 `19bf9f0bf226a0b2029f32a93833144ee54a2be3`。入口 [http://192.168.31.210:18446](http://192.168.31.210:18446)。

原短测试上游返回 HTTP 400 `MissingSessionID`，调用缺少 OpenCode 要求的会话上下文，页面此前只显示无法访问上游。本批修复三产品调用上下文，加入 OpenCode Zen / Go 预设，并为 `provider-self-service/v1` 增加向后兼容的可选 `test.error_code`。发布仅替换三产品镜像，保留已有数据库、设置和其他产品镜像。

172 targeted checks passed (Gateway 53, Companion 88, Platform catalog 21, joint 4, contract 1, browser 5); lint, TypeScript and production build passed.

NAS 发布回执确认 10 个服务、五核心 live/ready 均为 200 且验证 TLS，guard ready；53 个 HTTP 网页资源与候选匹配，Dockge 已同步三产品镜像。更新前备份为 `/volume2/tianshu-v2-resident-updates/opencode-session-20261004-core/production-generation-b24f4f7c5ad1478b8baca58cd960e0fd/cold-snapshot`，配置与用户状态保留，验证克隆已停止。

部署后独立真实短测试通过：一次外部调用，HTTP 200，原适配器回复验真为 true。 去敏回执：`{"attempts": 1, "original_test_reply": true, "http_status": 200, "reply_verified": true}`

发布过程的纯只读探针没有实例化 Platform 或调用真实模型；上面的独立真实短测试是部署后另一次协调调用，两种事实分别记录。真实短测试直接复用适配器及已有供应商配置，不等于原账号网页登录、网页供应商管理或 Companion→Memory 全对话验收。没有伪造网页会话。精确镜像、验证结果和回执哈希见同名 JSON。
